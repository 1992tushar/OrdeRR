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
from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from orderr_core.models.order import Order
from orderr_core.models.invoice import Invoice, InvoiceItem
from orderr_core.models.customer import Customer
from orderr_core.models.salesperson import Salesperson
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
        })

    # default sort: revenue desc, then most-recent
    rows.sort(key=lambda r: (r["revenue"], -(r["recency_days"] if isinstance(r["recency_days"], int) else 10**9)), reverse=True)

    return {
        "rows": rows,
        "areas": sorted(areas),
        "salespeople": sorted(salespeople),
        "days": days,
        "window_label": "All time" if days is None else f"Last {days} days",
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
    }
