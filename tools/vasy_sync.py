"""
vasy_sync.py — nightly bridge that re-creates OrdeRR invoices in Vasy ERP.

Vasy has no import/API, so this drives the Vasy *web* app with Playwright:
log in (reusing a saved session), read every invoice OrdeRR marked
`vasy_status='pending'`, create each as a sales voucher in Vasy, write the Vasy
voucher number back, and email a reconciliation report.

RUNS ON AN OFFICE PC (not Render) via Windows Task Scheduler — it needs a real
browser and your normal office IP. It talks to the SAME database as OrdeRR
(point DATABASE_URL at the production Postgres in this machine's .env).

────────────────────────────────────────────────────────────────────────────
STATUS: Phase 0 skeleton. The DB pull, session reuse, idempotent write-back and
reconciliation email are complete and runnable TODAY (great for validating the
Vasy login). The Vasy-specific bits — the login selectors and the sales-invoice
form-filling — are STUBBED and marked `# TODO(phase-1)`. They get wired once we
have a screen recording of one invoice being entered in Vasy.

Usage (from the repo root):
    python tools/vasy_sync.py              # dry-run: logs in + lists what it WOULD post
    python tools/vasy_sync.py --live       # actually creates vouchers (Phase 1+)
    python tools/vasy_sync.py --date 2026-07-07 --headed
────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import argparse
import os
import sys
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from pathlib import Path

# Make `import orderr_core...` work when run as `python tools/vasy_sync.py`.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from orderr_core.database import SessionLocal
from orderr_core.models.invoice import Invoice
from orderr_core.models.order import Order
from orderr_core.models.customer import Customer
from orderr_core.services.invoice_pdf import _product_info, _buyer_phone

IST = timezone(timedelta(hours=5, minutes=30))

# ── Config (env, with sensible defaults) ─────────────────────────────────────
VASY_URL          = os.getenv("VASY_URL", "")                       # login page URL
VASY_HOME_URL     = os.getenv("VASY_HOME_URL", VASY_URL)            # a post-login URL
VASY_USERNAME     = os.getenv("VASY_USERNAME", "")
VASY_PASSWORD     = os.getenv("VASY_PASSWORD", "")
VASY_SESSION_FILE = os.getenv("VASY_SESSION_FILE", str(REPO_ROOT / "tools" / ".vasy_session.json"))
# Login selectors — placeholders; confirm against the real Vasy login page.
SEL_USERNAME = os.getenv("VASY_SEL_USERNAME", "input[type='text'], input[name*='user' i], input[type='email']")
SEL_PASSWORD = os.getenv("VASY_SEL_PASSWORD", "input[type='password']")
SEL_SUBMIT   = os.getenv("VASY_SEL_SUBMIT",   "button[type='submit'], button:has-text('Login'), input[type='submit']")

# Reconciliation email reuses OrdeRR's SMTP config.
SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
REPORT_EMAIL  = os.getenv("REPORT_EMAIL", "")


# ── Data layer ───────────────────────────────────────────────────────────────

def fetch_pending(db, business_date: str | None):
    """Pending invoices (optionally for one business_date), with the data the
    Vasy voucher needs: buyer name, party details, and line items."""
    q = select(Invoice).where(Invoice.vasy_status == "pending")
    if business_date:
        q = q.where(Invoice.business_date == business_date)
    q = q.order_by(Invoice.invoice_number)
    invoices = db.scalars(q).all()

    payloads = []
    for inv in invoices:
        order = db.scalar(select(Order).where(Order.id == inv.order_id))
        hotel_name = (order.customer_name if order else None) or inv.customer_phone
        cust = db.scalar(select(Customer).where(Customer.phone_number == inv.customer_phone))
        items = [
            {
                "product":   it.product,
                "item_code": _product_info(it.product)[0],
                "erp_name":  _product_info(it.product)[1],
                "qty":       float(it.quantity),
                "unit":      it.unit,
                "rate":      float(it.rate_used),
                "amount":    float(it.amount),
            }
            for it in inv.items
        ]
        payloads.append({
            "invoice": inv,
            "invoice_number": inv.invoice_number,
            "business_date":  str(inv.business_date),
            "buyer_name":     hotel_name,
            "buyer_phone":    _buyer_phone(inv.customer_phone),
            "party": {
                "name":    (cust.restaurant_name if cust else hotel_name),
                "phone":   (cust.phone_number if cust else inv.customer_phone),
                "address": (cust.address if cust else "") or "",
                "city":    (cust.city if cust else "") or "",
            } if cust else None,
            "total": float(inv.total),
            "items": items,
        })
    return payloads


def mark(db, invoice: Invoice, status: str, voucher_no: str | None = None, error: str | None = None):
    invoice.vasy_status = status
    invoice.vasy_voucher_no = voucher_no
    invoice.vasy_error = (error or "")[:1000] or None
    invoice.vasy_pushed_at = datetime.now(IST)
    db.commit()


# ── Vasy web automation (Playwright) ─────────────────────────────────────────

def ensure_logged_in(context):
    """Reuse a saved session if valid, otherwise log in and persist it."""
    page = context.new_page()
    page.goto(VASY_HOME_URL or VASY_URL, wait_until="domcontentloaded")

    # Heuristic: if we can see a password field / the URL says login, we're out.
    logged_out = "login" in page.url.lower() or page.locator(SEL_PASSWORD).count() > 0
    if not logged_out:
        return page

    # TODO(phase-1): confirm these selectors + the submit/redirect flow against
    # the real Vasy login page (from the screen recording).
    if not (VASY_URL and VASY_USERNAME and VASY_PASSWORD):
        raise RuntimeError("VASY_URL / VASY_USERNAME / VASY_PASSWORD must be set in .env")
    page.goto(VASY_URL, wait_until="domcontentloaded")
    page.locator(SEL_USERNAME).first.fill(VASY_USERNAME)
    page.locator(SEL_PASSWORD).first.fill(VASY_PASSWORD)
    page.locator(SEL_SUBMIT).first.click()
    page.wait_for_load_state("networkidle")
    if "login" in page.url.lower():
        raise RuntimeError("Vasy login appears to have failed (still on a login URL).")
    context.storage_state(path=VASY_SESSION_FILE)   # persist session for next run
    return page


def post_invoice_to_vasy(page, payload: dict, dry_run: bool) -> str | None:
    """
    Create ONE sales voucher in Vasy and return its voucher number.

    Phase 0: form-filling is not wired yet. In dry-run we only report what we
    WOULD enter; in live mode we refuse rather than guess at the form.
    """
    if dry_run:
        it = ", ".join(f"{i['item_code']} {i['qty']}{i['unit']}@{i['rate']}" for i in payload["items"])
        print(f"   [dry-run] {payload['invoice_number']}  {payload['buyer_name']}  "
              f"₹{payload['total']:.2f}  [{it}]")
        return None
    # TODO(phase-1): navigate to New Sales Invoice → fill party (create if
    # missing) → add each line (item_code, qty, rate) → Save → read back the
    # voucher number. Wire this from the screen recording.
    raise NotImplementedError(
        "Vasy sales-invoice form-filling is not wired yet (Phase 1). "
        "Run with --dry-run, or provide the invoice-entry screen recording."
    )


# ── Reconciliation email ─────────────────────────────────────────────────────

def send_report(subject: str, body: str):
    if not (SMTP_USER and SMTP_PASSWORD and REPORT_EMAIL):
        print("   (reconciliation email skipped — SMTP/REPORT_EMAIL not configured)")
        return
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = REPORT_EMAIL
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASSWORD)
            s.sendmail(SMTP_USER, [e.strip() for e in REPORT_EMAIL.split(",")], msg.as_string())
        print("   reconciliation email sent")
    except Exception as e:
        print(f"   reconciliation email FAILED: {e}")


# ── Orchestration ────────────────────────────────────────────────────────────

def run(dry_run: bool, business_date: str | None, headed: bool, limit: int | None):
    db = SessionLocal()
    try:
        payloads = fetch_pending(db, business_date)
        if limit:
            payloads = payloads[:limit]
        scope = f"for {business_date}" if business_date else "(all dates)"
        print(f"Vasy sync {scope} — {len(payloads)} pending invoice(s). "
              f"{'DRY-RUN' if dry_run else 'LIVE'}.")
        if not payloads:
            send_report("Vasy sync: nothing to push", "No pending invoices.")
            return

        results = {"posted": [], "failed": [], "dry": 0}

        from playwright.sync_api import sync_playwright  # local import (office PC only)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not headed)
            ctx_kwargs = {}
            if os.path.exists(VASY_SESSION_FILE):
                ctx_kwargs["storage_state"] = VASY_SESSION_FILE
            context = browser.new_context(**ctx_kwargs)
            page = ensure_logged_in(context)
            print("Logged in to Vasy ✓")

            for p in payloads:
                try:
                    voucher = post_invoice_to_vasy(page, p, dry_run)
                    if dry_run:
                        results["dry"] += 1
                    else:
                        mark(db, p["invoice"], "posted", voucher_no=voucher)
                        results["posted"].append(f"{p['invoice_number']} → {voucher}")
                        print(f"   posted {p['invoice_number']} → {voucher}")
                except Exception as e:
                    if not dry_run:
                        mark(db, p["invoice"], "failed", error=str(e))
                    results["failed"].append(f"{p['invoice_number']}: {e}")
                    print(f"   FAILED {p['invoice_number']}: {e}")

            context.close()
            browser.close()

        total = float(sum(p["total"] for p in payloads))
        lines = [
            f"Vasy sync {scope} — {'DRY-RUN' if dry_run else 'LIVE'}",
            f"Pending processed : {len(payloads)}  (₹{total:,.2f})",
            f"Posted            : {len(results['posted'])}",
            f"Failed            : {len(results['failed'])}",
        ]
        if results["failed"]:
            lines += ["", "FAILURES:"] + [f"  - {f}" for f in results["failed"]]
        body = "\n".join(lines)
        print("\n" + body)
        send_report(
            f"Vasy sync: {len(results['posted'])} posted, {len(results['failed'])} failed",
            body,
        )
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser(description="Push OrdeRR invoices into Vasy ERP.")
    ap.add_argument("--live", action="store_true", help="actually create vouchers (default: dry-run)")
    ap.add_argument("--date", help="only this business_date (YYYY-MM-DD); default: all pending")
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    ap.add_argument("--limit", type=int, help="cap number of invoices (testing)")
    args = ap.parse_args()
    run(dry_run=not args.live, business_date=args.date, headed=args.headed, limit=args.limit)


if __name__ == "__main__":
    main()
