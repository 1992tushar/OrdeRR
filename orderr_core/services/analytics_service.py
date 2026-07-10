"""
Analytics service — read-only aggregations for the Analytics dashboard (Phase 1).

Phase 1 builds entirely on data already in OrdeRR (orders, invoices, actuals);
it has NO external dependency. Per the initiative's architecture principle,
Vasy ERP remains the money source-of-truth — so "sales" figures here are
OrdeRR's own (operational) *invoices*, and will be reconciled against / replaced
by mirrored Vasy revenue in Phase 2. Labels in the UI say so.

Conventions reused: orderr_core.dates.* for the business-date basis,
utils.fmt_qty for quantities. Money is rendered ₹ with en-IN (Indian) digit
grouping to match the app standard (client screens use toLocaleString('en-IN')).
All figures exclude cancelled orders and void invoices.
"""
import bisect
from datetime import date, timedelta
from statistics import median

from sqlalchemy import func
from sqlalchemy.orm import Session

from orderr_core.models.order import Order
from orderr_core.models.invoice import Invoice, InvoiceItem
from orderr_core.models.customer import Customer
from orderr_core.models.salesperson import Salesperson
from orderr_core.models.actuals import OrderItemActual
from orderr_core.models.customer_receipt import CustomerReceipt
from orderr_core.models.outstanding_snapshot import OutstandingSnapshot
from orderr_core.services.template_parser import erp_display_name
from orderr_core.utils import safe_list, fmt_qty


# ── formatting helpers (Indian grouping; stdlib-only, locale-independent) ──────

def _group_indian(int_str: str) -> str:
    """Group an integer string Indian-style: 1234567 -> '12,34,567'."""
    if len(int_str) <= 3:
        return int_str
    head, tail = int_str[:-3], int_str[-3:]
    # insert a comma every 2 digits from the right of the head
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    parts.insert(0, head)
    return ",".join(parts) + "," + tail


def fmt_inr(amount) -> str:
    """Format a rupee amount as '₹1,23,456' (no paise for whole numbers,
    2 decimals otherwise), with Indian digit grouping."""
    try:
        n = float(amount or 0)
    except (TypeError, ValueError):
        return "₹0"
    neg = n < 0
    n = abs(n)
    if n == int(n):
        body = _group_indian(str(int(n)))
    else:
        whole = int(n)
        frac = round(n - whole, 2)
        body = _group_indian(str(whole)) + f"{frac:.2f}"[1:]  # keep ".xx"
    return ("-₹" if neg else "₹") + body


def fmt_kg(qty) -> str:
    """Format a kg quantity with Indian grouping, dropping trailing .0."""
    try:
        n = float(qty or 0)
    except (TypeError, ValueError):
        return "0"
    if n == int(n):
        return _group_indian(str(int(n)))
    return _group_indian(str(int(n))) + f"{round(n - int(n), 1):.1f}"[1:]


# ── period helpers ─────────────────────────────────────────────────────────

def _period_bounds(today: date):
    """Return (key, label, sublabel, start_date, end_date) for the pulse
    periods, all inclusive of `today`.

      today  — just the current business date
      week   — rolling last 7 days (today-6 .. today)
      month  — calendar month-to-date (1st of month .. today)
    """
    return [
        ("today", "Today", today.strftime("%d %b"),
         today, today),
        ("week", "Last 7 Days", f"{(today - timedelta(days=6)).strftime('%d %b')} – {today.strftime('%d %b')}",
         today - timedelta(days=6), today),
        ("month", "Month to Date", today.strftime("%b %Y"),
         today.replace(day=1), today),
    ]


def _sales_for_range(db: Session, start: date, end: date):
    """(sales_rupees, sales_kg) from non-void invoices whose business_date is
    within [start, end]. Invoice.business_date is a Date column."""
    rupees = (
        db.query(func.coalesce(func.sum(Invoice.total), 0))
        .filter(Invoice.business_date >= start,
                Invoice.business_date <= end,
                Invoice.status != "void")
        .scalar()
    ) or 0

    kg = (
        db.query(func.coalesce(func.sum(InvoiceItem.quantity), 0))
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .filter(Invoice.business_date >= start,
                Invoice.business_date <= end,
                Invoice.status != "void",
                func.lower(InvoiceItem.unit) == "kg")
        .scalar()
    ) or 0

    return float(rupees), float(kg)


def _orders_for_range(db: Session, start: date, end: date):
    """(order_count, active_customer_count) from non-cancelled orders whose
    business_date (stored as 'YYYY-MM-DD' string) is within [start, end].
    ISO date strings sort lexicographically, so string comparison is safe."""
    s, e = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    base = db.query(Order).filter(
        Order.business_date >= s,
        Order.business_date <= e,
        Order.is_cancelled == False,  # noqa: E712
    )
    order_count = base.count()
    active_customers = (
        base.with_entities(Order.customer_phone)
        .distinct()
        .count()
    )
    return order_count, active_customers


def business_pulse(db: Session, today: date) -> dict:
    """P1-1 — Business Pulse KPI strip.

    Returns per-period sales (₹ & kg), order count and active-customer count
    for Today / Last 7 Days / Month-to-Date. Sales come from OrdeRR invoices
    (operational); orders/customers from the orders table.
    """
    periods = []
    for key, label, sublabel, start, end in _period_bounds(today):
        rupees, kg = _sales_for_range(db, start, end)
        orders, customers = _orders_for_range(db, start, end)
        periods.append({
            "key": key,
            "label": label,
            "sublabel": sublabel,
            "sales_rupees": rupees,
            "sales_rupees_fmt": fmt_inr(rupees),
            "sales_kg": kg,
            "sales_kg_fmt": fmt_kg(kg),
            "orders": orders,
            "active_customers": customers,
        })
    return {"periods": periods}


# ── P2 money helpers: latest / previous outstanding snapshot ───────────────

def _latest_two_snapshot_dates(db: Session):
    """The two most recent snapshot_dates present (latest, previous|None)."""
    dates = [d[0] for d in db.query(OutstandingSnapshot.snapshot_date)
             .distinct().order_by(OutstandingSnapshot.snapshot_date.desc()).limit(2).all()]
    latest = dates[0] if dates else None
    prev = dates[1] if len(dates) > 1 else None
    return latest, prev


def _outstanding_total(db: Session, snapshot_date):
    if snapshot_date is None:
        return 0.0
    return float(db.query(func.coalesce(func.sum(OutstandingSnapshot.closing), 0))
                 .filter(OutstandingSnapshot.snapshot_date == snapshot_date).scalar() or 0)


def _collections_for_range(db: Session, start: date, end: date):
    """(total, cash, bank) collections from receipts with receipt_date in range."""
    q = db.query(CustomerReceipt.mode, func.coalesce(func.sum(CustomerReceipt.amount), 0)) \
        .filter(CustomerReceipt.receipt_date >= start, CustomerReceipt.receipt_date <= end) \
        .group_by(CustomerReceipt.mode).all()
    total = cash = bank = 0.0
    for mode, amt in q:
        amt = float(amt or 0)
        total += amt
        if (mode or "").lower() == "cash":
            cash += amt
        elif (mode or "").lower() == "bank":
            bank += amt
    return total, cash, bank


