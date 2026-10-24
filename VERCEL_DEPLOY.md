# Deploy on Vercel

This project deploys the Flask application in [`api/index.py`](./api/index.py)
as a Vercel Python function. The frontend assets are copied to `public/` during
the Vercel build and served by Vercel's CDN.

## 1. Create a PostgreSQL database

Use a hosted PostgreSQL provider such as Neon, then run the SQL in
[`schema.sql`](./schema.sql) in that database's SQL console. The application
stores one encrypted workspace document per browser session.

## 2. Import the repository into Vercel

Create a Vercel project from this repository with the project root set to the
repository root. The checked-in [`vercel.json`](./vercel.json) supplies the
build command, Python function entrypoint, asset handling, and rewrites.

Do not set an output directory. The build command writes only `app.js` and
`styles.css` to `public/`; the `/` route must continue to be handled by
`api/index.py` for signed sessions and Gmail OAuth callbacks.

## 3. Configure environment variables

Add these variables in **Project Settings → Environment Variables** for the
Production environment:

| Variable | Value |
| --- | --- |
| `APP_BASE_URL` | The exact public URL, for example `https://your-project.vercel.app` |
| `DATABASE_URL` | PostgreSQL connection string with `sslmode=require` |
| `SESSION_SECRET` | At least 32 random characters |
| `TOKEN_ENCRYPTION_KEY` | A Fernet key |
| `GOOGLE_CLIENT_ID` | Optional: Google OAuth web-client ID |
| `GOOGLE_CLIENT_SECRET` | Optional: matching Google OAuth client secret |

Generate secrets locally without committing them:

```powershell
.\.venv-vercel\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
.\.venv-vercel\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Keep `SESSION_SECRET` and `TOKEN_ENCRYPTION_KEY` unchanged after deployment.
Changing either one invalidates existing sessions or makes existing workspace
data unreadable.

## 4. Optional Gmail setup

For Gmail sync, create a Google OAuth **Web application** client and add this
exact authorized redirect URI:

```text
https://your-project.vercel.app/
```

Then set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in Vercel. Gmail sync is
optional; inbox file imports and the demo work without these variables.

## 5. Deploy and verify

Deploy from Vercel, then check:

```text
https://your-project.vercel.app/api/health
```

The response should have `"status": "ready"`. If it reports
`needs_configuration`, check the environment variables and redeploy. If it
reports incomplete database setup, run [`schema.sql`](./schema.sql).

The Vercel filesystem is ephemeral, so the hosted build deliberately uses
PostgreSQL instead of the local SQLite database in `data/`.
