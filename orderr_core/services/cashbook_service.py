"""
Cash Book service (spec: CASH_BOOK_REQUIREMENTS.md).

The daily cash-drawer ledger. Read-only VIEW over the Vasy mirrors plus manual
`cash_entries` lines:

  IN   customer_receipts  mode='cash'   (incl. walk-ins / unmatched parties)
  OUT  vasy_payments      mode='cash'   (single cash-out source — the payments
                                         export already carries expense
                                         payments as vouchers; adding expenses
                                         too would double-count, §2 of spec)
  ±    cash_entries                     (drawings, bank deposits, float,
                                         adjustments — things Vasy never sees)

Running balance anchors on the most recent physically-verified number: the
last signed 5-Day Close's counted cash (start of to_date+1), or a manual
`opening_set` entry — whichever is later. Before any anchor exists the book
shows flows only, flagged un-anchored.

Mutations return an error string, or None on success (house convention).
"""
from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from orderr_core.models.cash_entry import CashEntry
from orderr_core.models.close_period import ClosePeriod
from orderr_core.models.customer import Customer
from orderr_core.models.customer_receipt import CustomerReceipt
from orderr_core.models.import_log import ImportLog
from orderr_core.models.vasy_expense import VasyExpense
from orderr_core.models.vasy_payment import VasyPayment
from orderr_core.services.analytics_service import fmt_inr

# type → fixed direction ('in'/'out'), None = caller supplies, '' = not a flow
ENTRY_TYPES = {
    "drawing":        {"label": "Owner drawing",   "direction": "out"},
    "bank_deposit":   {"label": "Cash → bank",     "direction": "out"},
    "float_given":    {"label": "Float given",     "direction": "out"},
    "float_returned": {"label": "Float returned",  "direction": "in"},
    "adjustment":     {"label": "Adjustment",      "direction": None},   # note mandatory
    "other":          {"label": "Other",           "direction": None},
    "opening_set":    {"label": "Opening balance", "direction": ""},     # anchor
    "spot_count":     {"label": "Drawer counted",  "direction": ""},     # check
}
FLOW_TYPES = [k for k, v in ENTRY_TYPES.items() if v["direction"] != ""]


def _fmt_day(d: date | None) -> str:
    return d.strftime("%d %b %Y") if d else ""


def _parse_date(raw) -> date | None:
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw).strip())
    except (TypeError, ValueError):
        return None


# ── flows ───────────────────────────────────────────────────────────────────

def _flow_range(db: Session, start: date | None, end: date) -> tuple:
    """(cash_in, cash_out) summed over [start, end] (start None = all time)."""
    rq = db.query(func.coalesce(func.sum(CustomerReceipt.amount), 0)) \
        .filter(CustomerReceipt.mode == "cash", CustomerReceipt.receipt_date <= end)
    pq = db.query(func.coalesce(func.sum(VasyPayment.amount), 0)) \
        .filter(VasyPayment.mode == "cash", VasyPayment.payment_date <= end)
    if start is not None:
        rq = rq.filter(CustomerReceipt.receipt_date >= start)
        pq = pq.filter(VasyPayment.payment_date >= start)
    cash_in = float(rq.scalar() or 0)
    cash_out = float(pq.scalar() or 0)

    eq = db.query(CashEntry).filter(CashEntry.entry_date <= end,
                                    CashEntry.type.in_(FLOW_TYPES))
    if start is not None:
        eq = eq.filter(CashEntry.entry_date >= start)
    for e in eq.all():
        if e.direction == "in":
            cash_in += float(e.amount)
        elif e.direction == "out":
            cash_out += float(e.amount)
    return cash_in, cash_out


def _anchor(db: Session, on_or_before: date):
    """Most recent verified drawer amount effective at the START of some day
    ≤ on_or_before. Returns (anchor_day, amount, source_label) or None.
    Candidates: signed close counted cash (effective to_date+1) and manual
    opening_set entries (effective their entry_date)."""
    candidates = []
    close = (db.query(ClosePeriod)
             .filter(ClosePeriod.counted_cash != None,                     # noqa: E711
                     ClosePeriod.to_date < on_or_before)
             .order_by(ClosePeriod.to_date.desc()).first())
    if close:
        candidates.append((close.to_date + timedelta(days=1), float(close.counted_cash),
                           f"counted at 5-day close {_fmt_day(close.to_date)}"))
    opening = (db.query(CashEntry)
               .filter(CashEntry.type == "opening_set", CashEntry.entry_date <= on_or_before)
               .order_by(CashEntry.entry_date.desc(), CashEntry.id.desc()).first())
    if opening:
        candidates.append((opening.entry_date, float(opening.amount),
                           f"opening set on {_fmt_day(opening.entry_date)}"))
    if not candidates:
        return None
    return max(candidates, key=lambda c: c[0])


