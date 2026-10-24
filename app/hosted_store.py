"""Bounded, JSON-serializable tracker for encrypted hosted workspaces."""
import copy
import hashlib
import re
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr

from app.classifier import classify

MAX_EMAILS = 1000


class HostedStore:
    def __init__(self, state, model=None):
        self.state = state
        self.model = model
        self.state.setdefault('applications', {})
        self.state.setdefault('emails', {})

    def import_emails(self, emails):
        # Stage before replacing state so a classifier failure never leaves half an import.
        apps = copy.deepcopy(self.state['applications'])
        records = dict(self.state['emails'])
        added = skipped = 0
        for email in sorted(emails, key=lambda e: (e['date'], e['id'])):
            if email['id'] in records:
                skipped += 1
                continue
            if len(records) >= MAX_EMAILS:
                raise ValueError('This demo workspace supports up to 1,000 emails. Export it before starting a new workspace.')
            company = email['company'] or parseaddr(email['sender'])[1].split('@')[-1].lower()
            role = email['role'] or re.sub(r'^(?:(?:re|fw|fwd):\s*)+', '', email['subject'], flags=re.I)
            if email['application_id']:
                key = 'explicit:' + email['application_id']
            else:
                key = ''
                references = re.findall(r'<[^>]+>', email['references'] + ' ' + email['in_reply_to'])
                for reference in reversed(references):
                    parent = next((r for r in records.values() if r['message_id'] == reference), None)
                    if parent:
                        key = parent['application_id']
                        break
                if not key:
                    group = (company.casefold().strip() + '|' + role.casefold().strip()) if email['company'] and email['role'] else email['id']
                    key = 'derived:' + hashlib.sha256(group.encode()).hexdigest()[:24]
            apps.setdefault(key, {'id':key, 'company':company, 'role':role, 'followup_at':None})
            records[email['id']] = {**email, **classify(email, self.model), 'application_id':key}
            added += 1
        self.state.update({'applications':apps, 'emails':records})
        return {'added':added, 'skipped':skipped}

    def snapshot(self, now=None):
        now = now or datetime.now(timezone.utc)
        applications = []
        for row in self.state['applications'].values():
            history = sorted((e for e in self.state['emails'].values() if e['application_id'] == row['id']),
                             key=lambda e: (e['date'], e['id']))
            latest = history[-1]
            flagged = [e for e in history if e['label'] == 'scam']
            status = 'scam' if flagged else latest['label']
            activity = max(latest['date'], row['followup_at'] or latest['date'])
            due_at = datetime.fromisoformat(activity) + timedelta(days=7)
            due = status in ('applied', 'interview') and due_at <= now
            applications.append({**row, 'status':status, 'last_activity':latest['date'],
                'email_count':len(history), 'history':history, 'reasons':[r for e in flagged for r in e['reasons']],
                'followup_due':due, 'followup_due_at':due_at.isoformat() if status in ('applied','interview') else None,
                'days_waiting':max(0, (now - datetime.fromisoformat(activity)).days)})
        applications.sort(key=lambda a:a['last_activity'], reverse=True)
        return {'applications':applications, 'email_count':len(self.state['emails']),
                'counts':{label:sum(a['status'] == label for a in applications) for label in ('applied','interview','rejected','offer','scam')},
                'followup_count':sum(a['followup_due'] for a in applications), 'as_of':now.isoformat()}

    def log_followup(self, application_id):
        app = next((a for a in self.snapshot()['applications'] if a['id'] == application_id), None)
        if app is None:
            raise ValueError('Application not found.')
        if not app['followup_due']:
            raise ValueError('A follow-up is not currently due for this application.')
        self.state['applications'][application_id]['followup_at'] = datetime.now(timezone.utc).isoformat()
