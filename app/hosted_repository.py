"""One encrypted bounded workspace per browser session, persisted in PostgreSQL.

Row locks serialize same-workspace requests across serverless instances. The small
hackathon workspace is intentionally stored as one document, not a large mailbox.
"""
import json
from contextlib import contextmanager

import psycopg
from cryptography.fernet import Fernet, InvalidToken

MAX_STATE_BYTES = 2_000_000


class StorageError(RuntimeError):
    pass


class PostgresRepository:
    def __init__(self, url, encryption_key):
        self.url = url
        self.cipher = Fernet(encryption_key.encode())

    @contextmanager
    def workspace(self, workspace_id):
        try:
            with psycopg.connect(self.url, connect_timeout=10) as db:
                db.execute("SET LOCAL lock_timeout = '2s'")
                db.execute("SET LOCAL statement_timeout = '10s'")
                db.execute('INSERT INTO autopilot_workspaces(id,payload) VALUES(%s,%s) ON CONFLICT(id) DO NOTHING',
                           (workspace_id, self.cipher.encrypt(b'{}').decode()))
                row = db.execute('SELECT payload FROM autopilot_workspaces WHERE id=%s FOR UPDATE', (workspace_id,)).fetchone()
                try:
                    state = json.loads(self.cipher.decrypt(row[0].encode()))
                except (InvalidToken, ValueError):
                    raise StorageError('Workspace could not be decrypted. Check TOKEN_ENCRYPTION_KEY; do not regenerate it for existing data.') from None
                yield state
                encoded = json.dumps(state, ensure_ascii=False).encode()
                if len(encoded) > MAX_STATE_BYTES:
                    raise ValueError('Workspace storage limit reached (2 MB). Export your tracker before starting a new workspace.')
                db.execute('UPDATE autopilot_workspaces SET payload=%s, updated_at=now() WHERE id=%s',
                           (self.cipher.encrypt(encoded).decode(), workspace_id))
        except psycopg.errors.LockNotAvailable:
            raise StorageError('This workspace is processing another request. Wait for the current scan to finish and retry.') from None
        except psycopg.errors.UndefinedTable:
            raise StorageError('Database setup is incomplete. Run the SQL in schema.sql using your database console.') from None
        except psycopg.Error:
            # DSNs and database errors can contain credentials; never return them.
            raise StorageError('Database unavailable. Check DATABASE_URL and the database connection, then retry.') from None
