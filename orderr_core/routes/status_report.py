"""
📊 Live order-status page — the fixed URL that replaced the manager_daily_report
/ manager_daily_summary WhatsApp templates (owner decision 2026-07-14: long
customer lists are unreadable in WhatsApp and each template send costs money).

One static link, NO auth (salespersons have no logins): /r/{REPORT_LINK_KEY}.
Content follows the business date, which rolls over at RESET_HOUR (9 PM IST) —
after 9 PM the page shows tomorrow's delivery cycle, exactly like ordering.
"""
from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from orderr_core.config import PLANT_NAME, REPORT_LINK_KEY, sp_slug
from orderr_core.constants import IST, RESET_HOUR
from orderr_core.database import get_db
from orderr_core.dates import get_current_business_date
from orderr_core.models.customer import Customer
from orderr_core.models.order import Order
from orderr_core.models.salesperson import Salesperson
from orderr_core.services.pending_orders import active_daily_customers_q, ordered_sets
from orderr_core.templating import make_templates

router = APIRouter()
templates = make_templates()


def order_status_data(db: Session, salesperson_id: int | None = None) -> dict:
    """Who has ordered vs who is pending for the current business date,
    grouped by salesperson. Roster = the 📣 Broadcast list; "ordered" =
    OrdeRR (WhatsApp) order OR Vasy invoice for the day. Pass
    `salesperson_id` to restrict to one salesperson's customers (their
    personal page). Pure/testable."""
    delivery_date = get_current_business_date()
    date_str = delivery_date.strftime("%Y-%m-%d")

    roster_q = active_daily_customers_q(db)
    if salesperson_id is not None:
        roster_q = roster_q.filter(Customer.salesperson_id == salesperson_id)
    customers = roster_q.all()
    ordered_phones, invoiced_ids = ordered_sets(db, delivery_date)
    orders = (db.query(Order)
              .filter(Order.business_date == date_str,
                      Order.is_cancelled == False)          # noqa: E712
              .all())
    order_by_phone = {}
    for o in orders:
        # keep the earliest order per phone for the "ordered at" stamp
        prev = order_by_phone.get(o.customer_phone)
        if prev is None or (o.created_at and prev.created_at and o.created_at < prev.created_at):
            order_by_phone[o.customer_phone] = o

    sp_names = {sp.id: sp.name for sp in db.query(Salesperson).all()}

    groups: dict = defaultdict(list)
    ordered_count = 0
    for c in customers:
        order = order_by_phone.get(c.phone_number)
        ordered = (c.phone_number in ordered_phones) or (c.id in invoiced_ids)
        if ordered:
            ordered_count += 1
        at = None
        if order and order.created_at:
            try:
                at = order.created_at.astimezone(IST).strftime("%I:%M %p").lstrip("0")
            except Exception:
                at = None
        elif ordered:
            at = "🧾 billed"     # phone order — arrived via the Vasy import
        groups[c.salesperson_id].append({
            "name": c.restaurant_name or c.owner_name or f"#{c.id}",
            "area": c.area,
            "ordered": ordered,
            "at": at,
        })

    sections = []
    for sp_id, rows in groups.items():
        rows.sort(key=lambda r: (r["ordered"], r["name"]))  # pending first
        pending = sum(1 for r in rows if not r["ordered"])
        name = sp_names.get(sp_id)
        sections.append({
            "salesperson": name or "Unassigned",
            "slug": sp_slug(name) if name else None,   # link to their personal page
            "rows": rows,
            "pending": pending,
            "total": len(rows),
        })
    # most pending on top; Unassigned last
    sections.sort(key=lambda s: (s["salesperson"] == "Unassigned", -s["pending"], s["salesperson"]))

    total = len(customers)
    return {
        "date_display": delivery_date.strftime("%A, %d %B %Y"),
        "total": total,
        "ordered": ordered_count,
        "pending": total - ordered_count,
        "sections": sections,
        "updated": datetime.now(IST).strftime("%I:%M %p").lstrip("0"),
        "reset_hour_display": f"{RESET_HOUR - 12} PM",
    }


@router.get("/r/{key}", response_class=HTMLResponse)
def order_status_page(key: str, request: Request, db: Session = Depends(get_db)):
    """Public live status page — all salespersons (the manager's view)."""
    if key != REPORT_LINK_KEY:
        raise HTTPException(status_code=404, detail="Not found")
    return templates.TemplateResponse(
        request=request,
        name="order_status.html",
        context={"plant_name": PLANT_NAME, "viewer": None,
                 "link_key": REPORT_LINK_KEY, **order_status_data(db)},
    )


@router.get("/r/{key}/{slug}", response_class=HTMLResponse)
def salesperson_status_page(key: str, slug: str, request: Request,
                            db: Session = Depends(get_db)):
    """A salesperson's personal page — only their customers. Slug = first
    name (config.sp_slug), e.g. /r/<key>/ganesh."""
    if key != REPORT_LINK_KEY:
        raise HTTPException(status_code=404, detail="Not found")
    sp = next((s for s in db.query(Salesperson).filter(Salesperson.active == True).all()  # noqa: E712
               if sp_slug(s.name) == slug.lower()), None)
    if not sp:
        raise HTTPException(status_code=404, detail="Not found")
    return templates.TemplateResponse(
        request=request,
        name="order_status.html",
        context={"plant_name": PLANT_NAME, "viewer": sp.name,
                 "link_key": REPORT_LINK_KEY,
                 **order_status_data(db, salesperson_id=sp.id)},
    )
