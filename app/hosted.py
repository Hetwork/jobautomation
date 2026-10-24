"""Vercel WSGI app. The original local server remains available as server.py."""
import csv
import io
import json
import os
import uuid
from contextlib import contextmanager
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, make_response, redirect, request, send_file, session
from werkzeug.exceptions import HTTPException

from app.classifier import PostingModel
from app.demo import demo_inbox
from app.gmail import GmailError
from app.hosted_gmail import HostedGmail
from app.hosted_repository import PostgresRepository, StorageError
from app.hosted_store import HostedStore
from app.inbox import parse_inbox

ROOT = Path(__file__).resolve().parents[1]
UPLOAD_LIMIT = 3 * 1024 * 1024
BATCH_SIZE = 20


@lru_cache(maxsize=1)
def model():
    return PostingModel(ROOT / 'data' / 'posting_model.json')


def create_app(config=None, repository=None):
    settings = dict(os.environ if config is None else config)
    app = Flask(__name__, static_folder=None)
    app.config.update(SECRET_KEY=settings.get('SESSION_SECRET'), SESSION_COOKIE_NAME='autopilot_session',
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SECURE=True, SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(days=30), MAX_CONTENT_LENGTH=UPLOAD_LIMIT)
    base = settings.get('APP_BASE_URL', '').rstrip('/')
    parsed = urlparse(base)
    if parsed.scheme == 'http' and parsed.hostname in ('localhost','127.0.0.1') and not settings.get('VERCEL'):
        app.config['SESSION_COOKIE_SECURE'] = False
    missing = [key for key in ('APP_BASE_URL','DATABASE_URL','SESSION_SECRET','TOKEN_ENCRYPTION_KEY') if not settings.get(key)]
    config_error = 'Deployment setup incomplete. Configure: ' + ', '.join(missing) if missing else None
    if not config_error:
        if (parsed.scheme not in ('http','https') or not parsed.netloc or parsed.path or parsed.query or parsed.fragment
                or parsed.username or parsed.password or (settings.get('VERCEL') and parsed.scheme != 'https')):
            config_error = 'APP_BASE_URL must be the public HTTPS origin, without a path or query.'
        elif len(settings['SESSION_SECRET']) < 32:
            config_error = 'SESSION_SECRET must contain at least 32 random characters.'
        else:
            try:
                repository = repository or PostgresRepository(settings['DATABASE_URL'], settings['TOKEN_ENCRYPTION_KEY'])
            except (ValueError, TypeError):
                config_error = 'TOKEN_ENCRYPTION_KEY must be a valid Fernet key. See VERCEL_DEPLOY.md.'

    @app.before_request
    def check_request():
        if request.path == '/api/health':
            return None
        if request.path.startswith('/api/') or (request.path == '/' and 'state' in request.args):
            if config_error:
                return jsonify(error=config_error), 503
            if request.host != parsed.netloc:
                return jsonify(error='Open the configured production URL to use this workspace: ' + base), 400
        if request.method == 'POST':
            if request.headers.get('X-Autopilot-Request') != '1':
                return jsonify(error='Start this action from the dashboard.'), 403
            if request.headers.get('Origin') not in (None, base):
                return jsonify(error='Cross-origin writes are not allowed.'), 403
        return None

    @app.after_request
    def security_headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        return response

    @app.errorhandler(Exception)
    def error_response(error):
        if isinstance(error, HTTPException):
            message = 'Import a file smaller than 3 MB.' if error.code == 413 else error.description
            return jsonify(error=message), error.code
        if isinstance(error, (ValueError, GmailError)):
            return jsonify(error=str(error)), 400
        if isinstance(error, StorageError):
            return jsonify(error=str(error)), 503
        # No raw exception/URL logs: callbacks, DSNs and Google errors may carry secrets.
        return jsonify(error='The request failed. Check deployment configuration and retry.'), 500

    @contextmanager
    def workspace():
        workspace_id = session.get('workspace')
        if not workspace_id:
            workspace_id = uuid.uuid4().hex
            session['workspace'] = workspace_id
            session.permanent = True
        with repository.workspace(workspace_id) as state:
            yield state, HostedStore(state.setdefault('tracker', {}), model())

    @contextmanager
    def gmail_workspace():
        with workspace() as (state, store):
            gmail = HostedGmail(state.get('gmail', {}), settings)
            try:
                yield gmail, store
            finally:
                state['gmail'] = gmail.dump()

    @app.get('/api/health')
    def health():
        return jsonify(status='needs_configuration' if config_error else 'ready',
                       error=config_error, storage='postgresql', gmail_configured=bool(settings.get('GOOGLE_CLIENT_ID') and settings.get('GOOGLE_CLIENT_SECRET'))), 503 if config_error else 200

    @app.get('/')
    def index():
        if base and request.host != parsed.netloc:
            return redirect(base + '/', code=303)
        if any(k in request.args for k in ('state','code','error')):
            # A callback without the signed initiating session must never create a new workspace.
            if config_error or not session.get('workspace'):
                return redirect('/?gmail=failed', code=303)
            with gmail_workspace() as (gmail, _):
                try:
                    gmail.complete(request.args.get('state',''), request.cookies.get('gmail_oauth',''),
                                   request.args.get('code',''), denied='error' in request.args)
                    outcome = 'connected'
                except GmailError as error:
                    gmail.last_error = str(error)
                    outcome = 'failed'
            response = redirect('/?gmail=' + outcome, code=303)
            response.delete_cookie('gmail_oauth', path='/', secure=app.config['SESSION_COOKIE_SECURE'], httponly=True, samesite='Lax')
            return response
        return send_file(ROOT / 'static' / 'index.html')

    # Used by Flask local testing; Vercel serves the generated public/ files via CDN.
    @app.get('/app.js')
    def javascript():
        return send_file(ROOT / 'static' / 'app.js')

    @app.get('/styles.css')
    def stylesheet():
        return send_file(ROOT / 'static' / 'styles.css')

    @app.get('/api/state')
    def state():
        with workspace() as (_, store):
            result = store.snapshot()
        metrics = ROOT / 'data' / 'model_metrics.json'
        result['model'] = json.loads(metrics.read_text()) if metrics.exists() and model().data else None
        result['deployment'] = {'hosted':True, 'max_upload_bytes':UPLOAD_LIMIT, 'gmail_batch_size':BATCH_SIZE}
        return jsonify(result)

    @app.get('/api/sample')
    def sample():
        return make_response(demo_inbox(), 200, {'Content-Type':'application/json',
                                               'Content-Disposition':'attachment; filename="sample_inbox.json"'})

    @app.post('/api/demo')
    def demo():
        with workspace() as (data, store):
            # Stable per workspace, including across date changes and cold starts.
            if not data.get('sample'):
                data['sample'] = json.loads(demo_inbox())
            result = store.import_emails(parse_inbox(json.dumps(data['sample']).encode(), 'demo.json'))
        return jsonify(result)

    @app.post('/api/import')
    def import_file():
        emails = parse_inbox(request.get_data(), request.args.get('filename',''))
        with workspace() as (_, store):
            result = store.import_emails(emails)
        return jsonify(result)

    @app.post('/api/followup')
    def followup():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get('application_id'), str):
            raise ValueError('Provide an application_id.')
        with workspace() as (_, store):
            store.log_followup(payload['application_id'])
        return jsonify(ok=True)

    @app.get('/api/export')
    def export():
        with workspace() as (_, store):
            applications = store.snapshot()['applications']
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['Company','Role','Status','Last email','Follow-up due','Scam reasons'])
        for item in applications:
            cells = [item['company'],item['role'],item['status'],item['last_activity'],
                     'Yes' if item['followup_due'] else 'No','; '.join(r['reason'] for r in item['reasons'])]
            writer.writerow(["'"+c if c.lstrip().startswith(('=','+','-','@')) or c.startswith(('\t','\r','\n')) else c for c in cells])
        return make_response(output.getvalue().encode('utf-8-sig'), 200,
            {'Content-Type':'text/csv; charset=utf-8','Content-Disposition':'attachment; filename="job_tracker.csv"'})

    @app.get('/api/gmail/status')
    def gmail_status():
        with gmail_workspace() as (gmail, _):
            result = gmail.status()
        result.update({'hosted':True,'batch_size':BATCH_SIZE,'redirect_uri':base+'/'})
        return jsonify(result)

    @app.post('/api/gmail/connect')
    def gmail_connect():
        with gmail_workspace() as (gmail, _):
            url, binding = gmail.begin(base + '/')
        response = jsonify(url=url)
        response.set_cookie('gmail_oauth', binding, max_age=600, path='/', httponly=True,
                            secure=app.config['SESSION_COOKIE_SECURE'], samesite='Lax')
        return response

    @app.post('/api/gmail/sync')
    def gmail_sync():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValueError('Provide a scan window.')
        with gmail_workspace() as (gmail, store):
            result = gmail.sync(store, payload.get('days',90), batch_size=BATCH_SIZE, max_seconds=75)
        return jsonify(result)

    @app.post('/api/gmail/disconnect')
    def gmail_disconnect():
        with gmail_workspace() as (gmail, _):
            result = gmail.disconnect()
        return jsonify(result)

    return app
