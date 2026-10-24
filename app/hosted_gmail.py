import os

from app.gmail import GmailConnection, GmailError

FIELDS = ('pending', 'tokens', 'account', 'cursor', 'window', 'last_sync', 'last_error')


class HostedGmail(GmailConnection):
    def __init__(self, state, config=None):
        super().__init__('unused-hosted-credentials')
        self.config = config if config is not None else os.environ
        for field in FIELDS:
            setattr(self, field, state.get(field))

    def _client(self):
        client_id = self.config.get('GOOGLE_CLIENT_ID')
        secret = self.config.get('GOOGLE_CLIENT_SECRET')
        if not client_id or not secret:
            raise GmailError('Gmail is not configured on this deployment. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in Vercel.')
        return {'client_id':client_id, 'client_secret':secret}

    def dump(self):
        return {field:getattr(self, field) for field in FIELDS}