def money_pulse(db: Session, today: date) -> dict:
    """P2-6 — money KPIs: collections per period (from receipts), billed vs
    collected (this month), and total outstanding + trend vs the previous
    snapshot. Returns has_data=False when no receipts/snapshots imported yet.
    """
    has_receipts = db.query(CustomerReceipt.id).first() is not None
    latest_snap, prev_snap = _latest_two_snapshot_dates(db)

    periods = []
    for key, label, sublabel, start, end in _period_bounds(today):
        total, cash, bank = _collections_for_range(db, start, end)
        billed, _ = _sales_for_range(db, start, end)   # OrdeRR invoices (operational)
        periods.append({
            "key": key, "label": label, "sublabel": sublabel,
            "collections": total, "collections_fmt": fmt_inr(total),
            "cash_fmt": fmt_inr(cash), "bank_fmt": fmt_inr(bank),
            "billed": billed, "billed_fmt": fmt_inr(billed),
            "net": billed - total, "net_fmt": fmt_inr(billed - total),
        })

    total_ar = _outstanding_total(db, latest_snap)
    prev_ar = _outstanding_total(db, prev_snap) if prev_snap else None
    ar_delta = (total_ar - prev_ar) if prev_ar is not None else None

    return {
        "has_data": has_receipts or latest_snap is not None,
        "periods": periods,
        "total_outstanding": total_ar,
        "total_outstanding_fmt": fmt_inr(total_ar),
        "outstanding_as_of": latest_snap.strftime("%d %b %Y") if latest_snap else None,
        "outstanding_delta_fmt": (fmt_inr(ar_delta) if ar_delta is not None else None),
        "outstanding_direction": (None if ar_delta is None else
                                  ("up" if ar_delta > 0 else "down" if ar_delta < 0 else "flat")),
        "prev_snap": prev_snap.strftime("%d %b %Y") if prev_snap else None,
    }


# ── P2-10 Collection velocity + P2-4 Unattributed receipts ─────────────────

def collections(db: Session, today: date, weeks: int = 12) -> dict:
    """P2-10 — weekly collection velocity (₹/week, cash vs bank) over the last
    `weeks` weeks, plus P2-4 unattributed receipts (customer_id NULL).

    Reconciliation: attributed + unattributed = grand total collected.
    """
    has_receipts = db.query(CustomerReceipt.id).first() is not None
    if not has_receipts:
        return {"has_data": False}

    window_start = today - timedelta(days=7 * weeks - 1)

    # weekly buckets (oldest → newest, each ending on a 7-day boundary at today)
    buckets = []
    for i in range(weeks - 1, -1, -1):
        wk_end = today - timedelta(days=7 * i)
        wk_start = wk_end - timedelta(days=6)
        buckets.append({"start": wk_start, "end": wk_end,
                        "label": wk_start.strftime("%d %b"),
                        "total": 0.0, "cash": 0.0, "bank": 0.0})

    rows = (
        db.query(CustomerReceipt.receipt_date, CustomerReceipt.mode,
                 func.coalesce(func.sum(CustomerReceipt.amount), 0))
        .filter(CustomerReceipt.receipt_date >= window_start,
                CustomerReceipt.receipt_date <= today)
        .group_by(CustomerReceipt.receipt_date, CustomerReceipt.mode).all()
    )
    for rdate, mode, amt in rows:
        amt = float(amt or 0)
        # find bucket
        idx = (rdate - window_start).days // 7
        if 0 <= idx < len(buckets):
            b = buckets[idx]
            b["total"] += amt
            if (mode or "").lower() == "cash":
                b["cash"] += amt
            elif (mode or "").lower() == "bank":
                b["bank"] += amt

    win_total = sum(b["total"] for b in buckets)
    win_cash = sum(b["cash"] for b in buckets)
    win_bank = sum(b["bank"] for b in buckets)

    series = [{"label": b["label"],
               "total": round(b["total"], 2), "total_fmt": fmt_inr(b["total"]),
               "cash": round(b["cash"], 2), "bank": round(b["bank"], 2)}
              for b in buckets]

    # P2-4 unattributed receipts (all time, customer_id NULL)
    un_rows = (
        db.query(CustomerReceipt.party_name,
                 func.count(CustomerReceipt.id),
                 func.coalesce(func.sum(CustomerReceipt.amount), 0))
        .filter(CustomerReceipt.customer_id == None)   # noqa: E711
        .group_by(CustomerReceipt.party_name)
        .order_by(func.sum(CustomerReceipt.amount).desc()).all()
    )
    unattributed = [{"party_name": p, "count": n, "total": round(float(t), 2),
                     "total_fmt": fmt_inr(t)} for p, n, t in un_rows]
    un_total = float(db.query(func.coalesce(func.sum(CustomerReceipt.amount), 0))
                     .filter(CustomerReceipt.customer_id == None).scalar() or 0)  # noqa: E711
    grand_total = float(db.query(func.coalesce(func.sum(CustomerReceipt.amount), 0)).scalar() or 0)

    return {
        "has_data": True,
        "weeks": weeks,
        "window_from": window_start.strftime("%d %b %Y"),
        "series": series,
        "win_total_fmt": fmt_inr(win_total),
        "win_cash_fmt": fmt_inr(win_cash),
        "win_bank_fmt": fmt_inr(win_bank),
        "win_cash_pct": round(win_cash / win_total * 100) if win_total else 0,
        "win_bank_pct": round(win_bank / win_total * 100) if win_total else 0,
        "avg_per_week_fmt": fmt_inr(win_total / weeks) if weeks else fmt_inr(0),
        "unattributed": unattributed,
        "unattributed_total_fmt": fmt_inr(un_total),
        "unattributed_count": len(unattributed),
        "attributed_total_fmt": fmt_inr(grand_total - un_total),
        "grand_total_fmt": fmt_inr(grand_total),
    }


# ── P2-8/9/11/12 Receivables (AR exposure, debtors, aging proxy) ───────────

def _receipt_stats_by_customer(db: Session):
    """{customer_id: (last_date, count, total)} over matched receipts."""
    rows = (
        db.query(CustomerReceipt.customer_id,
                 func.max(CustomerReceipt.receipt_date),
                 func.count(CustomerReceipt.id),
                 func.coalesce(func.sum(CustomerReceipt.amount), 0))
        .filter(CustomerReceipt.customer_id != None)   # noqa: E711
        .group_by(CustomerReceipt.customer_id).all()
    )
    return {cid: (last, cnt, float(tot)) for cid, last, cnt, tot in rows}


