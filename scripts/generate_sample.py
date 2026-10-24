"""Regenerate both demo formats with current relative dates."""
import json
import mailbox
import sys
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.demo import demo_inbox


if __name__ == '__main__':
    sample = demo_inbox()
    (ROOT / 'data' / 'sample_inbox.json').write_text(json.dumps(json.loads(sample), indent=2), encoding='utf-8')
    target = ROOT / 'data' / 'sample_inbox.mbox'
    inbox = mailbox.mbox(str(target))
    try:
        inbox.lock()
        inbox.clear()
        for raw in json.loads(sample)['emails']:
            message = EmailMessage()
            for header, field in [('From','from'), ('Subject','subject'), ('Message-ID','message_id'),
                                   ('X-Application-ID','application_id'), ('X-Company','company'), ('X-Role','role')]:
                message[header] = raw[field]
            from datetime import datetime
            message['Date'] = datetime.fromisoformat(raw['date'])
            message.set_content(raw['body'])
            inbox.add(message)
        inbox.flush()
    finally:
        inbox.close()
    print('Generated data/sample_inbox.json and data/sample_inbox.mbox (20 emails each).')
