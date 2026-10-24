import base64
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from app.gmail import GmailConnection, GmailError, SCOPE, body_text, gmail_email, job_related
from app.store import Store


def payload(text, mime='text/plain'):
    return {'mimeType': mime, 'body': {'data': base64.urlsafe_b64encode(text.encode()).decode().rstrip('=')}}


def message(id='one', subject='Your application', body='Thank you for applying. Your application was received.', **kwargs):
    content = payload(body)
    content['headers'] = [{'name':'From','value':'Hiring <jobs@example.com>'}, {'name':'Subject','value':subject},
                          {'name':'Message-ID','value':f'<{id}@example.com>'}]
    return {'id':id, 'threadId':'thread1', 'internalDate':'1792843200000', 'payload':content, **kwargs}


class GmailParsingTests(unittest.TestCase):
    def test_job_relevance(self):
        for subject, body in [('Your application', 'Thank you for applying'), ('Interview invitation', 'Tuesday at 10'),
                              ('Immediate job offer', 'Pay a training fee')]:
            self.assertTrue(job_related(subject, body))
        for subject, body in [('Your receipt', 'Thanks for shopping'), ('Daily job alerts', 'Interview tips and 20 jobs')]:
            self.assertFalse(job_related(subject, body))

    def test_html_body_strips_scripts(self):
        self.assertEqual(body_text(payload('<head><style>bad</style></head><p>Interview</p><script>secret()</script>', 'text/html')).strip(), 'Interview')

    def test_plain_alternative_preferred_attachments_ignored(self):
        alternative = {'mimeType':'multipart/alternative', 'parts':[payload('<b>HTML</b>', 'text/html'), payload('Plain')]}
        self.assertEqual(body_text(alternative), 'Plain')
        attachment = {**payload('SECRET'), 'filename':'notes.txt'}
        self.assertEqual(body_text({'mimeType':'multipart/mixed', 'parts':[alternative, attachment]}), 'Plain')

    def test_thread_grouping_and_accounts_are_distinct(self):
        first = gmail_email(message(), 'one@gmail.com')
        second = gmail_email(message('two'), 'one@gmail.com')
        other = gmail_email(message(), 'two@gmail.com')
        self.assertEqual(first['application_id'], second['application_id'])
        self.assertNotEqual(first['application_id'], other['application_id'])
        self.assertEqual(first['source'], 'gmail')

    def test_unrelated_is_not_imported(self):
        self.assertIsNone(gmail_email(message(subject='Dinner', body='See you tomorrow'), 'one@gmail.com'))

    def test_missing_body_is_rejected(self):
        with self.assertRaises(ValueError):
            gmail_email(message(body=''), 'one@gmail.com')


class GmailConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.credentials = self.root / 'credentials.json'
        self.credentials.write_text(json.dumps({'installed':{'client_id':'test-client','client_secret':'test-secret'}}))
        self.gmail = GmailConnection(self.credentials)
        self.store = Store(self.root / 'tracker.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def connected(self):
        self.gmail.tokens = {'access_token':'test-access', 'refresh_token':'test-refresh', 'expires_at':time.time()+3600}
        self.gmail.account = 'person@gmail.com'

    def test_missing_configuration_has_actionable_error(self):
        gmail = GmailConnection(self.root / 'missing.json')
        self.assertFalse(gmail.status()['configured'])
        with self.assertRaisesRegex(GmailError, 'Desktop app'):
            gmail.begin('http://127.0.0.1:8000/')

    def test_pkce_readonly_and_no_secrets_in_status_or_url(self):
        url, binding = self.gmail.begin('http://127.0.0.1:8000/')
        args = parse_qs(urlparse(url).query)
        self.assertEqual(args['scope'], [SCOPE])
        self.assertEqual(args['code_challenge_method'], ['S256'])
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.gmail.pending['verifier'].encode()).digest()).decode().rstrip('=')
        self.assertEqual(args['code_challenge'], [challenge])
        self.assertNotIn('test-secret', url)
        self.assertNotIn(binding, url)
        self.connected()
        self.assertNotIn('test-access', json.dumps(self.gmail.status()))

    def test_web_client_supports_local_callback_without_exported_redirect_list(self):
        self.credentials.write_text(json.dumps({'web':{'client_id':'web-client','client_secret':'web-secret'}}), encoding='utf-8-sig')
        self.assertTrue(self.gmail.status()['configured'])
        url, _ = self.gmail.begin('http://127.0.0.1:8000/')
        params = parse_qs(urlparse(url).query)
        self.assertEqual(params['client_id'], ['web-client'])
        self.assertEqual(params['redirect_uri'], ['http://127.0.0.1:8000/'])
        self.assertNotIn('web-secret', url)

    def test_wrong_state_cookie_expiry_and_replay_rejected(self):
        url, binding = self.gmail.begin('http://127.0.0.1:8000/')
        state = parse_qs(urlparse(url).query)['state'][0]
        with patch('app.gmail.google_request') as request:
            for wrong_state, wrong_cookie in [('bad',binding),(state,'bad'),('é',binding)]:
                with self.assertRaises(GmailError):
                    self.gmail.complete(wrong_state, wrong_cookie, 'code')
            request.assert_not_called()
            self.gmail.pending['created'] -= 601
            with self.assertRaises(GmailError):
                self.gmail.complete(state, binding, 'code')
        url, binding = self.gmail.begin('http://127.0.0.1:8000/')
        state = parse_qs(urlparse(url).query)['state'][0]
        with patch('app.gmail.google_request', side_effect=[{'access_token':'secret', 'scope':SCOPE}, {'emailAddress':'person@gmail.com'}]):
            self.gmail.complete(state, binding, 'code')
        self.assertTrue(self.gmail.status()['connected'])
        with self.assertRaises(GmailError):
            self.gmail.complete(state, binding, 'code')

    def test_denial_and_missing_scope(self):
        for denied in (True, False):
            url, binding = self.gmail.begin('http://127.0.0.1:8000/')
            state = parse_qs(urlparse(url).query)['state'][0]
            with patch('app.gmail.google_request', return_value={'access_token':'test','scope':'openid'}):
                with self.assertRaises(GmailError):
                    self.gmail.complete(state, binding, 'code', denied=denied)
            self.assertFalse(self.gmail.status()['connected'])

    def test_refresh_token_is_kept_in_memory(self):
        self.connected()
        self.gmail.tokens['expires_at'] = 0
        with patch('app.gmail.google_request', return_value={'access_token':'new-access','expires_in':3600}):
            self.assertEqual(self.gmail._access_token(), 'new-access')
        self.assertEqual(self.gmail.tokens['refresh_token'], 'test-refresh')
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['credentials.json','tracker.sqlite3'])

    def test_scan_filters_assesses_deduplicates_and_pages(self):
        self.connected()
        mails = [message(), message('scam', subject='Job offer', body='Pay a training fee to secure your job offer.'),
                 message('digest', subject='Daily job alerts', body='Interview tips'),
                 message('sent', labelIds=['SENT'])]
        listing = {'messages':[{'id':m['id']} for m in mails], 'nextPageToken':'next-page'}
        with patch('app.gmail.google_request', side_effect=[listing, *mails]):
            result = self.gmail.sync(self.store)
        self.assertEqual((result['added'],result['ignored'],result['assessment']['scam']), (2,2,1))
        self.assertTrue(result['has_more'])
        with patch('app.gmail.google_request', side_effect=[{'messages':[{'id':'one'}]}, mails[0]]) as request:
            again = self.gmail.sync(self.store)
        self.assertIn('pageToken=next-page', request.call_args_list[0].args[0])
        self.assertEqual(again['skipped'], 1)
        self.assertFalse(again['has_more'])

    def test_api_failure_does_not_advance_or_partially_import(self):
        self.connected()
        listing = {'messages':[{'id':'one'}, {'id':'two'}], 'nextPageToken':'next'}
        with patch('app.gmail.google_request', side_effect=[listing, message(), GmailError('offline')]):
            with self.assertRaises(GmailError):
                self.gmail.sync(self.store)
        self.assertIsNone(self.gmail.cursor)
        self.assertEqual(self.store.snapshot()['email_count'], 0)

    def test_unreadable_message_count_and_window_validation(self):
        self.connected()
        with patch('app.gmail.google_request', side_effect=[{'messages':[{'id':'one'}]}, message(body='')]):
            self.assertEqual(self.gmail.sync(self.store)['unreadable'], 1)
        for days in (True, '90', 300, -1):
            with self.assertRaises(GmailError):
                self.gmail.sync(self.store, days)

    def test_disconnect_clears_connection_even_when_revoke_fails(self):
        self.connected()
        with patch('app.gmail.google_request', side_effect=GmailError('offline')):
            result = self.gmail.disconnect()
        self.assertFalse(result['revoked'])
        self.assertIsNone(self.gmail.tokens)
        self.assertFalse(self.gmail.status()['connected'])


if __name__ == '__main__':
    unittest.main()
