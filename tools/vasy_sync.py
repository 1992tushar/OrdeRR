"""
vasy_sync.py — standalone bot that types already-generated invoices into Vasy ERP.

Fully self-contained. It does NOT import OrdeRR, touch its database, or write
anything back into the app. You give it two things:

    1. the invoices (a folder / file you point it at)
    2. your Vasy username + password

...and it logs into the Vasy web app and creates each invoice as a sales
voucher. It keeps its own little "already pushed" ledger file so re-running
never double-posts — no app database involved.

Runs on an office PC (needs a real browser + your normal office IP).

    python tools/vasy_sync.py --invoices ./invoices                 # dry-run
    python tools/vasy_sync.py --invoices ./invoices --live          # do it
    VASY_USERNAME=... VASY_PASSWORD=... python tools/vasy_sync.py --invoices ./invoices --headed

Credentials (any of):
    --user / --password / --url   CLI flags, or
    VASY_USERNAME / VASY_PASSWORD / VASY_URL   environment variables.

STATUS: the plumbing — reading the invoices folder, the local pushed-ledger,
login + session reuse, the per-invoice loop and summary — is done. The two
Vasy-specific pieces (login selectors + the sales-invoice form-filling) and the
invoice parser are marked `# TODO(phase-1)`; they get finalised once we have the
exact invoice format and a screen recording of one entry in Vasy.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


# ── Local "already pushed" ledger (idempotency, no database) ─────────────────

def load_ledger(path: Path) -> set[str]:
    if path.exists():
        try:
            return set(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_ledger(path: Path, done: set[str]) -> None:
    path.write_text(json.dumps(sorted(done), indent=1), encoding="utf-8")


# ── Read the invoices you supply ─────────────────────────────────────────────

def load_invoices(invoices_path: Path) -> list[dict]:
    """
    Turn the invoices you hand the bot into a list of dicts:
        {invoice_number, date, party_name, party_phone, total,
         items: [{code, name, qty, unit, rate, amount}, ...]}

    TODO(phase-1): implement the parser for the agreed input format
    (folder of generated invoice PDFs, or a CSV/Excel you provide). Kept as a
    single seam so the rest of the bot doesn't care what the source is.
    """
    if not invoices_path.exists():
        raise FileNotFoundError(f"--invoices path not found: {invoices_path}")
    # Enumerate what's there so a dry-run is useful even before parsing is wired.
    if invoices_path.is_dir():
        files = sorted(p for p in invoices_path.glob("*.pdf"))
    else:
        files = [invoices_path]
    print(f"Found {len(files)} invoice file(s) in {invoices_path}")
    sys.exit(
        "\nParser not wired yet (Phase 1). Confirm how invoices will be supplied "
        "(folder of generated PDFs, or a CSV/Excel) and send one sample — then "
        "load_invoices() gets finalised and this bot is ready to run."
    )


# ── Vasy web automation (Playwright) ─────────────────────────────────────────

def ensure_logged_in(context, url: str, username: str, password: str, session_file: str):
    page = context.new_page()
    page.goto(url, wait_until="domcontentloaded")
    if "login" not in page.url.lower() and page.locator("input[type='password']").count() == 0:
        return page  # already logged in via the reused session
    # TODO(phase-1): confirm the real login selectors + submit/redirect flow.
    page.locator("input[type='text'], input[name*='user' i], input[type='email']").first.fill(username)
    page.locator("input[type='password']").first.fill(password)
    page.locator("button[type='submit'], button:has-text('Login'), input[type='submit']").first.click()
    page.wait_for_load_state("networkidle")
    if "login" in page.url.lower():
        raise RuntimeError("Vasy login failed (still on a login URL). Check credentials/selectors.")
    context.storage_state(path=session_file)
    return page


def post_invoice_to_vasy(page, inv: dict, dry_run: bool) -> str | None:
    """Create ONE sales voucher in Vasy; return its voucher number."""
    if dry_run:
        items = ", ".join(f"{i.get('code','?')} {i.get('qty')}{i.get('unit','')}@{i.get('rate')}" for i in inv["items"])
        print(f"   [dry-run] {inv['invoice_number']}  {inv.get('party_name')}  ₹{inv.get('total')}  [{items}]")
        return None
    # TODO(phase-1): new sales invoice → party (create if missing) → line items
    # → save → read back voucher number. Wire from the screen recording.
    raise NotImplementedError("Vasy form-filling not wired yet (Phase 1). Use --dry-run.")


# ── Orchestration ────────────────────────────────────────────────────────────

def run(args):
    url      = args.url      or os.getenv("VASY_URL", "")
    username = args.user     or os.getenv("VASY_USERNAME", "")
    password = args.password or os.getenv("VASY_PASSWORD", "")
    dry_run  = not args.live

    invoices_path = Path(args.invoices).expanduser().resolve()
    ledger_path   = Path(args.ledger).expanduser().resolve()
    session_file  = str(Path(args.session).expanduser().resolve())

    invoices = load_invoices(invoices_path)          # raises until Phase 1 parser is wired
    done = load_ledger(ledger_path)
    pending = [i for i in invoices if i["invoice_number"] not in done]
    if args.limit:
        pending = pending[: args.limit]

    print(f"{len(invoices)} invoice(s) supplied, {len(done)} already pushed, "
          f"{len(pending)} to process. {'DRY-RUN' if dry_run else 'LIVE'}.")
    if not pending:
        return

    if not dry_run and not (url and username and password):
        sys.exit("Vasy URL/username/password required for --live (flags or VASY_* env).")

    from playwright.sync_api import sync_playwright  # office-PC only
    posted, failed = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(storage_state=session_file) if os.path.exists(session_file) else browser.new_context()
        page = ensure_logged_in(ctx, url, username, password, session_file)
        print("Logged in to Vasy ✓")
        for inv in pending:
            try:
                voucher = post_invoice_to_vasy(page, inv, dry_run)
                if not dry_run:
                    done.add(inv["invoice_number"])
                    save_ledger(ledger_path, done)     # persist after each success
                    posted.append(f"{inv['invoice_number']} → {voucher}")
                    print(f"   posted {inv['invoice_number']} → {voucher}")
            except Exception as e:
                failed.append(f"{inv['invoice_number']}: {e}")
                print(f"   FAILED {inv['invoice_number']}: {e}")
        ctx.close(); browser.close()

    print(f"\nDone. posted={len(posted)} failed={len(failed)}")
    for f in failed:
        print(f"  - {f}")


def main():
    ap = argparse.ArgumentParser(description="Type generated invoices into Vasy ERP.")
    ap.add_argument("--invoices", required=True, help="folder/file of invoices to push")
    ap.add_argument("--user",     help="Vasy username (or VASY_USERNAME env)")
    ap.add_argument("--password", help="Vasy password (or VASY_PASSWORD env)")
    ap.add_argument("--url",      help="Vasy login URL (or VASY_URL env)")
    ap.add_argument("--ledger",   default="tools/pushed_invoices.json", help="local already-pushed ledger")
    ap.add_argument("--session",  default="tools/.vasy_session.json", help="saved browser session")
    ap.add_argument("--live",     action="store_true", help="actually create vouchers (default: dry-run)")
    ap.add_argument("--headed",   action="store_true", help="show the browser window")
    ap.add_argument("--limit",    type=int, help="cap number of invoices (testing)")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
