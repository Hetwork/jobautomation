"""Run with python server.py, then visit http://127.0.0.1:8000."""
import argparse
import csv
import io
import json
import mimetypes
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from app.classifier import PostingModel
from app.demo import demo_inbox
from app.gmail import GmailConnection, GmailError
from app.inbox import parse_inbox
from app.store import Store

ROOT = Path(__file__).resolve().parent
MAX_UPLOAD = 10 * 1024 * 1024


def make_handler(store, gmail=None):
    gmail = gmail or GmailConnection(ROOT / 'config' / 'gmail_credentials.json')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # OAuth codes arrive in the query string; do not log request lines.
            pass

        def valid_host(self):
            return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

        def respond(self, status, payload, content_type='application/json; charset=utf-8', filename=None, headers=None):
            if not isinstance(payload, bytes):
                payload = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            if filename:
                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if not self.valid_host():
                return self.respond(403, {'error': 'Use the localhost URL printed by the server.'})
            if self.headers.get('Host', '').startswith('localhost:'):
                return self.respond(303, b'', headers={'Location': f'http://127.0.0.1:{self.server.server_port}/'})
            route = urlparse(self.path).path
            query = parse_qs(urlparse(self.path).query)
            if route == '/' and any(k in query for k in ('code', 'state', 'error')):
                cookies = SimpleCookie()
                try:
                    cookies.load(self.headers.get('Cookie', ''))
                    cookie = cookies.get('gmail_oauth')
                    gmail.complete(query.get('state', [''])[0], cookie.value if cookie else '',
                                   query.get('code', [''])[0], denied='error' in query)
                    outcome = 'connected'
                except GmailError as error:
                    gmail.last_error = str(error)
                    outcome = 'failed'
                except (ValueError, KeyError, CookieError):
                    gmail.last_error = 'Gmail returned an invalid connection response. Start again from Connect Gmail.'
                    outcome = 'failed'
                return self.respond(303, b'', headers={'Location': '/?gmail=' + outcome,
                    'Set-Cookie': 'gmail_oauth=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0'})
            if route == '/api/gmail/status':
                return self.respond(200, gmail.status())
            if route == '/api/state':
                result = store.snapshot()
                metrics = ROOT / 'data' / 'model_metrics.json'
                result['model'] = json.loads(metrics.read_text()) if metrics.exists() and store.model and store.model.data else None
                return self.respond(200, result)
            if route == '/api/sample':
                return self.respond(200, demo_inbox(), filename='sample_inbox.json')
            if route == '/api/export':
                out = io.StringIO(newline='')
                writer = csv.writer(out)
                writer.writerow(['Company', 'Role', 'Status', 'Last email', 'Follow-up due', 'Scam reasons'])
                for app in store.snapshot()['applications']:
                    cells = [app['company'], app['role'], app['status'], app['last_activity'],
                             'Yes' if app['followup_due'] else 'No', '; '.join(r['reason'] for r in app['reasons'])]
                    writer.writerow(["'" + c if c.lstrip().startswith(('=', '+', '-', '@')) or c.startswith(('\t', '\r', '\n')) else c for c in cells])
                return self.respond(200, out.getvalue().encode('utf-8-sig'), 'text/csv; charset=utf-8', 'job_tracker.csv')
            if route.startswith('/api/'):
                return self.respond(404, {'error': 'Not found.'})
            path = (ROOT / 'static' / (unquote(route).lstrip('/') or 'index.html')).resolve()
            if not path.is_relative_to(ROOT / 'static') or not path.is_file():
                return self.respond(404, {'error': 'Not found.'})
            return self.respond(200, path.read_bytes(), mimetypes.guess_type(path)[0] or 'application/octet-stream')

        def do_POST(self):
            if not self.valid_host():
                return self.respond(403, {'error': 'Use the localhost URL printed by the server.'})
            # Local single-user service: reject cross-site browser writes.
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + self.headers.get('Host', ''):
                return self.respond(403, {'error': 'Cross-origin writes are not allowed.'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length < 0 or length > MAX_UPLOAD:
                    return self.respond(413, {'error': 'File is too large. Maximum upload size is 10 MB.'})
                data = self.rfile.read(length)
                route = urlparse(self.path)
                if route.path.startswith('/api/gmail/'):
                    if self.headers.get('X-Autopilot-Request') != '1':
                        return self.respond(403, {'error': 'Start Gmail actions from the dashboard.'})
                    if route.path == '/api/gmail/connect':
                        url, binding = gmail.begin(f'http://127.0.0.1:{self.server.server_port}/')
                        return self.respond(200, {'url': url}, headers={
                            'Set-Cookie': f'gmail_oauth={binding}; HttpOnly; SameSite=Lax; Path=/; Max-Age=600'})
                    if route.path == '/api/gmail/sync':
                        payload = json.loads(data or b'{}')
                        if not isinstance(payload, dict):
                            raise ValueError('Provide a scan window.')
                        return self.respond(200, gmail.sync(store, payload.get('days', 90)))
                    if route.path == '/api/gmail/disconnect':
                        return self.respond(200, gmail.disconnect())
                if route.path == '/api/import':
                    filename = parse_qs(route.query).get('filename', [''])[0]
                    return self.respond(200, store.import_emails(parse_inbox(data, filename)))
                if route.path == '/api/demo':
                    sample = ROOT / 'data' / 'sample_inbox.json'
                    if not sample.exists():
                        sample.write_bytes(demo_inbox())
                    return self.respond(200, store.import_emails(parse_inbox(sample.read_bytes(), 'sample.json')))
                if route.path == '/api/followup':
                    payload = json.loads(data)
                    if not isinstance(payload, dict) or not isinstance(payload.get('application_id'), str):
                        raise ValueError('Provide an application_id.')
                    store.log_followup(payload['application_id'])
                    return self.respond(200, {'ok': True})
                return self.respond(404, {'error': 'Not found.'})
            except (ValueError, UnicodeDecodeError) as error:
                return self.respond(400, {'error': str(error)})
            except Exception:
                import traceback
                traceback.print_exc()
                return self.respond(500, {'error': 'The operation failed. Check the server terminal and try again.'})
    return Handler


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--db', default=str(ROOT / 'data' / 'autopilot.sqlite3'))
    args = parser.parse_args()
    model = PostingModel(ROOT / 'data' / 'posting_model.json')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(Store(args.db, model)))
    print(f'Job Hunt Autopilot: http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