def receivables(db: Session, today: date) -> dict:
    """P2-8/9/11/12 — receivables from the latest outstanding snapshot fused
    with receipt history.

    Per debtor (closing > 0): outstanding, balance direction vs previous
    snapshot (P2-9), days-since-last-payment (P2-11), last-payment date, avg
    receipt. Plus total AR + top-10 concentration (P2-8) and an aging proxy
    bucketed by days-since-last-payment (P2-12 — NOT true invoice aging;
    labelled as such, since Vasy open-bills aren't exported).
    """
    latest_snap, prev_snap = _latest_two_snapshot_dates(db)
    if latest_snap is None:
        return {"has_data": False}

    snaps = db.query(OutstandingSnapshot).filter(
        OutstandingSnapshot.snapshot_date == latest_snap).all()
    prev_closing = {}
    if prev_snap:
        prev_closing = {s.party_key: float(s.closing) for s in db.query(OutstandingSnapshot)
                        .filter(OutstandingSnapshot.snapshot_date == prev_snap).all()}
    rstats = _receipt_stats_by_customer(db)
    sp_name = {s.id: s.name for s in db.query(Salesperson).all()}
    cust = {c.id: c for c in db.query(Customer).all()}

    rows = []
    total_ar = 0.0
    credit_total = 0.0
    areas, salespeople = set(), set()
    aging = {"0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0, "no-payment": 0.0}

    for s in snaps:
        closing = float(s.closing)
        if closing < 0:
            credit_total += closing
        if closing <= 0:
            continue
        total_ar += closing

        c = cust.get(s.customer_id) if s.customer_id else None
        area = (c.area if c else "") or ""
        sp = (sp_name.get(c.salesperson_id) if c and c.salesperson_id else "") or ""
        if area:
            areas.add(area)
        if sp:
            salespeople.add(sp)

        last_date = count = avg = None
        days_since = None
        if s.customer_id in rstats:
            last_date, count, tot = rstats[s.customer_id]
            avg = tot / count if count else 0
            if last_date:
                days_since = (today - last_date).days

        # direction vs previous snapshot
        if prev_snap and s.party_key in prev_closing:
            pc = prev_closing[s.party_key]
            direction = "up" if closing > pc else "down" if closing < pc else "flat"
        else:
            direction = None

        # aging proxy bucket (by payment recency)
        if days_since is None:
            aging["no-payment"] += closing
        elif days_since <= 30:
            aging["0-30"] += closing
        elif days_since <= 60:
            aging["31-60"] += closing
        elif days_since <= 90:
            aging["61-90"] += closing
        else:
            aging["90+"] += closing

        rows.append({
            "customer_id": s.customer_id,
            "name": (c.restaurant_name if c else None) or s.party_name,
            "area": area, "salesperson": sp,
            "outstanding": round(closing, 2), "outstanding_fmt": fmt_inr(closing),
            "direction": direction,
            "last_payment": last_date.strftime("%Y-%m-%d") if last_date else "",
            "last_payment_display": last_date.strftime("%d %b %Y") if last_date else "never",
            "days_since_payment": days_since if days_since is not None else "",
            "avg_receipt_fmt": fmt_inr(avg) if avg is not None else "—",
        })

    rows.sort(key=lambda r: r["outstanding"], reverse=True)
    top10 = sum(r["outstanding"] for r in rows[:10])
    top5 = sum(r["outstanding"] for r in rows[:5])

    return {
        "has_data": True,
        "as_of": latest_snap.strftime("%d %b %Y"),
        "has_trend": prev_snap is not None,
        "prev_as_of": prev_snap.strftime("%d %b %Y") if prev_snap else None,
        "rows": rows,
        "debtor_count": len(rows),
        "total_ar": round(total_ar, 2), "total_ar_fmt": fmt_inr(total_ar),
        "credit_total_fmt": fmt_inr(credit_total),
        "top10_fmt": fmt_inr(top10), "top5_fmt": fmt_inr(top5),
        "top10_pct": round(top10 / total_ar * 100, 1) if total_ar else 0,
        "top5_pct": round(top5 / total_ar * 100, 1) if total_ar else 0,
        "aging": [
            {"bucket": "0–30 days", "amount": round(aging["0-30"], 2), "amount_fmt": fmt_inr(aging["0-30"])},
            {"bucket": "31–60 days", "amount": round(aging["31-60"], 2), "amount_fmt": fmt_inr(aging["31-60"])},
            {"bucket": "61–90 days", "amount": round(aging["61-90"], 2), "amount_fmt": fmt_inr(aging["61-90"])},
            {"bucket": "90+ days", "amount": round(aging["90+"], 2), "amount_fmt": fmt_inr(aging["90+"])},
            {"bucket": "No payment on record", "amount": round(aging["no-payment"], 2), "amount_fmt": fmt_inr(aging["no-payment"])},
        ],
        "areas": sorted(areas), "salespeople": sorted(salespeople),
    }


# ── P1-2 Customer 360 (sales side) ─────────────────────────────────────────

# Windows offered in the C360 period selector. days=None → all time.
C360_WINDOWS = {"7": 7, "30": 30, "90": 90, "all": None}


def customer_360(db: Session, today: date, days=30) -> dict:
    """P1-2 — one sales-side row per customer.

    Per customer: revenue (₹, selected window, from OrdeRR invoices), order
    count in the window, days-since-last-order (recency, all-time), and a
    product-mix summary (top SKUs by quantity in the window, ERP display names).

    `days` bounds the revenue / order-count / mix window; None = all time.
    Recency is always all-time (a customer's true last order). Grouped queries
    keep this to a handful of round-trips regardless of customer count.
    """
    window_start = None if days is None else today - timedelta(days=days - 1)
    ws = window_start.strftime("%Y-%m-%d") if window_start else None
    today_str = today.strftime("%Y-%m-%d")

    # salesperson id → name
    sp_name = {s.id: s.name for s in db.query(Salesperson).all()}

    # ── P2-7 payment enrichment: outstanding + receipt behaviour per customer ──
    latest_snap, prev_snap = _latest_two_snapshot_dates(db)
    snap_now = {}
    if latest_snap:
        snap_now = {s.customer_id: float(s.closing) for s in db.query(OutstandingSnapshot)
                    .filter(OutstandingSnapshot.snapshot_date == latest_snap,
                            OutstandingSnapshot.customer_id != None).all()}  # noqa: E711
    snap_prev = {}
    if prev_snap:
        snap_prev = {s.customer_id: float(s.closing) for s in db.query(OutstandingSnapshot)
                     .filter(OutstandingSnapshot.snapshot_date == prev_snap,
                             OutstandingSnapshot.customer_id != None).all()}  # noqa: E711
    rstats = _receipt_stats_by_customer(db)
    has_money = bool(latest_snap) or bool(rstats)

    # revenue by phone within window (non-void invoices)
    rev_q = db.query(
        Invoice.customer_phone, func.coalesce(func.sum(Invoice.total), 0)
    ).filter(Invoice.status != "void", Invoice.business_date <= today)
    if window_start:
        rev_q = rev_q.filter(Invoice.business_date >= window_start)
    revenue = {ph: float(t) for ph, t in rev_q.group_by(Invoice.customer_phone).all()}

    # order count by phone within window (non-cancelled)
    oc_q = db.query(Order.customer_phone, func.count(Order.id)).filter(
        Order.is_cancelled == False,  # noqa: E712
        Order.business_date <= today_str,
    )
    if ws:
        oc_q = oc_q.filter(Order.business_date >= ws)
    orders_win = {ph: c for ph, c in oc_q.group_by(Order.customer_phone).all()}

    # last order date (all time, non-cancelled)
    last_q = db.query(
        Order.customer_phone, func.max(Order.business_date)
    ).filter(Order.is_cancelled == False).group_by(Order.customer_phone).all()  # noqa: E712
    last_order = {ph: d for ph, d in last_q if d}

    # product mix within window — parsed_items is JSON, so aggregate in Python
    mix_q = db.query(Order.customer_phone, Order.parsed_items).filter(
        Order.is_cancelled == False,  # noqa: E712
        Order.business_date <= today_str,
    )
    if ws:
        mix_q = mix_q.filter(Order.business_date >= ws)
    mix = {}  # phone → {erp_name: qty}
    for ph, parsed in mix_q.all():
        bucket = mix.setdefault(ph, {})
        for item in safe_list(parsed):
            if not isinstance(item, dict):
                continue
            name = erp_display_name(item.get("product", "") or "Unknown")
            try:
                qty = float(item.get("quantity") or 0)
            except (TypeError, ValueError):
                qty = 0
            bucket[name] = bucket.get(name, 0) + qty

    rows = []
    areas, salespeople = set(), set()
    for c in db.query(Customer).all():
        ph = c.phone_number
        rev = revenue.get(ph, 0.0)
        n_orders = orders_win.get(ph, 0)
        last = last_order.get(ph)
        recency_days = (today - date.fromisoformat(last)).days if last else None

        # top-3 products by quantity → summary
        prod_qty = mix.get(ph, {})
        top = sorted(prod_qty.items(), key=lambda kv: kv[1], reverse=True)[:3]
        mix_summary = ", ".join(
            f"{n} ({fmt_qty(q)})" for n, q in top
        ) if top else "—"

        sp = sp_name.get(c.salesperson_id) if c.salesperson_id else None
        area = c.area or None
        if area:
            areas.add(area)
        if sp:
            salespeople.add(sp)

        # payment enrichment
        outstanding = snap_now.get(c.id)
        bal_dir = None
        if c.id in snap_now and c.id in snap_prev:
            bal_dir = ("up" if snap_now[c.id] > snap_prev[c.id]
                       else "down" if snap_now[c.id] < snap_prev[c.id] else "flat")
        last_pay = last_pay_days = avg_receipt = None
        if c.id in rstats:
            lp, cnt, tot = rstats[c.id]
            avg_receipt = tot / cnt if cnt else 0
            if lp:
                last_pay = lp
                last_pay_days = (today - lp).days

        rows.append({
            "customer_id": c.id,
            "phone": ph or "",
            "name": c.restaurant_name or (ph or f"#{c.id}"),
            "area": area or "",
            "salesperson": sp or "",
            "is_active": bool(c.is_active),
            "revenue": rev,
            "revenue_fmt": fmt_inr(rev),
            "orders": n_orders,
            "last_order": last or "",
            "recency_days": recency_days if recency_days is not None else "",
            "mix_summary": mix_summary,
            # P2-7 payment columns
            "outstanding": round(outstanding, 2) if outstanding is not None else "",
            "outstanding_fmt": fmt_inr(outstanding) if outstanding is not None else "—",
            "balance_dir": bal_dir,
            "last_payment_display": last_pay.strftime("%d %b %Y") if last_pay else "—",
            "days_since_payment": last_pay_days if last_pay_days is not None else "",
            "avg_receipt_fmt": fmt_inr(avg_receipt) if avg_receipt is not None else "—",
        })

    # default sort: revenue desc, then most-recent
    rows.sort(key=lambda r: (r["revenue"], -(r["recency_days"] if isinstance(r["recency_days"], int) else 10**9)), reverse=True)

    return {
        "rows": rows,
        "areas": sorted(areas),
        "salespeople": sorted(salespeople),
        "days": days,
        "window_label": "All time" if days is None else f"Last {days} days",
        "has_money": has_money,   # whether Vasy payment columns have data
    }


# ── P1-3 Customer detail ───────────────────────────────────────────────────

def _month_key(d: date) -> str:
    return d.strftime("%Y-%m")


def _last_n_months(today: date, n: int = 12):
    """List of the last n month keys ('YYYY-MM'), oldest → newest, ending at
    today's month."""
    ym = today.year * 12 + (today.month - 1)
    keys = []
    for i in range(n - 1, -1, -1):
        m = ym - i
        keys.append(f"{m // 12:04d}-{(m % 12) + 1:02d}")
    return keys


def _month_label(key: str) -> str:
    y, m = key.split("-")
    return date(int(y), int(m), 1).strftime("%b %y")


def customer_detail(db: Session, customer_id: int, today: date, months: int = 12):
    """P1-3 — full sales-side detail for one customer.

    Returns None if the customer id is unknown. Otherwise: header/KPIs, a
    12-month revenue trend (from OrdeRR invoices, gaps filled with 0), an
    all-time product mix (ERP names), and order + invoice history.
    """
    customer = db.query(Customer).get(customer_id)
    if customer is None:
        return None

    phone = customer.phone_number
    sp = None
    if customer.salesperson_id:
        sp_row = db.query(Salesperson).get(customer.salesperson_id)
        sp = sp_row.name if sp_row else None

    # ── invoices (revenue) ──
    invoices = []
    monthly = {}  # 'YYYY-MM' → revenue
    total_revenue = 0.0
    if phone:
        inv_rows = (
            db.query(Invoice)
            .filter(Invoice.customer_phone == phone, Invoice.status != "void")
            .order_by(Invoice.business_date.desc())
            .all()
        )
        for inv in inv_rows:
            amt = float(inv.total or 0)
            total_revenue += amt
            monthly[_month_key(inv.business_date)] = monthly.get(_month_key(inv.business_date), 0.0) + amt
            invoices.append({
                "number": inv.invoice_number,
                "date": inv.business_date.strftime("%Y-%m-%d"),
                "date_display": inv.business_date.strftime("%d %b %Y"),
                "total": amt,
                "total_fmt": fmt_inr(amt),
                "status": inv.status,
            })

    # ── revenue trend (last N months, gaps = 0) ──
    trend = [{"key": k, "label": _month_label(k), "revenue": round(monthly.get(k, 0.0), 2)}
             for k in _last_n_months(today, months)]

    # ── orders + product mix ──
    orders = []
    mix = {}  # (erp_name, unit) → qty
    last_order = None
    first_order = None
    if phone:
        ord_rows = (
            db.query(Order)
            .filter(Order.customer_phone == phone, Order.is_cancelled == False)  # noqa: E712
            .order_by(Order.business_date.desc())
            .all()
        )
        for o in ord_rows:
            items = []
            for it in safe_list(o.parsed_items):
                if not isinstance(it, dict):
                    continue
                name = erp_display_name(it.get("product", "") or "Unknown")
                unit = (it.get("unit", "kg") or "kg").lower()
                try:
                    qty = float(it.get("quantity") or 0)
                except (TypeError, ValueError):
                    qty = 0
                items.append({"name": name, "qty": fmt_qty(qty), "unit": unit})
                mix[(name, unit)] = mix.get((name, unit), 0) + qty
            orders.append({
                "date": o.business_date or "",
                "date_display": (date.fromisoformat(o.business_date).strftime("%d %b %Y")
                                 if o.business_date else ""),
                "status": o.status or "",
                "line_items": items,
            })
        dates = [o.business_date for o in ord_rows if o.business_date]
        if dates:
            last_order, first_order = max(dates), min(dates)

    mix_list = [{"name": n, "unit": u, "qty": fmt_qty(q), "qty_raw": q}
                for (n, u), q in sorted(mix.items(), key=lambda kv: kv[1], reverse=True)]

    # ── P2-14 payments & balance history ──
    receipts = []
    collected = 0.0
    rec_rows = (db.query(CustomerReceipt)
                .filter(CustomerReceipt.customer_id == customer.id)
                .order_by(CustomerReceipt.receipt_date.desc(),
                          CustomerReceipt.receipt_no.desc()).all())
    for r in rec_rows:
        amt = float(r.amount or 0)
        collected += amt
        receipts.append({
            "receipt_no": r.receipt_no,
            "date_display": r.receipt_date.strftime("%d %b %Y") if r.receipt_date else "",
            "mode": (r.mode or "").title(),
            "amount_fmt": fmt_inr(amt),
            "status": (r.status or "").title(),
        })
    snap_rows = (db.query(OutstandingSnapshot)
                 .filter(OutstandingSnapshot.customer_id == customer.id)
                 .order_by(OutstandingSnapshot.snapshot_date).all())
    balance_trend = [{"date": s.snapshot_date.strftime("%Y-%m-%d"),
                      "label": s.snapshot_date.strftime("%d %b"),
                      "closing": round(float(s.closing), 2)} for s in snap_rows]
    current_outstanding = float(snap_rows[-1].closing) if snap_rows else None
    last_payment = rec_rows[0].receipt_date if rec_rows else None
    n_rec = len(rec_rows)

    recency_days = (today - date.fromisoformat(last_order)).days if last_order else None
    n_invoices = len(invoices)
    avg_order_value = (total_revenue / n_invoices) if n_invoices else 0.0

    return {
        "customer": {
            "id": customer.id,
            "name": customer.restaurant_name or (phone or f"#{customer.id}"),
            "phone": phone or "",
            "area": customer.area or "",
            "salesperson": sp or "",
            "is_active": bool(customer.is_active),
            "outstanding_fmt": fmt_inr(customer.outstanding or 0),
        },
        "kpis": {
            "total_revenue_fmt": fmt_inr(total_revenue),
            "orders": len(orders),
            "invoices": n_invoices,
            "avg_order_value_fmt": fmt_inr(avg_order_value),
            "recency_days": recency_days if recency_days is not None else "",
            "last_order_display": (date.fromisoformat(last_order).strftime("%d %b %Y")
                                   if last_order else "never"),
            "first_order_display": (date.fromisoformat(first_order).strftime("%d %b %Y")
                                    if first_order else "—"),
        },
        "trend": trend,
        "mix": mix_list,
        "orders": orders,
        "invoices": invoices,
        "payments": {
            "has_data": bool(rec_rows) or bool(snap_rows),
            "current_outstanding_fmt": fmt_inr(current_outstanding) if current_outstanding is not None else "—",
            "collected_fmt": fmt_inr(collected),
            "receipt_count": n_rec,
            "avg_receipt_fmt": fmt_inr(collected / n_rec) if n_rec else "—",
            "last_payment_display": last_payment.strftime("%d %b %Y") if last_payment else "never",
            "days_since_payment": (today - last_payment).days if last_payment else "",
            "receipts": receipts,
            "balance_trend": balance_trend,
        },
    }


# ── shared: distinct order dates per customer phone ────────────────────────

def _order_dates_by_phone(db: Session, upto: date):
    """{phone: [date, …] sorted ascending} of DISTINCT non-cancelled order
    business-dates up to and including `upto`. Malformed date strings skipped."""
    rows = (
        db.query(Order.customer_phone, Order.business_date)
        .filter(Order.is_cancelled == False,  # noqa: E712
                Order.business_date != None,   # noqa: E711
                Order.business_date <= upto.strftime("%Y-%m-%d"))
        .all()
    )
    out = {}
    for ph, ds in rows:
        try:
            d = date.fromisoformat(ds)
        except (TypeError, ValueError):
            continue
        out.setdefault(ph, set()).add(d)
    return {ph: sorted(s) for ph, s in out.items()}


# ── P1-4 Silent-churn detector ─────────────────────────────────────────────

def churn_risk(db: Session, today: date, min_orders: int = 3,
               ratio_threshold: float = 2.0, floor_days: int = 3) -> dict:
    """P1-4 — customers overdue relative to their OWN ordering cadence.

    Cadence = median gap (days) between a customer's distinct order dates.
    A customer is flagged when days-since-last-order exceeds
    `ratio_threshold` × cadence (and at least `floor_days`, to avoid
    daily-order jitter). Needs `min_orders` distinct order days to derive a
    stable cadence; customers with less history are excluded (no reliable
    baseline — surfaced separately by other views, not guessed here).

    ratio = days_since_last / cadence. Severity: ≥3 high, ≥2 medium.
    Returns only flagged customers, most-overdue (by ratio) first.
    """
    dates_by_phone = _order_dates_by_phone(db, today)
    sp_name = {s.id: s.name for s in db.query(Salesperson).all()}
    customers = {c.phone_number: c for c in db.query(Customer).all() if c.phone_number}

    rows = []
    areas, salespeople = set(), set()
    for phone, dates in dates_by_phone.items():
        cust = customers.get(phone)
        if cust is None or len(dates) < min_orders:
            continue
        gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
        gaps = [g for g in gaps if g > 0] or gaps  # guard all-zero
        cadence = median(gaps) if gaps else None
        if not cadence or cadence <= 0:
            continue
        last = dates[-1]
        days_since = (today - last).days
        ratio = days_since / cadence
        if days_since < floor_days or ratio < ratio_threshold:
            continue

        severity = "high" if ratio >= 3 else "medium"
        sp = sp_name.get(cust.salesperson_id) if cust.salesperson_id else ""
        area = cust.area or ""
        if area:
            areas.add(area)
        if sp:
            salespeople.add(sp)

        rows.append({
            "customer_id": cust.id,
            "name": cust.restaurant_name or phone,
            "phone": phone,
            "area": area,
            "salesperson": sp,
            "orders": len(dates),
            "cadence_days": round(cadence, 1),
            "days_since_last": days_since,
            "last_order": last.strftime("%Y-%m-%d"),
            "last_order_display": last.strftime("%d %b %Y"),
            "ratio": round(ratio, 2),
            "severity": severity,
        })

    rows.sort(key=lambda r: r["ratio"], reverse=True)
    return {
        "rows": rows,
        "areas": sorted(areas),
        "salespeople": sorted(salespeople),
        "high": sum(1 for r in rows if r["severity"] == "high"),
        "medium": sum(1 for r in rows if r["severity"] == "medium"),
        "params": {"min_orders": min_orders, "ratio_threshold": ratio_threshold, "floor_days": floor_days},
    }


# ── P1-5 Revenue trend + MoM ───────────────────────────────────────────────

def _pct_change(curr: float, prev: float):
    """MoM % change; None when there is no prior base (prev == 0)."""
    if prev == 0:
        return None
    return round((curr - prev) / prev * 100, 1)


def revenue_trends(db: Session, today: date, months: int = 12) -> dict:
    """P1-5 — overall revenue over time (with MoM %) plus a per-customer
    current-vs-previous-month comparison.

    Revenue is OrdeRR invoice totals (non-void), bucketed by business-month.
    """
    keys = _last_n_months(today, months)
    key_set = set(keys)

    # overall monthly totals + per-(phone,month) totals in one pass
    monthly = {k: 0.0 for k in keys}
    per_cust = {}  # phone → {month_key: revenue}
    scan_start = date(int(keys[0][:4]), int(keys[0][5:]), 1)

    inv_rows = (
        db.query(Invoice.customer_phone, Invoice.business_date, Invoice.total)
        .filter(Invoice.status != "void",
                Invoice.business_date >= scan_start,
                Invoice.business_date <= today)
        .all()
    )
    for phone, bdate, total in inv_rows:
        mk = _month_key(bdate)
        amt = float(total or 0)
        if mk in monthly:
            monthly[mk] += amt
        per_cust.setdefault(phone, {})[mk] = per_cust.get(phone, {}).get(mk, 0.0) + amt

    # overall trend with MoM
    trend = []
    prev_rev = None
    for k in keys:
        rev = round(monthly[k], 2)
        mom = _pct_change(rev, prev_rev) if prev_rev is not None else None
        trend.append({
            "key": k, "label": _month_label(k),
            "revenue": rev, "revenue_fmt": fmt_inr(rev), "mom_pct": mom,
        })
        prev_rev = rev

    curr_key = keys[-1]
    prev_key = keys[-2] if len(keys) >= 2 else None

    # per-customer curr vs prev month
    sp_name = {s.id: s.name for s in db.query(Salesperson).all()}
    customers = {c.phone_number: c for c in db.query(Customer).all() if c.phone_number}
    cust_rows = []
    areas, salespeople = set(), set()
    for phone, by_month in per_cust.items():
        curr = round(by_month.get(curr_key, 0.0), 2)
        prev = round(by_month.get(prev_key, 0.0), 2) if prev_key else 0.0
        if curr == 0 and prev == 0:
            continue
        cust = customers.get(phone)
        name = (cust.restaurant_name if cust else None) or phone or "(unattributed)"
        area = (cust.area if cust else "") or ""
        sp = (sp_name.get(cust.salesperson_id) if cust and cust.salesperson_id else "") or ""
        if area:
            areas.add(area)
        if sp:
            salespeople.add(sp)
        pct = _pct_change(curr, prev)
        if prev == 0 and curr > 0:
            direction = "new"
        elif curr == 0 and prev > 0:
            direction = "lost"
        elif curr > prev:
            direction = "up"
        elif curr < prev:
            direction = "down"
        else:
            direction = "flat"
        cust_rows.append({
            "customer_id": cust.id if cust else None,
            "name": name, "area": area, "salesperson": sp,
            "curr": curr, "curr_fmt": fmt_inr(curr),
            "prev": prev, "prev_fmt": fmt_inr(prev),
            "delta": round(curr - prev, 2), "delta_fmt": fmt_inr(curr - prev),
            "pct": pct, "direction": direction,
        })

    cust_rows.sort(key=lambda r: r["delta"], reverse=True)

    curr_total = monthly[curr_key]
    prev_total = monthly[prev_key] if prev_key else 0.0

    return {
        "trend": trend,
        "current_label": _month_label(curr_key),
        "prev_label": _month_label(prev_key) if prev_key else "",
        "current_revenue_fmt": fmt_inr(curr_total),
        "prev_revenue_fmt": fmt_inr(prev_total),
        "current_mom_pct": _pct_change(curr_total, prev_total),
        "customers": cust_rows,
        "areas": sorted(areas),
        "salespeople": sorted(salespeople),
    }


# ── P1-6 New vs lost customers ─────────────────────────────────────────────

def new_vs_lost(db: Session, today: date, months: int = 12) -> dict:
    """P1-6 — monthly customer acquisitions vs attrition.

    Acquisition[m] = customers whose FIRST order month == m.
    Attrition[m]   = customers whose LAST order month == m, excluding the
                     current month (a customer can't be declared lost in the
                     month still in progress — they may yet order).
    Derived from Order.business_date history (non-cancelled).
    """
    rows = (
        db.query(Order.customer_phone,
                 func.min(Order.business_date), func.max(Order.business_date))
        .filter(Order.is_cancelled == False,        # noqa: E712
                Order.business_date != None)        # noqa: E711
        .group_by(Order.customer_phone)
        .all()
    )

    keys = _last_n_months(today, months)
    key_set = set(keys)
    curr_key = _month_key(today)
    acq = {k: 0 for k in keys}
    att = {k: 0 for k in keys}

    for phone, first_ds, last_ds in rows:
        try:
            fm = _month_key(date.fromisoformat(first_ds))
            lm = _month_key(date.fromisoformat(last_ds))
        except (TypeError, ValueError):
            continue
        if fm in key_set:
            acq[fm] += 1
        if lm in key_set and lm != curr_key:
            att[lm] += 1

    series = [{"key": k, "label": _month_label(k), "new": acq[k], "lost": att[k],
               "net": acq[k] - att[k]} for k in keys]

    return {
        "series": series,
        "total_new": sum(acq.values()),
        "total_lost": sum(att.values()),
        "net": sum(acq.values()) - sum(att.values()),
    }


# ── P1-7 Product mix (value + volume) ──────────────────────────────────────

def product_mix(db: Session, today: date, days=30) -> dict:
    """P1-7 — per-SKU billed value (₹) and volume (kg / nos) over a window,
    from invoice items (the billed truth). ERP display names; % of total value.
    `days` bounds the window; None = all time.
    """
    window_start = None if days is None else today - timedelta(days=days - 1)

    q = (
        db.query(InvoiceItem.product, InvoiceItem.unit,
                 func.coalesce(func.sum(InvoiceItem.quantity), 0),
                 func.coalesce(func.sum(InvoiceItem.amount), 0))
        .join(Invoice, InvoiceItem.invoice_id == Invoice.id)
        .filter(Invoice.status != "void", Invoice.business_date <= today)
    )
    if window_start:
        q = q.filter(Invoice.business_date >= window_start)
    q = q.group_by(InvoiceItem.product, InvoiceItem.unit)

    agg = {}  # erp_name → {kg, nos, value}
    for product, unit, qty, amount in q.all():
        name = erp_display_name(product or "Unknown")
        u = (unit or "kg").lower()
        row = agg.setdefault(name, {"kg": 0.0, "nos": 0.0, "value": 0.0})
        if u == "nos":
            row["nos"] += float(qty or 0)
        else:
            row["kg"] += float(qty or 0)
        row["value"] += float(amount or 0)

    total_value = sum(r["value"] for r in agg.values()) or 0.0
    rows = []
    for name, r in agg.items():
        pct = round(r["value"] / total_value * 100, 1) if total_value else 0.0
        rows.append({
            "product": name,
            "kg": r["kg"], "kg_fmt": fmt_kg(r["kg"]),
            "nos": r["nos"], "nos_fmt": fmt_qty(r["nos"]),
            "value": round(r["value"], 2), "value_fmt": fmt_inr(r["value"]),
            "pct": pct,
        })
    rows.sort(key=lambda x: x["value"], reverse=True)

    return {
        "rows": rows,
        "total_value": round(total_value, 2),
        "total_value_fmt": fmt_inr(total_value),
        "days": days,
        "window_label": "All time" if days is None else f"Last {days} days",
    }


# ── P1-8 Demand trend (per SKU, over time) ─────────────────────────────────

def demand_trend(db: Session, today: date, months: int = 12) -> dict:
    """P1-8 — ordered demand per SKU per month (production-planning signal),
    from order parsed_items (what customers asked for, not what was billed).

    Returns month labels, and per-SKU monthly quantity series. Quantity is
    summed across units (SKUs are single-unit in practice); the dominant unit
    is reported per SKU for labelling.
    """
    keys = _last_n_months(today, months)
    scan_start = date(int(keys[0][:4]), int(keys[0][5:]), 1)
    key_set = set(keys)

    rows = (
        db.query(Order.business_date, Order.parsed_items)
        .filter(Order.is_cancelled == False,                 # noqa: E712
                Order.business_date != None,                 # noqa: E711
                Order.business_date >= scan_start.strftime("%Y-%m-%d"),
                Order.business_date <= today.strftime("%Y-%m-%d"))
        .all()
    )

    # sku → {month_key: qty}, sku → unit counts
    series = {}
    unit_votes = {}
    for bdate, parsed in rows:
        try:
            mk = _month_key(date.fromisoformat(bdate))
        except (TypeError, ValueError):
            continue
        if mk not in key_set:
            continue
        for it in safe_list(parsed):
            if not isinstance(it, dict):
                continue
            name = erp_display_name(it.get("product", "") or "Unknown")
            unit = (it.get("unit", "kg") or "kg").lower()
            try:
                qty = float(it.get("quantity") or 0)
            except (TypeError, ValueError):
                qty = 0
            series.setdefault(name, {}).setdefault(mk, 0.0)
            series[name][mk] += qty
            uv = unit_votes.setdefault(name, {})
            uv[unit] = uv.get(unit, 0) + 1

    skus = []
    for name, by_month in series.items():
        months_series = [{"key": k, "label": _month_label(k),
                          "qty": round(by_month.get(k, 0.0), 2)} for k in keys]
        total = sum(by_month.values())
        unit = max(unit_votes.get(name, {"kg": 1}).items(), key=lambda kv: kv[1])[0]
        skus.append({
            "product": name,
            "unit": unit,
            "total": round(total, 2),
            "total_fmt": fmt_qty(total),
            "series": months_series,
        })
    skus.sort(key=lambda s: s["total"], reverse=True)

    return {
        "months": [{"key": k, "label": _month_label(k)} for k in keys],
        "skus": skus,
    }


# ── P1-12 Excel export ─────────────────────────────────────────────────────

def build_xlsx(sheet_name: str, headers: list, rows: list) -> bytes:
    """Serialise headers + rows to .xlsx bytes (openpyxl). Bold header row,
    auto-ish column widths. Numbers stay numbers so the sheet is usable."""
    import io
    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = (sheet_name or "Sheet1")[:31]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)
    for row in rows:
        ws.append(row)
    ws.freeze_panes = "A2"
    for i, h in enumerate(headers, start=1):
        width = max(len(str(h)), *(len(str(r[i - 1])) for r in rows)) if rows else len(str(h))
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = min(48, width + 2)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_dataset(db: Session, today: date, name: str, days=None):
    """Return (filename, sheet_name, headers, rows) for an analytics list, so
    the export route can serialise it. Mirrors what each screen shows.
    Unknown name → None.
    """
    tag = today.strftime("%Y%m%d")

    if name == "customers":
        data = customer_360(db, today, days=days if days is not None else 30)
        headers = ["Customer", "Phone", "Area", "Salesperson", "Active",
                   "Revenue (INR)", "Outstanding (INR)", "Orders", "Last order",
                   "Days since order", "Last payment", "Days since payment",
                   "Avg receipt", "Product mix"]
        rows = [[r["name"], r["phone"], r["area"], r["salesperson"],
                 "Yes" if r["is_active"] else "No", r["revenue"], r["outstanding"],
                 r["orders"], r["last_order"], r["recency_days"],
                 r["last_payment_display"], r["days_since_payment"],
                 r["avg_receipt_fmt"], r["mix_summary"]] for r in data["rows"]]
        return (f"customers_{tag}.xlsx", "Customer 360", headers, rows)

    if name == "churn":
        data = churn_risk(db, today)
        headers = ["Customer", "Phone", "Area", "Salesperson", "Orders",
                   "Cadence (days)", "Days since last", "Last order", "Overdue x", "Severity"]
        rows = [[r["name"], r["phone"], r["area"], r["salesperson"], r["orders"],
                 r["cadence_days"], r["days_since_last"], r["last_order"],
                 r["ratio"], r["severity"]] for r in data["rows"]]
        return (f"churn_{tag}.xlsx", "Churn risk", headers, rows)

    if name == "revenue":
        data = revenue_trends(db, today)
        headers = ["Customer", "Area", "Salesperson",
                   f"{data['prev_label']} (INR)", f"{data['current_label']} (INR)",
                   "Delta (INR)", "MoM %", "Movement"]
        rows = [[r["name"], r["area"], r["salesperson"], r["prev"], r["curr"],
                 r["delta"], ("" if r["pct"] is None else r["pct"]), r["direction"]]
                for r in data["customers"]]
        return (f"revenue_mom_{tag}.xlsx", "Revenue MoM", headers, rows)

    if name == "products":
        data = product_mix(db, today, days=days if days is not None else 30)
        headers = ["SKU", "kg", "nos", "Value (INR)", "% of value"]
        rows = [[r["product"], r["kg"], r["nos"], r["value"], r["pct"]] for r in data["rows"]]
        return (f"product_mix_{tag}.xlsx", "Product mix", headers, rows)

    if name == "team":
        data = team_performance(db, today, days=days if days is not None else 30)
        headers = ["Group", "Name", "Revenue (INR)", "Collected (INR)",
                   "Outstanding (INR)", "Orders", "Active", "Portfolio"]
        rows = ([["Salesperson", r["name"], r["revenue"], r["collected"], r["outstanding"], r["orders"], r["active"], r["portfolio"]]
                 for r in data["by_salesperson"]] +
                [["Area", r["name"], r["revenue"], r["collected"], r["outstanding"], r["orders"], r["active"], r["portfolio"]]
                 for r in data["by_area"]])
        return (f"team_area_{tag}.xlsx", "Team & area", headers, rows)

    if name == "receivables":
        data = receivables(db, today)
        if not data.get("has_data"):
            return (f"receivables_{tag}.xlsx", "Receivables", ["Note"], [["No outstanding snapshot imported yet."]])
        headers = ["Customer", "Area", "Salesperson", "Outstanding (INR)", "Trend",
                   "Days since payment", "Last payment", "Avg receipt"]
        rows = [[r["name"], r["area"], r["salesperson"], r["outstanding"],
                 r["direction"] or "", r["days_since_payment"], r["last_payment"],
                 r["avg_receipt_fmt"]] for r in data["rows"]]
        return (f"receivables_{tag}.xlsx", "Receivables", headers, rows)

    if name == "rfm":
        data = rfm(db, today)
        headers = ["Customer", "Area", "Salesperson", "Recency (days)",
                   "Frequency", "Monetary (INR)", "R", "F", "M", "Segment"]
        rows = [[r["name"], r["area"], r["salesperson"], r["recency_days"],
                 r["frequency"], r["monetary"], r["R"], r["F"], r["M"], r["segment"]]
                for r in data["rows"]]
        return (f"rfm_{tag}.xlsx", "RFM", headers, rows)

    return None


