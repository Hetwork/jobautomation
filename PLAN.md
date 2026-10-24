# Job Hunt Autopilot — 150-minute build

The demo follows someone rebuilding their job search after a layoff. A file of 20 emails becomes an application tracker, suspicious offers carry red warnings with evidence, and unanswered active applications surface after seven days.

| Time | Deliverable |
| --- | --- |
| 0–20 min | Define the import contract and a 20-email demo spanning all five labels |
| 20–55 min | One-pass classification, explainable scam rules, SQLite persistence and deduplication |
| 55–80 min | Application histories, chronological status updates and seven-day follow-ups |
| 80–110 min | Responsive dashboard, imports, search, details and CSV export |
| 110–130 min | Train and evaluate a lightweight fraud baseline from the supplied CSV |
| 130–150 min | Test edge cases, run the demo and prepare the pitch |

## Architecture

Python standard library HTTP server → JSON/mbox parser or optional Gmail importer → one classification per new email → SQLite → browser dashboard. No third-party Python dependencies. The app binds to localhost. Imported email contents remain in local storage; optional Gmail access communicates directly with Google using OAuth.

Rules give explicit scam evidence and determine lifecycle labels. A multinomial Naive Bayes model trained on job-posting text supplies an optional additional fraud signal inside that same classification pass. A model score alone does not trigger a scam label. Posting evaluation is not email accuracy, and the UI does not claim otherwise.

Explicit `application_id` is the best grouping key; email reply headers and company/role metadata provide fallbacks. Ambiguous messages remain separate rather than silently merging unrelated roles. A suspicious message keeps its application flagged for manual review. Latest dated lifecycle email determines other application stages, independent of import order.

Reminders appear for applied/interview applications with seven days of inactivity. Logging a follow-up restarts that clock without sending anything. Terminal applications and scam flags never receive follow-up prompts. Imports are additive and atomic; repeated imports do not reclassify existing emails.

## Scope boundary

The original 150-minute build uses file imports. The user-requested extension adds read-only Gmail OAuth, filtered imports and assessment summaries. No email sending, scheduled background notifications, resume rewriting, benefits advice, or separate scam product. Reminders refresh while the dashboard is open and on the next visit. The prototype is local and single-user, not an internet-facing production service.

## Gmail extension

1. Desktop OAuth flow with state, browser binding and PKCE; in-memory refresh tokens.
2. Search incoming messages over 30/90/180 days, then filter for job correspondence locally.
3. Import bounded, paginated batches with account/thread grouping and existing deduplication.
4. Show scan assessment counts and existing per-email stage/scam evidence.
5. Document Google setup and test using simulated API responses; live verification awaits user configuration and sign-in.
