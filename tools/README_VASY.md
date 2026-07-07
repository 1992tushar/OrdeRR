# Vasy ERP sync bot

Re-creates OrdeRR invoices in **Vasy ERP** (which has no import/API) by driving
the Vasy **web** app with Playwright. Reads invoices marked `vasy_status='pending'`,
creates each as a sales voucher, writes Vasy's voucher number back (so re-runs
never double-post), and emails a reconciliation report.

## Where it runs

On an **always-on office PC**, on a schedule — **not** on Render. It needs a
real browser and your normal office IP, and it connects to the **same database**
OrdeRR uses (point `DATABASE_URL` at the production Postgres).

## One-time setup (office PC)

1. Install Python 3.12+, then from the repo root:
   ```
   pip install -r tools/requirements-vasy.txt
   python -m playwright install chromium
   ```
2. Create a `.env` in the repo root (it is git-ignored):
   ```
   # same production DB OrdeRR uses
   DATABASE_URL=postgresql://<prod-connection-string>

   # Vasy login
   VASY_URL=https://<your-vasy-login-url>
   VASY_HOME_URL=https://<a-page-you-see-after-login>
   VASY_USERNAME=<vasy user>
   VASY_PASSWORD=<vasy pass>

   # reconciliation email (reuses OrdeRR's SMTP)
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=officeoffluffy@gmail.com
   SMTP_PASSWORD=<gmail app password>
   REPORT_EMAIL=officeoffluffy@gmail.com
   ```

## Running

```
python tools/vasy_sync.py                 # DRY-RUN: log in + list what it WOULD post
python tools/vasy_sync.py --headed        # same, but watch the browser
python tools/vasy_sync.py --date 2026-07-07 --headed
python tools/vasy_sync.py --live          # actually create vouchers (Phase 1+)
```

Start with a **dry-run** — it validates DB access, the Vasy login, and session
reuse without writing anything to Vasy.

## Nightly schedule (Windows Task Scheduler)

Create a Basic Task → trigger *Daily* at your post-closure time (e.g. 23:30) →
Action *Start a program*:
- Program: `python`
- Arguments: `tools\vasy_sync.py --live`
- Start in: the repo folder

Run it **attended** for a few nights before trusting it unattended.

## Status

- ✅ DB pull, session reuse, idempotent write-back, reconciliation email — done.
- ⏳ **Vasy login selectors** and **sales-invoice form-filling** — stubbed
  (`# TODO(phase-1)` in `vasy_sync.py`). These get wired from a screen recording
  of one invoice being entered in Vasy. Until then, `--live` refuses to guess at
  the form; `--dry-run` works.

## Security

Vasy credentials and the saved session (`tools/.vasy_session.json`) live only on
this PC and are git-ignored. Never commit `.env` or the session file.