# ── P1-13 RFM segmentation ─────────────────────────────────────────────────

def _quintile_scores(pairs, higher_better=True):
    """Map each (id, value) to a 1–5 score by rank-based quintile.
    higher_better=False inverts (used for recency, where fewer days = better).
    Robust to ties and tiny samples."""
    vals = sorted(v for _, v in pairs)
    n = len(vals) or 1
    out = {}
    for cid, v in pairs:
        rank = bisect.bisect_right(vals, v)        # count of values ≤ v (1..n)
        score = min(5, max(1, int((rank / n - 1e-9) * 5) + 1))
        out[cid] = (6 - score) if not higher_better else score
    return out


def _rfm_segment(r: int, f: int, m: int) -> str:
    """Name a segment from R/F/M scores (1–5). Standard RFM matrix, simplified
    and explainable."""
    fm = round((f + m) / 2)
    if r >= 4 and fm >= 4:
        return "Champions"
    if r >= 3 and fm >= 4:
        return "Loyal"
    if r >= 4 and fm >= 2:
        return "Potential loyalist"
    if r >= 4 and fm < 2:
        return "New"
    if r <= 2 and fm >= 4:
        return "Can't lose"
    if r <= 2 and fm == 3:
        return "At risk"
    if r <= 2 and fm <= 2:
        return "Hibernating"
    return "Needs attention"


