import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path

from app.classifier import classify


class Store:
    def __init__(self, path, model=None):
        self.path = str(path)
        self.model = model
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS applications (
                    id TEXT PRIMARY KEY, company TEXT NOT NULL, role TEXT NOT NULL, followup_at TEXT
                );
                CREATE TABLE IF NOT EXISTS emails (
                    id TEXT PRIMARY KEY, application_id TEXT NOT NULL REFERENCES applications(id),
                    message_id TEXT, date TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS email_application ON emails(application_id);
                CREATE INDEX IF NOT EXISTS email_message ON emails(message_id);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def import_emails(self, emails):
        added = skipped = 0
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Ensure parents are available before dated replies even in reversed exports.
            for email in sorted(emails, key=lambda item: (item['date'], item['id'])):
                if db.execute('SELECT 1 FROM emails WHERE id=?', (email['id'],)).fetchone():
                    skipped += 1
                    continue
                sender_domain = parseaddr(email['sender'])[1].split('@')[-1].lower()
                company = email['company'] or sender_domain
                role = email['role'] or re.sub(r'^(?:(?:re|fw|fwd):\s*)+', '', email['subject'], flags=re.I)
                if email['application_id']:
                    key = 'explicit:' + email['application_id']
                else:
                    key = ''
                    references = re.findall(r'<[^>]+>', email['references'] + ' ' + email['in_reply_to'])
                    for reference in reversed(references):
                        parent = db.execute('SELECT application_id FROM emails WHERE message_id=? LIMIT 1', (reference,)).fetchone()
                        if parent:
                            key = parent[0]
                            break
                    if not key:
                        group = (company.casefold().strip() + '|' + role.casefold().strip()) if email['company'] and email['role'] else email['id']
                        key = 'derived:' + hashlib.sha256(group.encode()).hexdigest()[:24]
                db.execute('INSERT OR IGNORE INTO applications(id,company,role) VALUES(?,?,?)', (key, company, role))
                record = {**email, **classify(email, self.model), 'application_id': key}
                db.execute('INSERT INTO emails VALUES(?,?,?,?,?)',
                           (email['id'], key, email['message_id'], email['date'], json.dumps(record)))
                added += 1
        return {'added': added, 'skipped': skipped}

    def snapshot(self, now=None):
        now = now or datetime.now(timezone.utc)
        applications, all_emails = [], []
        with self.connect() as db:
            rows = db.execute('SELECT * FROM applications').fetchall()
            for row in rows:
                history = [json.loads(e[0]) for e in db.execute('SELECT payload FROM emails WHERE application_id=? ORDER BY date,id', (row['id'],))]
                latest = history[-1]
                flagged = [e for e in history if e['label'] == 'scam']
                status = 'scam' if flagged else latest['label']
                activity = max(latest['date'], row['followup_at'] or latest['date'])
                due_at = datetime.fromisoformat(activity) + timedelta(days=7)
                due = status in ('applied', 'interview') and due_at <= now
                applications.append({**dict(row), 'status': status, 'last_activity': latest['date'],
                    'email_count': len(history), 'history': history, 'reasons': [r for e in flagged for r in e['reasons']],
                    'followup_due': due, 'followup_due_at': due_at.isoformat() if status in ('applied', 'interview') else None,
                    'days_waiting': max(0, (now - datetime.fromisoformat(activity)).days)})
                all_emails.extend(history)
        applications.sort(key=lambda item: item['last_activity'], reverse=True)
        return {'applications': applications, 'email_count': len(all_emails),
                'counts': {label: sum(a['status'] == label for a in applications) for label in ('applied', 'interview', 'rejected', 'offer', 'scam')},
                'followup_count': sum(a['followup_due'] for a in applications), 'as_of': now.isoformat()}

    def log_followup(self, application_id):
        app = next((a for a in self.snapshot()['applications'] if a['id'] == application_id), None)
        if app is None:
            raise ValueError('Application not found.')
        if not app['followup_due']:
            raise ValueError('A follow-up is not currently due for this application.')
        with self.connect() as db:
            db.execute('UPDATE applications SET followup_at=? WHERE id=?',
                       (datetime.now(timezone.utc).isoformat(), application_id))
