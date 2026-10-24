"""Read-only Gmail OAuth and bounded imports. Tokens never leave server memory."""
import base64
import hashlib
import json
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.inbox import normalize

SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
API_URL = 'https://gmail.googleapis.com/gmail/v1/users/me/'


class GmailError(ValueError):
    pass


def google_request(url, form=None, token=None):
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = None
    if form is not None:
        data = urlencode(form).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    try:
        with urlopen(Request(url, data=data, headers=headers), timeout=30) as response:
            body = response.read(10 * 1024 * 1024 + 1)
        if len(body) > 10 * 1024 * 1024:
            raise GmailError('A Gmail response was too large to process.')
        return json.loads(body) if body else {}
    except HTTPError as error:
        # Never include request URLs, token payloads, or Google's raw errors in logs/UI.
        messages = {400: 'Google could not complete this request. Reconnect Gmail and try again.',
                    401: 'Gmail authorization expired or was revoked. Reconnect Gmail.',
                    403: 'Google denied access. Check that Gmail API is enabled and your account is an OAuth test user.',
                    429: 'Google rate limit reached. Wait a moment and retry.'}
        raise GmailError(messages.get(error.code, 'Google is temporarily unavailable. Please retry.')) from None
    except (URLError, TimeoutError, OSError):
        raise GmailError('Could not reach Google. Check your internet connection and retry.') from None
    except (ValueError, UnicodeDecodeError):
        raise GmailError('Google returned an unreadable response. Please retry.') from None


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'head'):
            self.hidden += 1
        if tag in ('p', 'br', 'div', 'li', 'tr'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'head') and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def body_text(payload):
    """Prefer plain text alternatives and ignore attachments, including .eml files."""
    if payload.get('filename'):
        return ''
    mime = payload.get('mimeType', '')
    if mime.startswith('multipart/'):
        parts = payload.get('parts', [])
        if mime == 'multipart/alternative':
            parts = sorted(parts, key=lambda p: p.get('mimeType') != 'text/plain')
            for part in parts:
                text = body_text(part)
                if text.strip():
                    return text
            return ''
        return '\n'.join(filter(None, (body_text(part) for part in parts)))
    if mime not in ('text/plain', 'text/html'):
        return ''
    data = payload.get('body', {}).get('data', '')
    if not data:
        return ''
    if len(data) > 1_000_000:
        raise ValueError('Message body is too large.')
    decoded = base64.urlsafe_b64decode(data + '=' * (-len(data) % 4))
    content_type = next((h['value'] for h in payload.get('headers', []) if h['name'].lower() == 'content-type'), '')
    charset = re.search(r'charset=["\x27]?([^;\s"\x27]+)', content_type, re.I)
    try:
        text = decoded.decode(charset.group(1) if charset else 'utf-8', errors='replace')
    except LookupError:
        text = decoded.decode('utf-8', errors='replace')
    if mime == 'text/html':
        parser = TextExtractor()
        parser.feed(text)
        text = ''.join(parser.parts)
    return text.strip()


def job_related(subject, body):
    """Conservative triage before the existing five-way assessment."""
    subject = subject.lower()
    text = subject + '\n' + body.lower()
    # Digests are opportunities to browse, not applications or personal offers.
    if re.search(r'\b(?:job alerts?|jobs? for you|recommended jobs|daily jobs|weekly jobs|new jobs|jobs? digest|career newsletter)\b', subject):
        return False
    return bool(re.search(
        r'\b(?:job offer|employment offer|offer of employment|offer letter|training fee|'
        r'application (?:received|confirmation|status|update|for)|your application|'
        r'thank(?:s| you) for applying|interview|technical round|screening call|'
        r'candidate|recruiter|recruiting|hiring|onboarding|position.{0,30}filled|'
        r'pleased to offer you|selected for (?:the |this )?(?:role|position))\b', text))