# display order + accent for segments
RFM_SEGMENTS = [
    ("Champions", "good"), ("Loyal", "good"), ("Potential loyalist", "blue"),
    ("New", "blue"), ("Needs attention", "amber"), ("At risk", "amber"),
    ("Can't lose", "red"), ("Hibernating", "red"),
]


def rfm(db: Session, today: date) -> dict:
    """P1-13 — Recency/Frequency/Monetary scoring & segmentation.

    Recency = days since last order (all-time), Frequency = lifetime order
    count, Monetary = lifetime invoice revenue. Each scored 1–5 by quintile
    across the active base; a named segment is derived from the trio.
    Only customers with at least one order are scored.
    """
    dates_by_phone = _order_dates_by_phone(db, today)
    # frequency = lifetime non-cancelled order count (not distinct days)
    freq_rows = (
        db.query(Order.customer_phone, func.count(Order.id))
        .filter(Order.is_cancelled == False)               # noqa: E712
        .group_by(Order.customer_phone).all()
    )
    freq = {ph: c for ph, c in freq_rows}
    rev_rows = (
        db.query(Invoice.customer_phone, func.coalesce(func.sum(Invoice.total), 0))
        .filter(Invoice.status != "void")
        .group_by(Invoice.customer_phone).all()
    )
    revenue = {ph: float(t) for ph, t in rev_rows}
    customers = {c.phone_number: c for c in db.query(Customer).all() if c.phone_number}
    sp_name = {s.id: s.name for s in db.query(Salesperson).all()}

    base = []  # (phone, recency_days, frequency, monetary)
    for phone, dates in dates_by_phone.items():
        if phone not in customers or not dates:
            continue
        recency = (today - dates[-1]).days
        base.append((phone, recency, freq.get(phone, 0), revenue.get(phone, 0.0)))

    if not base:
        return {"rows": [], "segments": [], "areas": [], "salespeople": [], "total": 0}

    r_scores = _quintile_scores([(p, rec) for p, rec, _, _ in base], higher_better=False)
    f_scores = _quintile_scores([(p, fr) for p, _, fr, _ in base], higher_better=True)
    m_scores = _quintile_scores([(p, mo) for p, _, _, mo in base], higher_better=True)

    rows = []
    areas, salespeople = set(), set()
    seg_counts = {}
    for phone, recency, frequency, monetary in base:
        cust = customers[phone]
        R, F, M = r_scores[phone], f_scores[phone], m_scores[phone]
        seg = _rfm_segment(R, F, M)
        seg_counts[seg] = seg_counts.get(seg, 0) + 1
        sp = (sp_name.get(cust.salesperson_id) if cust.salesperson_id else "") or ""
        area = cust.area or ""
        if area:
            areas.add(area)
        if sp:
            salespeople.add(sp)
        rows.append({
            "customer_id": cust.id,
            "name": cust.restaurant_name or phone,
            "area": area, "salesperson": sp,
            "recency_days": recency, "frequency": frequency,
            "monetary": round(monetary, 2), "monetary_fmt": fmt_inr(monetary),
            "R": R, "F": F, "M": M, "rfm": f"{R}{F}{M}", "segment": seg,
        })
    rows.sort(key=lambda x: (x["R"] + x["F"] + x["M"], x["monetary"]), reverse=True)

    segments = [{"name": name, "accent": accent, "count": seg_counts.get(name, 0)}
                for name, accent in RFM_SEGMENTS if seg_counts.get(name, 0)]

    return {
        "rows": rows,
        "segments": segments,
        "areas": sorted(areas),
        "salespeople": sorted(salespeople),
        "total": len(rows),
    }


