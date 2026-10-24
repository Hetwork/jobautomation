import hashlib
import json
import re
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime, parseaddr
from html import unescape

MAX_EMAILS = 1000


def timestamp(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Every email needs a valid date.')
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
        except (ValueError, TypeError):
            raise ValueError(f'Invalid email date: {value[:80]}') from None
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return date.astimezone(timezone.utc).isoformat()


def normalize(raw):
    if not isinstance(raw, dict):
        raise ValueError('Each email must be a JSON object.')
    email = {}
    for field in ('subject', 'body', 'company', 'role', 'application_id', 'message_id', 'in_reply_to', 'references'):
        value = raw.get(field, '')
        if not isinstance(value, str):
            raise ValueError(f'{field} must be text.')
        email[field] = value.strip()
    sender = raw.get('from', raw.get('sender', ''))
    if not isinstance(sender, str) or not parseaddr(sender)[1] or '@' not in parseaddr(sender)[1]:
        raise ValueError('Every email needs a valid from/sender email address.')
    if not email['subject'] or not email['body']:
        raise ValueError('Every email needs a non-empty subject and body.')
    email['sender'] = sender.strip()
    email['date'] = timestamp(raw.get('date'))
    if len(email['body']) > 200_000:
        raise ValueError('An email body exceeds the 200 KB limit.')
    # Content identity prevents a reused Message-ID from discarding a different email.
    identity = [email[k] for k in ('sender', 'date', 'subject', 'body')]
    email['id'] = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    return email


def parse_inbox(data, filename):
    if filename.lower().endswith('.json'):
        try:
            raw = json.loads(data.decode('utf-8-sig'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('Invalid JSON. Upload a UTF-8 JSON array or an object with an emails array.') from None
        if isinstance(raw, dict):
            raw = raw.get('emails')
        if not isinstance(raw, list):
            raise ValueError('JSON must contain an array of emails.')
    elif filename.lower().endswith('.mbox'):
        raw = []
        # mbox envelope separators begin with "From "; escaped body lines begin ">From ".
        chunks = re.split(br'(?m)^From [^\r\n]*\r?\n', data)
        if len(chunks) < 2:
            raise ValueError('Invalid mbox: no From envelope separators found.')
        for chunk in chunks[1:]:
            message = BytesParser(policy=policy.default).parsebytes(chunk)
            part = message.get_body(preferencelist=('plain', 'html')) if message.is_multipart() else message
            if part is None or part.get_content_maintype() != 'text':
                raise ValueError('An mbox email has no readable text body.')
            body = part.get_content()
            if part.get_content_type() == 'text/html':
                body = unescape(re.sub('<[^>]+>', ' ', body))
            raw.append({'subject': str(message.get('Subject', '')), 'from': str(message.get('From', '')),
                        'date': str(message.get('Date', '')), 'body': body.replace('\n>From ', '\nFrom '),
                        'message_id': str(message.get('Message-ID', '')), 'in_reply_to': str(message.get('In-Reply-To', '')),
                        'references': str(message.get('References', '')),
                        'company': str(message.get('X-Company', '')), 'role': str(message.get('X-Role', '')),
                        'application_id': str(message.get('X-Application-ID', ''))})
    else:
        raise ValueError('Choose a .json or .mbox file.')
    if not raw or len(raw) > MAX_EMAILS:
        raise ValueError(f'Import between 1 and {MAX_EMAILS} emails at a time.')
    result = []
    for index, email in enumerate(raw, 1):
        try:
            result.append(normalize(email))
        except ValueError as error:
            raise ValueError(f'Email {index}: {error}') from None
    return result
