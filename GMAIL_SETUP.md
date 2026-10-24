# Connect Gmail to Job Hunt Autopilot

Gmail is an optional extension to the file-import demo. The app connects directly to Google's Gmail API with your consent and requests **gmail.readonly**. It does not need your Gmail password or an app password.

## 1. Create your Google configuration

1. Open [Google Cloud Console](https://console.cloud.google.com/) and select or create a project.
2. Open **APIs & Services → Library**, find **Gmail API**, and enable it.
3. Open **Google Auth Platform**. Configure **Branding** with your application name and contact email.
4. For a personal Gmail hackathon demo, choose an **External** audience, keep the app in **Testing**, and add the Gmail addresses you intend to connect as **test users**.
5. Under **Data Access**, add `https://www.googleapis.com/auth/gmail.readonly`.
6. Under **Clients**, create or use a **Desktop app** or **Web application** OAuth client. For a Web application client, add exactly `http://127.0.0.1:8000/` under **Authorized redirect URIs** and save in Google Cloud. Include the trailing slash. Download its JSON file.
7. Save that file as **`config/gmail_credentials.json`** in this project. The JSON must contain an `installed` or `web` object. Redirect changes must be saved in Google Cloud; editing the downloaded file does not register a redirect. An older download without a redirect list is accepted once the URI is saved in Google Cloud.

Google documents the Desktop app client setup in its [Gmail Python quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python) and the PKCE/loopback authorization flow in its [desktop OAuth guide](https://developers.google.com/identity/protocols/oauth2/native-app). This implementation uses those HTTP endpoints directly and adds no Python dependencies.

The client file is excluded by `.gitignore`, and the HTTP server serves only the `static` directory. Do not put the file in `static/`, paste it into chat, or commit it. No real credentials are included in this project.

## 2. Connect and assess

```powershell
python server.py
```

Open **http://127.0.0.1:8000** in the browser on the same computer as the server. If the app was running before this feature was added, restart it and reload the page.

1. Click **Connect Gmail**. If configuration is missing, the app shows the setup instructions.
2. On Google's page, select your test-user account and grant read-only Gmail access.
3. Google redirects back to the local dashboard. The connected address appears in the Gmail section.
4. Choose **Last 30, 90, or 180 days**, then click **Scan job emails**.
5. Read the assessment summary: checked messages, job-related messages, new imports, duplicates, excluded messages, unreadable messages, and all five classification counts.
6. Open the tracker or **Flagged offers** to inspect messages and the evidence behind each assessment.

Each click processes at most 100 Gmail search matches. If more are available, the button becomes **Scan next 100 matches**. Continue until the scan is complete. Scanning again after completion starts a fresh search for recent messages; imports already in the tracker are skipped. Changing the date window also starts a fresh search. The continuation cursor is held in memory and resets on disconnect/restart. Batch assessment counts include previously imported messages encountered in that batch, while the new-import count only includes new records.

## What gets imported

- A Gmail query retrieves recent messages mentioning applications, interviews, hiring, offers, recruiters, onboarding, training fees, and related terms.
- A local relevance filter excludes ordinary unrelated content and explicit job-alert/digest subjects. Keywords can produce false positives and false negatives; this is not a complete mailbox audit.
- Sent messages, drafts, spam and trash are excluded. Archived incoming messages can be included. Consequently scams already in Spam are not assessed by this scan.
- MIME plain text is preferred, with HTML converted to text. Attachments are ignored. Unreadable or oversized bodies are counted and skipped, not silently assessed from a snippet.
- Accepted messages go through the same classifier and SQLite tracker as file imports. Scam signals include fee demands, sensitive information requests, and check-transfer instructions.
- Gmail account + thread ID provide the grouping key. Different threads may represent the same application and remain separate. A thread about multiple roles stays together. Company/role fall back to sender domain and subject; the app does not claim reliable entity extraction.
- Gmail receipt time drives chronological updates. A file export using a different sender timestamp can appear as a separate import because content deduplication includes timestamp.

The application does not send emails, mark messages read, create labels, modify Gmail, or automatically scan in the background. Click **Scan job emails** for updates. Existing seven-day reminders are recalculated in the tracker.

## Credentials, disconnect, and scope

OAuth uses a random state, a browser-binding HttpOnly cookie, a ten-minute authorization window, and PKCE. The callback checks the state and cookie before exchanging the code. Authorization codes are not printed in HTTP logs. Access and refresh tokens stay **only in server memory**, never in browser storage or token files. Refresh occurs when needed during the running session. Restarting the server requires reconnecting.

**Disconnect** attempts to revoke Google's grant and always drops the local token state. If Google cannot be reached, the UI tells you to revoke access through [Google Account connections](https://myaccount.google.com/connections). Already imported email contents remain in the local SQLite tracker; disconnect does not erase them. To use a separate empty workspace, start with `python server.py --db data/separate.sqlite3 --port 8001`.

This remains a localhost, single-person prototype. Anyone with access to that local workspace can see its imported messages. It is not a hosted multi-user login system. Gmail permission is mailbox-wide read access; the app's search filter limits what it processes but is not a narrower Google permission. Google's [scope reference](https://developers.google.com/identity/protocols/oauth2/scopes) lists `gmail.readonly`, and its [verification guidance](https://developers.google.com/workspace/gmail/api/auth/scopes) describes restricted-scope requirements for public distribution.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Setup dialog appears | Check the exact filename and that the downloaded JSON contains `installed` or `web`. |
| Access blocked or denied | Check the test-user list, enabled Gmail API, and Google Workspace administrator restrictions. |
| Redirect mismatch | For a Web application client, register exactly `http://127.0.0.1:8000/` in Google Cloud (or your selected port). For a Desktop app client, use the local URL on the same computer. |
| Connection fails after returning from Google | Start Connect Gmail again in the same browser. Keep cookies enabled, finish within ten minutes, and do not restart the server during consent. |
| Google authorization expired/revoked | Disconnect and reconnect. |
| Nothing found | Try 180 days. Only matching incoming messages are searched; use file import for messages the keyword filter misses. |
| More matches remain | Click Scan next 100 matches. |
| Scan fails mid-batch | Retry. No partial batch is committed and its cursor is not advanced. |

## Verification

The automated suite uses simulated Google responses to verify PKCE/state/cookie checks, refusal of missing permissions, token refresh, MIME handling, relevance filtering, grouping, pagination, duplicates, assessment counts, and failures. Run `python -m unittest discover -s tests -v`.

A real Google consent/API round trip requires your client configuration and sign-in and has not been performed as part of implementation.