def opening_balance(db: Session, day: date):
    """(opening, anchored: bool, anchor_label) at the start of `day`."""
    a = _anchor(db, day)
    if a is None:
        cash_in, cash_out = _flow_range(db, None, day - timedelta(days=1))
        return round(cash_in - cash_out, 2), False, None
    anchor_day, amount, label = a
    if anchor_day > day - timedelta(days=1):
        return round(amount, 2), True, label
    cash_in, cash_out = _flow_range(db, anchor_day, day - timedelta(days=1))
    return round(amount + cash_in - cash_out, 2), True, label


# ── day page ────────────────────────────────────────────────────────────────

def day_page(db: Session, day: date) -> dict:
    cust_name = {c.id: (c.restaurant_name or c.phone_number or f"#{c.id}")
                 for c in db.query(Customer).all()}
    lines = []

    for r in (db.query(CustomerReceipt)
              .filter(CustomerReceipt.mode == "cash", CustomerReceipt.receipt_date == day)
              .order_by(CustomerReceipt.id).all()):
        name = cust_name.get(r.customer_id) or r.party_name or "?"
        lines.append({"kind": "receipt", "direction": "in", "amount": float(r.amount),
                      "label": name, "sub": f"cash receipt {r.receipt_no}",
                      "customer_id": r.customer_id, "entry_id": None})

    for p in (db.query(VasyPayment)
              .filter(VasyPayment.mode == "cash", VasyPayment.payment_date == day)
              .order_by(VasyPayment.id).all()):
        lines.append({"kind": "payment", "direction": "out", "amount": float(p.amount),
                      "label": p.party_name or "?", "sub": f"cash paid {p.payment_no}",
                      "customer_id": None, "entry_id": None})

    spot = None
    for e in (db.query(CashEntry).filter(CashEntry.entry_date == day)
              .order_by(CashEntry.id).all()):
        meta = ENTRY_TYPES.get(e.type, ENTRY_TYPES["other"])
        if e.type == "spot_count":
            spot = {"amount": float(e.amount), "entry_id": e.id, "note": e.note or ""}
            continue
        direction = meta["direction"] if meta["direction"] else e.direction
        if e.type == "opening_set":
            direction = ""      # anchor line, shown but not a flow
        lines.append({"kind": "manual", "direction": direction, "amount": float(e.amount),
                      "label": meta["label"], "sub": e.note or "", "type": e.type,
                      "customer_id": None, "entry_id": e.id})

    total_in = sum(l["amount"] for l in lines if l["direction"] == "in")
    total_out = sum(l["amount"] for l in lines if l["direction"] == "out")
    for l in lines:
        l["amount_fmt"] = fmt_inr(l["amount"])

    opening, anchored, anchor_label = opening_balance(db, day)
    closing = round(opening + total_in - total_out, 2)
    variance = round(spot["amount"] - closing, 2) if spot else None

    return {
        "day": day.strftime("%Y-%m-%d"), "day_display": _fmt_day(day),
        "lines": lines,
        "total_in": round(total_in, 2), "total_in_fmt": fmt_inr(total_in),
        "total_out": round(total_out, 2), "total_out_fmt": fmt_inr(total_out),
        "opening": opening, "opening_fmt": fmt_inr(opening),
        "closing": closing, "closing_fmt": fmt_inr(closing),
        "anchored": anchored, "anchor_label": anchor_label,
        "spot": ({**spot, "amount_fmt": fmt_inr(spot["amount"]),
                  "variance": variance, "variance_fmt": fmt_inr(abs(variance))}
                 if spot else None),
    }


def month_strip(db: Session, day: date) -> list:
    """Per-day in/out/closing for `day`'s month. Each day's closing is
    computed via opening_balance() (anchor-aware) + that day's flows — NOT a
    naive month-long roll-forward, which drifts from the day pages whenever an
    anchor (opening_set / signed close) takes effect mid-month."""
    from calendar import monthrange
    first = day.replace(day=1)
    last_dom = monthrange(day.year, day.month)[1]

    per_day = {}
    for d, amt in (db.query(CustomerReceipt.receipt_date,
                            func.coalesce(func.sum(CustomerReceipt.amount), 0))
                   .filter(CustomerReceipt.mode == "cash",
                           CustomerReceipt.receipt_date >= first,
                           CustomerReceipt.receipt_date <= first.replace(day=last_dom))
                   .group_by(CustomerReceipt.receipt_date).all()):
        per_day.setdefault(d, [0.0, 0.0])[0] += float(amt)
    for d, amt in (db.query(VasyPayment.payment_date,
                            func.coalesce(func.sum(VasyPayment.amount), 0))
                   .filter(VasyPayment.mode == "cash",
                           VasyPayment.payment_date >= first,
                           VasyPayment.payment_date <= first.replace(day=last_dom))
                   .group_by(VasyPayment.payment_date).all()):
        per_day.setdefault(d, [0.0, 0.0])[1] += float(amt)
    for e in (db.query(CashEntry)
              .filter(CashEntry.entry_date >= first,
                      CashEntry.entry_date <= first.replace(day=last_dom),
                      CashEntry.type.in_(FLOW_TYPES)).all()):
        slot = per_day.setdefault(e.entry_date, [0.0, 0.0])
        if e.direction == "in":
            slot[0] += float(e.amount)
        elif e.direction == "out":
            slot[1] += float(e.amount)

    rows = []
    for dom in range(1, last_dom + 1):
        d = first.replace(day=dom)
        cin, cout = per_day.get(d, (0.0, 0.0))
        opening, _, _ = opening_balance(db, d)
        rows.append({"day": d.strftime("%Y-%m-%d"), "dom": dom,
                     "label": d.strftime("%d %a"),
                     "in": round(cin, 2), "in_fmt": fmt_inr(cin),
                     "out": round(cout, 2), "out_fmt": fmt_inr(cout),
                     "closing_fmt": fmt_inr(round(opening + cin - cout, 2)),
                     "has_activity": bool(cin or cout)})
    return rows