# ── P1-11 Salesperson & area performance (sales) ───────────────────────────

def team_performance(db: Session, today: date, days=30) -> dict:
    """P1-11 — sales rolled up by salesperson and by area.

    Per group: revenue (₹, window, from invoices), order count (window),
    active customers (ordered in window) and portfolio size (assigned
    customers). `days` bounds revenue/orders; None = all time.
    Customers with no salesperson/area roll into an "Unassigned" bucket.
    """
    window_start = None if days is None else today - timedelta(days=days - 1)
    ws = window_start.strftime("%Y-%m-%d") if window_start else None
    today_str = today.strftime("%Y-%m-%d")

    rev_q = db.query(
        Invoice.customer_phone, func.coalesce(func.sum(Invoice.total), 0)
    ).filter(Invoice.status != "void", Invoice.business_date <= today)
    if window_start:
        rev_q = rev_q.filter(Invoice.business_date >= window_start)
    revenue = {ph: float(t) for ph, t in rev_q.group_by(Invoice.customer_phone).all()}

    oc_q = db.query(Order.customer_phone, func.count(Order.id)).filter(
        Order.is_cancelled == False, Order.business_date <= today_str)  # noqa: E712
    if ws:
        oc_q = oc_q.filter(Order.business_date >= ws)
    orders_win = {ph: c for ph, c in oc_q.group_by(Order.customer_phone).all()}

    sp_name = {s.id: s.name for s in db.query(Salesperson).all()}

    # collections (receipts in window) by customer_id
    col_q = db.query(CustomerReceipt.customer_id, func.coalesce(func.sum(CustomerReceipt.amount), 0)) \
        .filter(CustomerReceipt.customer_id != None, CustomerReceipt.receipt_date <= today)  # noqa: E711
    if window_start:
        col_q = col_q.filter(CustomerReceipt.receipt_date >= window_start)
    collected = {cid: float(t) for cid, t in col_q.group_by(CustomerReceipt.customer_id).all()}

    # current outstanding (latest snapshot) by customer_id
    latest_snap, _ = _latest_two_snapshot_dates(db)
    outstanding = {}
    if latest_snap:
        outstanding = {s.customer_id: float(s.closing) for s in db.query(OutstandingSnapshot)
                       .filter(OutstandingSnapshot.snapshot_date == latest_snap,
                               OutstandingSnapshot.customer_id != None).all()}  # noqa: E711

    def _bucket():
        return {"revenue": 0.0, "orders": 0, "active": 0, "portfolio": 0,
                "collected": 0.0, "outstanding": 0.0}
    by_sp, by_area = {}, {}

    for c in db.query(Customer).all():
        ph = c.phone_number
        rev = revenue.get(ph, 0.0) if ph else 0.0
        no = orders_win.get(ph, 0) if ph else 0
        col = collected.get(c.id, 0.0)
        out = outstanding.get(c.id, 0.0)
        sp = (sp_name.get(c.salesperson_id) if c.salesperson_id else None) or "Unassigned"
        area = c.area or "Unassigned"
        for key, table in ((sp, by_sp), (area, by_area)):
            b = table.setdefault(key, _bucket())
            b["revenue"] += rev
            b["orders"] += no
            b["portfolio"] += 1
            b["collected"] += col
            b["outstanding"] += out
            if no > 0:
                b["active"] += 1

    def _rows(table):
        rows = [{
            "name": k,
            "revenue": round(v["revenue"], 2), "revenue_fmt": fmt_inr(v["revenue"]),
            "orders": v["orders"], "active": v["active"], "portfolio": v["portfolio"],
            "collected": round(v["collected"], 2), "collected_fmt": fmt_inr(v["collected"]),
            "outstanding": round(v["outstanding"], 2), "outstanding_fmt": fmt_inr(v["outstanding"]),
        } for k, v in table.items()]
        rows.sort(key=lambda r: r["revenue"], reverse=True)
        return rows

    return {
        "by_salesperson": _rows(by_sp),
        "by_area": _rows(by_area),
        "days": days,
        "window_label": "All time" if days is None else f"Last {days} days",
        "has_money": bool(collected) or bool(outstanding),
    }


