import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from app.store import Store
from app.gmail import GmailConnection, SCOPE
from server import make_handler


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.temp.name) / 'api.sqlite3')
        credentials = Path(cls.temp.name) / 'credentials.json'
        credentials.write_text(json.dumps({'installed':{'client_id':'test-client','client_secret':'test-secret'}}))
        cls.gmail = GmailConnection(credentials)
        handler = make_handler(cls.store, cls.gmail)
        handler.log_message = lambda *args: None
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request(self, path, data=None, headers=None):
        return urlopen(Request(self.base + path, data=data, headers=headers or {}), timeout=5)

    def test_static_and_security_headers(self):
        with self.request('/') as response:
            self.assertEqual(response.status, 200)
            self.assertIn('Job Hunt Autopilot', response.read().decode())
            self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
        with self.assertRaises(HTTPError) as error:
            self.request('/%2e%2e/server.py')
        self.assertEqual(error.exception.code, 404)

    def test_sample_import_state_and_export(self):
        with self.request('/api/sample') as response:
            sample = response.read()
        with self.request('/api/import?filename=inbox.json', sample) as response:
            self.assertEqual(json.load(response)['added'], 20)
        with self.request('/api/state') as response:
            state = json.load(response)
        self.assertEqual(state['email_count'], 20)
        self.assertEqual(state['counts']['scam'], 3)
        with self.request('/api/import?filename=inbox.json', sample) as response:
            self.assertEqual(json.load(response)['skipped'], 20)
        with self.request('/api/export') as response:
            self.assertIn('training', response.read().decode('utf-8-sig'))
            self.assertIn('attachment', response.headers['Content-Disposition'])

    def test_invalid_import_is_a_clear_error(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/import?filename=inbox.json', b'{')
        self.assertEqual(error.exception.code, 400)
        self.assertIn('Invalid JSON', json.load(error.exception)['error'])

    def test_cross_origin_write_rejected(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/demo', b'', {'Origin':'https://example.com'})
        self.assertEqual(error.exception.code, 403)

    def test_invalid_followup(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/followup', b'{"application_id":"missing"}')
        self.assertEqual(error.exception.code, 400)

    def test_gmail_http_oauth_callback_and_disconnect(self):
        headers = {'X-Autopilot-Request':'1', 'Content-Type':'application/json'}
        with self.request('/api/gmail/connect', b'{}', headers) as response:
            cookie = response.headers['Set-Cookie'].split(';')[0]
            self.assertIn('HttpOnly', response.headers['Set-Cookie'])
            url = json.load(response)['url']
        state = parse_qs(urlparse(url).query)['state'][0]
        with patch('app.gmail.google_request', side_effect=[{'access_token':'test-token','scope':SCOPE}, {'emailAddress':'test@gmail.com'}]):
            with self.request('/?code=test-code&state=' + state, headers={'Cookie':cookie}) as response:
                self.assertTrue(response.url.endswith('/?gmail=connected'))
        with self.request('/api/gmail/status') as response:
            result = json.load(response)
        self.assertEqual(result['account'], 'test@gmail.com')
        self.assertNotIn('test-token', json.dumps(result))
        with patch('app.gmail.google_request', return_value={}):
            with self.request('/api/gmail/disconnect', b'{}', headers) as response:
                self.assertTrue(json.load(response)['revoked'])

    def test_gmail_action_requires_custom_header_and_host_is_checked(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/gmail/connect', b'{}')
        self.assertEqual(error.exception.code, 403)

    def test_gmail_callback_failure_exposes_safe_reason(self):
        with self.request('/?state=invalid&code=do-not-expose') as response:
            self.assertTrue(response.url.endswith('/?gmail=failed'))
        with self.request('/api/gmail/status') as response:
            result = json.load(response)
        self.assertIn('expired or could not be verified', result['last_error'])
        self.assertNotIn('do-not-expose', json.dumps(result))
        with self.assertRaises(HTTPError) as error:
            self.request('/api/state', headers={'Host':'attacker.example'})
        self.assertEqual(error.exception.code, 403)


if __name__ == '__main__':
    unittest.main()
