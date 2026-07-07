# Vasy ERP sync bot (standalone)

Types your **already-generated** invoices into **Vasy ERP** (which has no
import/API) by driving the Vasy web app with Playwright.

It is completely self-contained: it does **not** import OrdeRR, use its
database, or write anything back into the app. You give it the invoices and your
Vasy login — that's all. It keeps its own local "already pushed" file so
re-running never double-posts.

Runs on an **office PC** (needs a real browser + your normal office IP).

## Setup (once)

```
pip install -r tools/requirements-vasy.txt
python -m playwright install chromium
```

## Run

```
# dry-run: lists what it would push, touches nothing
python tools/vasy_sync.py --invoices ./invoices

# actually create the vouchers in Vasy
python tools/vasy_sync.py --invoices ./invoices --live --headed
```

Credentials via flags or environment variables:

```
--user / --password / --url
VASY_USERNAME / VASY_PASSWORD / VASY_URL
```

Local state files (git-ignored, live only on this PC):
- `tools/pushed_invoices.json` — the already-pushed ledger (idempotency)
- `tools/.vasy_session.json` — the saved Vasy browser session

## Nightly schedule (Windows Task Scheduler)

Basic Task → Daily at your post-closure time → *Start a program*:
- Program: `python`
- Arguments: `tools\vasy_sync.py --invoices C:\path\to\invoices --live`
- Start in: the repo folder

Run it **attended** for a few nights before trusting it unattended.

## Status

Plumbing (invoices folder, pushed-ledger, login + session reuse, per-invoice
loop, summary) is done. Two pieces are `# TODO(phase-1)`:
- **the invoice parser** (`load_invoices`) — finalised once the input format is
  confirmed (folder of generated PDFs, or a CSV/Excel);
- **the Vasy login selectors + sales-invoice form-filling** — wired from a screen
  recording of one invoice being entered in Vasy.