# ── P1-9 Fill rate (ordered vs delivered) ──────────────────────────────────

def fill_rate(db: Session, today: date, days=90) -> dict:
    """P1-9 — delivered vs ordered quantity, from OrderItemActual.

    Fill % = Σ actual / Σ ordered, per SKU and overall, over rows where an
    actual was captured AND the actual unit matches the ordered unit (mixed
    units aren't summable). `days` bounds by the order's business_date;
    None = all time. Rows without an actual are counted as `pending`
    (awaiting weigh-in), not as a shortfall.
    """
    window_start = None if days is None else today - timedelta(days=days - 1)

    q = (
        db.query(OrderItemActual.product, OrderItemActual.ordered_quantity,
                 OrderItemActual.ordered_unit, OrderItemActual.actual_quantity,
                 OrderItemActual.actual_unit)
        .join(Order, OrderItemActual.order_id == Order.id)
        .filter(Order.is_cancelled == False,          # noqa: E712
                Order.business_date <= today.strftime("%Y-%m-%d"))
    )
    if window_start:
        q = q.filter(Order.business_date >= window_start.strftime("%Y-%m-%d"))

    agg = {}  # erp_name → {ordered, actual, unit, lines, pending}
    pending_total = 0
    for product, oq, ou, aq, au in q.all():
        name = erp_display_name(product or "Unknown")
        unit = (ou or "kg").lower()
        row = agg.setdefault(name, {"ordered": 0.0, "actual": 0.0, "unit": unit,
                                    "lines": 0, "pending": 0})
        row["lines"] += 1
        if aq is None:
            row["pending"] += 1
            pending_total += 1
            continue
        if (au or unit).lower() != unit:
            # unit mismatch — can't sum; skip from fill math (rare)
            continue
        row["ordered"] += float(oq or 0)
        row["actual"] += float(aq or 0)

    rows = []
    tot_ordered = tot_actual = 0.0
    for name, r in agg.items():
        fill = round(r["actual"] / r["ordered"] * 100, 1) if r["ordered"] else None
        tot_ordered += r["ordered"]
        tot_actual += r["actual"]
        rows.append({
            "product": name, "unit": r["unit"],
            "ordered": round(r["ordered"], 3), "ordered_fmt": fmt_qty(r["ordered"]),
            "actual": round(r["actual"], 3), "actual_fmt": fmt_qty(r["actual"]),
            "fill_pct": fill, "lines": r["lines"], "pending": r["pending"],
        })
    rows.sort(key=lambda x: x["ordered"], reverse=True)

    overall = round(tot_actual / tot_ordered * 100, 1) if tot_ordered else None
    return {
        "rows": rows,
        "overall_fill_pct": overall,
        "ordered_fmt": fmt_qty(tot_ordered),
        "actual_fmt": fmt_qty(tot_actual),
        "pending": pending_total,
        "has_data": bool(rows),
        "days": days,
        "window_label": "All time" if days is None else f"Last {days} days",
    }


