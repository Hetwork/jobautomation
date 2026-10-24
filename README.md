# Job Hunt Autopilot

**Track every application. Catch suspicious offers before you act.** A local hackathon app for someone rebuilding their job search after a layoff: import an inbox, see application stages, review red scam warnings, and find follow-ups due after seven days.

## Run

Requires Python 3.10 or newer. No pip packages or API keys are needed for file imports. Optional Gmail connection requires a Google Desktop app OAuth client and consent.

```powershell
python server.py
```

Open **http://127.0.0.1:8000**, then select **Load demo inbox**. To use a different port: `python server.py --port 8001`.

## Optional: connect your Gmail

The dashboard now includes **Connect Gmail**, a 30/90/180-day scan window, job-email filtering, and a batch assessment summary. Matching messages enter the existing tracker with scam reasons and follow-up reminders. Access is read-only; tokens remain in server memory.

Follow [GMAIL_SETUP.md](GMAIL_SETUP.md) to enable Gmail API, create a Desktop app OAuth client, add your address as a test user, and save the download as `config/gmail_credentials.json`. Then connect and click **Scan job emails**. Each scan checks up to 100 matching incoming messages, with continuation for further batches.

File imports still work without Google configuration. The real Google sign-in flow requires your own credentials and consent; automated Gmail tests use simulated API responses.

The included 20-email sample creates **16 applications: 6 applied, 4 interview, 2 rejected, 1 offer, and 3 potential scams**. Five reminders become due by the end of the sample's generation day. The checked-in sample has October 24, 2026 dates. Demo imports reuse that file so repeated clicks do not create duplicates. **Download sample JSON** generates a fresh sample relative to the current UTC day; import that for later presentations (prefer a fresh database when changing demo dates).

## Two-minute demo

1. “After a layoff, I applied everywhere. Now the replies are scattered, and some offers are trying to exploit the urgency.”
2. Click **Load demo inbox**: show 20 emails condensed into 16 applications. Northstar has progressed from applied to interview; Orbit Labs has an offer.
3. Open **Flagged offers → RemoteHub Careers**: show the training-fee demand and no-interview promise, highlighted with reasons. Vertex requests identity/bank details; Apex uses a check-transfer request.
4. Open **Follow-ups → Figment**: copy a follow-up draft. After sending it elsewhere, choose **I sent a follow-up** to restart the reminder clock. The app sends no email.
5. Import the same file again: all 20 emails are skipped. Export the tracker as CSV.
6. Open **How it works** to explain the rules and honest dataset evaluation.

## Import format

Select **Import inbox** and upload a `.json` or `.mbox` file. Maximum: 10 MB, 1,000 emails, 200,000 characters per email body. JSON accepts an array or `{ "emails": [...] }`:

```json
{
  "emails": [
    {
      "application_id": "northstar-product-designer",
      "company": "Northstar",
      "role": "Product Designer",
      "from": "Careers <careers@northstar.example>",
      "date": "2026-10-14T10:00:00+05:30",
      "subject": "Application received",
      "body": "Thank you for applying. We have received your application.",
      "message_id": "<application-123@northstar.example>"
    }
  ]
}
```

`from` (or `sender`), `date`, `subject`, and `body` are required. ISO 8601 and email-style dates are supported; dates without a timezone are treated as UTC. Other fields are optional. Use the same `application_id` for every message about one application, and a different one for each distinct application—even if company and role are identical.

For `.mbox`, standard From/Date/Subject and MIME plain-text or HTML bodies are parsed; attachments are ignored. `Message-ID`, `In-Reply-To`, and `References` link replies when the parent is available. Optional `X-Application-ID`, `X-Company`, and `X-Role` headers provide explicit metadata. A 20-message example is at `data/sample_inbox.mbox`.

Grouping priority: explicit application ID → known reply reference → supplied company + role → separate record. Without metadata or a known parent, the sender domain and subject are display fallbacks. This avoids merging different jobs from a shared ATS domain. A reply imported before its parent in a separate import is not retroactively regrouped; use explicit application IDs when available. There is no general-purpose company/role extraction model.

## Pipeline and persistence

`file → validation → content deduplication → one classification → SQLite → tracker`

- One of five labels per email; scam checks have priority over lifecycle signals.
- Explicit risky requests include job/training payments, sensitive documents or bank details, check transfers, and promises of hiring without interviews. Reasons include matched text. Simple quoted replies and negated requests are excluded.
- Latest email timestamp determines lifecycle stage, independent of import order. Any scam-labeled email keeps its application flagged. No manual flag-dismissal workflow is included.
- Reminders are computed for applied/interview applications after **at least 7 × 24 hours** since the newest email or logged follow-up. Offers, rejections, and scam flags are excluded. The dashboard refreshes every 30 seconds while visible and on returning to the tab; no closed-browser notifications are sent.
- Imports validate completely before writing and commit atomically. Existing messages are not reclassified after model changes. Content identity uses sender, timestamp, subject and body, so changing grouping metadata alone does not reimport a message.
- State is saved in `data/autopilot.sqlite3`. To run an isolated demonstration without changing existing data: `python server.py --db data/new_demo.sqlite3 --port 8001`.
- CSV output escapes spreadsheet formula prefixes. Email content is escaped as text in the UI.

## Using the supplied Kaggle CSV

The supplied `fake_job_postings.csv` is used by the included dependency-free multinomial Naive Bayes baseline. Its generated model and metrics are in `data/`.

```powershell
python scripts/train_model.py
```

Restart the server after training. Training uses title, company profile, description, requirements and benefits; exact duplicate normalized texts are removed before a stratified 80/20 split with seed 42. Vocabulary is built on training rows only. No labels or IDs are included as input features.

Observed evaluation on this CSV:

| Metric | Result |
| --- | --- |
| Raw postings | 17,880 |
| Exact duplicates removed | 2,187 |
| Training / held-out postings | 12,553 / 3,140 |
| Fraud precision | 42.1% |
| Fraud recall | 74.6% |
| Fraud F1 | 0.538 |
| Accuracy | 94.2% |

The accuracy is inflated by the predominance of real postings. These are **posting metrics, not email metrics**. Near-duplicate postings can still cross the split. Scores are uncalibrated and never trigger a scam label on their own; a strong score can add supporting evidence to an existing rule flag. Rules may miss paraphrased scams and misread legitimate requests. Unflagged does not mean verified safe. No email-classifier accuracy claim is made from this dataset.

## Checks

```powershell
python -m unittest discover -s tests -v
node --check static/app.js
```

Python tests cover all five labels, scam evidence, negation, deduplication, chronological transitions, exact seven-day boundaries, terminal-status exclusions, follow-up logging, reply grouping, persistence, atomic imports, MIME parsing, and the complete sample. Node is only needed for the optional JavaScript syntax check, not to run the app.

## Files

- `PLAN.md`: the 150-minute implementation plan and scope.
- `server.py`: localhost HTTP API and static frontend server.
- `app/`: inbox parser, classifier, persistence and demo generator.
- `static/`: responsive dashboard, search, filters, details and drafts.
- `scripts/train_model.py`: reproducible CSV training and evaluation.
- `data/model_metrics.json`: exact evaluation and confusion matrix.
- `tests/`: pipeline and API regression tests.

This is a local single-user prototype, not an authenticated multi-user service. Gmail OAuth is optional; automatic sending, resume rewriting, PF and gratuity features remain out of scope.