# ── manual entries ──────────────────────────────────────────────────────────

def add_entry(db: Session, data: dict, today: date):
    etype = (data.get("type") or "").strip()
    if etype not in ENTRY_TYPES:
        return "Invalid entry type."
    try:
        amount = round(float(str(data.get("amount") or "").replace(",", "").strip()), 2)
    except ValueError:
        return "Enter the amount."
    if amount <= 0 and etype != "opening_set":
        return "Amount must be more than zero."
    if amount < 0:
        return "Amount can't be negative."
    d = _parse_date(data.get("entry_date")) or today
    if d > today:
        return "Date can't be in the future."
    note = (data.get("note") or "").strip()
    if etype == "adjustment" and not note:
        return "An adjustment needs a note saying why."

    fixed = ENTRY_TYPES[etype]["direction"]
    if fixed == "":
        direction = None
    elif fixed is not None:
        direction = fixed
    else:
        direction = (data.get("direction") or "").strip()
        if direction not in ("in", "out"):
            return "Pick money in or money out."

    if etype == "spot_count":
        # one count per day — replace
        db.query(CashEntry).filter(CashEntry.entry_date == d,
                                   CashEntry.type == "spot_count").delete()
    db.add(CashEntry(entry_date=d, type=etype, direction=direction,
                     amount=amount, note=note or None))
    db.commit()
    return None


def delete_entry(db: Session, entry_id: int):
    rec = db.query(CashEntry).get(entry_id)
    if rec is None:
        return "Entry not found."
    db.delete(rec)
    db.commit()
    return None


# ── freshness + expense cross-check ─────────────────────────────────────────

def freshness(db: Session) -> list:
    """Last import per entity the book depends on — never present a stale
    drawer as authoritative (same rule as the 5-day close)."""
    out = []
    for entity, label in (("receipts", "Cash in (receipts)"),
                          ("payments", "Cash out (payments)"),
                          ("expenses", "Expense cross-check")):
        last = (db.query(func.max(ImportLog.imported_at))
                .filter(ImportLog.entity == entity).scalar())
        out.append({"entity": entity, "label": label,
                    "imported_at": last.strftime("%d %b %Y %H:%M") if last else "never"})
    return out


def expense_crosscheck(db: Session, limit: int = 25) -> dict:
    """P2 §6 — expenses whose Payment Data says cash but no matching cash
    payment voucher exists (drawer money left without a voucher), plus mode
    mismatches. Match: party_key + date + amount, then party_key + date.
    Only expenses the register export has covered (cash_paid NOT NULL)."""
    expenses = (db.query(VasyExpense)
                .filter(VasyExpense.cash_paid != None)                     # noqa: E711
                .all())
    if not expenses:
        return {"covered": 0, "no_voucher": [], "mode_mismatch": []}

    pays = {}
    for p in db.query(VasyPayment).filter(VasyPayment.payment_date != None).all():  # noqa: E711
        pays.setdefault((p.party_key, p.payment_date), []).append(p)

    no_voucher, mode_mismatch = [], []
    for e in expenses:
        cash = float(e.cash_paid or 0)
        if cash <= 0 or e.expense_date is None:
            continue
        siblings = pays.get((e.party_key, e.expense_date), [])
        exact_cash = [p for p in siblings if p.mode == "cash" and abs(float(p.amount) - cash) < 0.01]
        if exact_cash:
            continue
        row = {"expense_no": e.expense_no, "party": e.party_name,
               "date": _fmt_day(e.expense_date), "cash_fmt": fmt_inr(cash)}
        noncash_twin = [p for p in siblings if p.mode != "cash"
                        and abs(float(p.amount) - cash) < 0.01]
        if noncash_twin:
            row["payment_mode"] = noncash_twin[0].mode or "?"
            mode_mismatch.append(row)
        elif any(p.mode == "cash" for p in siblings):
            continue    # same-day cash voucher with different amount — likely combined; skip
        else:
            no_voucher.append(row)

    return {"covered": len(expenses),
            "no_voucher": no_voucher[:limit], "no_voucher_count": len(no_voucher),
            "mode_mismatch": mode_mismatch[:limit], "mode_mismatch_count": len(mode_mismatch)}