# ── P1-10 Parse-quality monitor (unclear rate over time) ───────────────────

def parse_quality(db: Session, today: date, days: int = 30) -> dict:
    """P1-10 — daily unclear-order rate (data-hygiene signal).

    % unclear = orders flagged is_unclear / total orders, per business_date,
    over the last `days` days. Cancelled orders excluded.
    """
    start = today - timedelta(days=days - 1)
    rows = (
        db.query(Order.business_date, Order.is_unclear)
        .filter(Order.is_cancelled == False,        # noqa: E712
                Order.business_date != None,        # noqa: E711
                Order.business_date >= start.strftime("%Y-%m-%d"),
                Order.business_date <= today.strftime("%Y-%m-%d"))
        .all()
    )
    by_day = {}  # 'YYYY-MM-DD' → [total, unclear]
    for bdate, unclear in rows:
        d = by_day.setdefault(bdate, [0, 0])
        d[0] += 1
        if unclear:
            d[1] += 1

    series = []
    d = start
    tot = unc = 0
    while d <= today:
        ds = d.strftime("%Y-%m-%d")
        t, u = by_day.get(ds, [0, 0])
        tot += t
        unc += u
        series.append({
            "date": ds, "label": d.strftime("%d %b"),
            "total": t, "unclear": u,
            "pct": round(u / t * 100, 1) if t else None,
        })
        d += timedelta(days=1)

    return {
        "series": series,
        "total_orders": tot,
        "total_unclear": unc,
        "overall_pct": round(unc / tot * 100, 1) if tot else None,
        "days": days,
    }