def gmail_email(message, account):
    payload = message.get('payload', {})
    headers = {h['name'].lower(): h['value'] for h in payload.get('headers', [])}
    subject = headers.get('subject', '(No subject)')
    body = body_text(payload)
    if not body:
        raise ValueError('No supported message body.')
    if not job_related(subject, body):
        return None
    account_key = hashlib.sha256(account.casefold().encode()).hexdigest()[:20]
    # internalDate is Gmail's receipt time; do not trust arbitrary sender Date headers.
    received = datetime.fromtimestamp(int(message['internalDate']) / 1000, timezone.utc).isoformat()
    result = normalize({'from': headers.get('from', ''), 'subject': subject, 'body': body,
                        'date': received, 'message_id': headers.get('message-id', ''),
                        'application_id': f'gmail:{account_key}:{message["threadId"]}',
                        'in_reply_to': headers.get('in-reply-to', ''), 'references': headers.get('references', '')})
    result.update({'source': 'gmail', 'gmail_id': message['id']})
    return result


class GmailConnection:
    def __init__(self, credentials_path):
        self.credentials_path = Path(credentials_path)
        self.lock = threading.RLock()
        self.pending = None
        self.tokens = None
        self.account = None
        self.cursor = None
        self.window = None
        self.last_sync = None
        self.syncing = False
        self.last_error = None

    def _client(self):
        try:
            configuration = json.loads(self.credentials_path.read_text(encoding='utf-8-sig'))
            client = configuration.get('installed') or configuration.get('web')
            if not isinstance(client, dict) or not client.get('client_id') or not client.get('client_secret'):
                raise ValueError()
            return client
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise GmailError('Add a Google Desktop app or Web application OAuth file at config/gmail_credentials.json. Open Gmail setup for instructions.') from None

    def status(self):
        # Avoid waiting behind a potentially slow network scan just to render status.
        try:
            self._client()
            configured = True
        except GmailError:
            configured = False
        return {'configured': configured, 'connected': bool(self.tokens and self.account),
                'account': self.account, 'last_sync': self.last_sync, 'has_more': bool(self.cursor),
                'days': self.window or 90, 'syncing': self.syncing, 'last_error': self.last_error}

    def begin(self, redirect_uri):
        with self.lock:
            if self.tokens:
                raise GmailError('Disconnect the current Gmail account before connecting another.')
            client = self._client()
            self.last_error = None
            state, binding, verifier = (secrets.token_urlsafe(32) for _ in range(3))
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
            self.pending = {'state': state, 'binding': binding, 'verifier': verifier,
                            'redirect_uri': redirect_uri, 'created': time.monotonic()}
            params = {'client_id': client['client_id'], 'redirect_uri': redirect_uri, 'response_type': 'code',
                      'scope': SCOPE, 'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256',
                      'access_type': 'offline', 'prompt': 'consent select_account'}
            return AUTH_URL + '?' + urlencode(params), binding

    def complete(self, state, binding, code, denied=False):
        with self.lock:
            pending = self.pending
            if (not pending or time.monotonic() - pending['created'] > 600
                    or not secrets.compare_digest(state.encode(), pending['state'].encode())
                    or not secrets.compare_digest(binding.encode(), pending['binding'].encode())):
                raise GmailError('Gmail connection expired or could not be verified. Start again from Connect Gmail.')
            self.pending = None  # Codes and state can be used only once, including cancellations.
            if denied or not code:
                raise GmailError('Gmail permission was not granted. You can still import files.')
            client = self._client()
            tokens = google_request(TOKEN_URL, form={'client_id': client['client_id'],
                'client_secret': client['client_secret'], 'code': code, 'code_verifier': pending['verifier'],
                'redirect_uri': pending['redirect_uri'], 'grant_type': 'authorization_code'})
            if SCOPE not in tokens.get('scope', '').split() or not tokens.get('access_token'):
                raise GmailError('Read-only Gmail access was not granted. Connect again and allow that permission.')
            profile = google_request(API_URL + 'profile', token=tokens['access_token'])
            if not profile.get('emailAddress'):
                raise GmailError('Could not identify the connected Gmail account.')
            tokens['expires_at'] = time.time() + int(tokens.get('expires_in', 3600))
            self.tokens, self.account = tokens, profile['emailAddress']
            self.cursor = self.window = self.last_sync = None

    def _access_token(self):
        if not self.tokens:
            raise GmailError('Connect Gmail before scanning messages.')
        if self.tokens['expires_at'] <= time.time() + 60:
            if not self.tokens.get('refresh_token'):
                raise GmailError('Gmail session expired. Disconnect and reconnect Gmail.')
            client = self._client()
            updated = google_request(TOKEN_URL, form={'client_id': client['client_id'],
                'client_secret': client['client_secret'], 'refresh_token': self.tokens['refresh_token'],
                'grant_type': 'refresh_token'})
            if not updated.get('access_token'):
                raise GmailError('Gmail session expired. Disconnect and reconnect Gmail.')
            self.tokens.update(updated)
            self.tokens['expires_at'] = time.time() + int(updated.get('expires_in', 3600))
        return self.tokens['access_token']

    def sync(self, store, days=90):
        if type(days) is not int or days not in (30, 90, 180):
            raise GmailError('Choose a 30, 90 or 180 day scan window.')
        if not self.lock.acquire(blocking=False):
            raise GmailError('Another Gmail operation is running. Please wait.')
        self.syncing = True
        try:
            token = self._access_token()
            query = (f'newer_than:{days}d -in:sent -in:drafts '
                     '{application interview "job offer" "offer letter" "employment offer" '
                     'recruiter recruiting hiring candidate onboarding "training fee" "technical round"}')
            params = {'q': query, 'maxResults': 100}
            cursor = self.cursor if days == self.window else None
            if cursor:
                params['pageToken'] = cursor
            listing = google_request(API_URL + 'messages?' + urlencode(params), token=token)
            emails, ignored, unreadable = [], 0, 0
            candidates = listing.get('messages', [])
            for item in candidates:
                message = google_request(API_URL + 'messages/' + item['id'] + '?format=full', token=self._access_token())
                if any(label in message.get('labelIds', []) for label in ('SENT', 'DRAFT', 'SPAM', 'TRASH')):
                    ignored += 1
                    continue
                try:
                    email = gmail_email(message, self.account)
                except (ValueError, KeyError, TypeError, OverflowError, OSError):
                    unreadable += 1
                    continue
                if email:
                    emails.append(email)
                else:
                    ignored += 1
            # Keep cursor unchanged on API/storage failure; retrying is idempotent.
            result = store.import_emails(emails)
            self.window = days
            self.cursor = listing.get('nextPageToken')
            self.last_sync = datetime.now(timezone.utc).isoformat()
            imported_ids = {e['id'] for e in emails}
            counts = {label: 0 for label in ('applied', 'interview', 'rejected', 'offer', 'scam')}
            for app in store.snapshot()['applications']:
                for email in app['history']:
                    if email['id'] in imported_ids:
                        counts[email['label']] += 1
            return {**result, 'scanned': len(candidates), 'job_related': len(emails), 'ignored': ignored,
                    'unreadable': unreadable, 'has_more': bool(self.cursor), 'assessment': counts}
        finally:
            self.syncing = False
            self.lock.release()

    def disconnect(self):
        with self.lock:
            tokens = self.tokens
            self.tokens = self.account = self.pending = self.cursor = self.window = self.last_sync = None
            revoked = True
            if tokens:
                try:
                    google_request('https://oauth2.googleapis.com/revoke',
                                   form={'token': tokens.get('refresh_token') or tokens['access_token']})
                except GmailError:
                    revoked = False
            return {'ok': True, 'revoked': revoked}
