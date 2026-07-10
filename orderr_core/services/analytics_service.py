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
from statistics import median

from sqlalchemy import func
from sqlalchemy.orm import Session

from orderr_core.models.order import Order
from orderr_core.models.invoice import Invoice, InvoiceItem
from orderr_core.models.customer import Customer
from orderr_core.models.salesperson import Salesperson
from orderr_core.models.actuals import OrderItemActual
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
