"""
admin.py — Admin API routes (Basic Auth protected)

Salesperson:  GET/POST /admin/salespersons
              PUT/DELETE /admin/salespersons/{id}
Customer:     GET /admin/customers
              GET /admin/customers/unassigned
              GET /admin/customers/{id}/orders
              POST /admin/customers/{id}/assign
              PUT  /admin/customers/{id}/status
              POST /customers
Pending:      GET /admin/pending
Window:       GET /admin/window-status
Unclear:      GET /admin/unclear-items
              GET /admin/unclear-items/aliases
              GET /admin/product-names
              POST /admin/unclear-items/resolve
              DELETE /admin/unclear-items/aliases/{id}
"""

import os
import re
import json
from datetime import date, datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.auth import require_auth
from app.database import get_db
from app.models.customer import Customer
from app.models.order import Order
from app.models.salesperson import Salesperson
from app.models.inbound_message import InboundMessage
from app.models.unclear_item_alias import UnclearItemAlias
from app.services.customer_service import normalize_phone
from app.services.notifier import send_whatsapp_message
from app.services.pending_orders import get_pending_customers, get_delivery_date_for_now
from app.services.template_parser import PRODUCT_DEFINITIONS
from app.services.customer_service import create_customer_manually

from app.services.order_service import get_current_business_date_str, RESET_HOUR
router     = APIRouter()
PLANT_NAME = os.getenv("PLANT_NAME", "Fluffy")
MANAGER_PHONE = os.getenv("MANAGER_PHONE", "")
IST        = timezone(timedelta(hours=5, minutes=30))


# ── Schemas ───────────────────────────────────────────────────────────────────

class SalespersonCreate(BaseModel):
    name: str
    phone: str

class SalespersonUpdate(BaseModel):
    name:   Optional[str]  = None
    phone:  Optional[str]  = None
    active: Optional[bool] = None

class CustomerAssign(BaseModel):
    area:           Optional[str] = None
    salesperson_id: Optional[int] = None

class CustomerStatus(BaseModel):
    is_active: bool

class CustomerCreate(BaseModel):
    phone: str
    restaurant_name: str
    area: Optional[str] = None
    salesperson_id: Optional[int] = None


class NextDayOverride(BaseModel):
    is_next_day: bool

# ── Salespersons ──────────────────────────────────────────────────────────────

@router.get("/salespersons")
def list_salespersons(db: Session = Depends(get_db), username: str = Depends(require_auth)):
    sps = db.query(Salesperson).order_by(Salesperson.name).all()
    result = []
    for sp in sps:
        count = db.query(Customer).filter(Customer.salesperson_id == sp.id).count()
        result.append({
            "id": sp.id, "name": sp.name, "phone": sp.phone,
            "active": sp.active, "customer_count": count,
            "created_at": sp.created_at.isoformat() if sp.created_at else None,
        })
    return {"salespersons": result, "total": len(result)}


@router.post("/salespersons")
def create_salesperson(payload: SalespersonCreate, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    normalized = normalize_phone(payload.phone)
    if db.query(Salesperson).filter(Salesperson.phone == normalized).first():
        raise HTTPException(status_code=400, detail=f"Salesperson with phone {normalized} already exists")

    sp = Salesperson(name=payload.name.strip(), phone=normalized, active=True)
    db.add(sp); db.commit(); db.refresh(sp)

    try:
        send_whatsapp_message(
            sp.phone,
            f"👋 Hi {sp.name},\n\n"
            f"You've been added to the OrdeRR system for *{PLANT_NAME}*.\n\n"
            f"You will receive a daily WhatsApp message at *11:05 PM* "
            f"with a list of customers who haven't placed their order yet.\n\n"
            f"Please follow up with them for order collection.\n\n"
            f"— {PLANT_NAME} Team"
        )
    except Exception as e:
        print(f"⚠️ Welcome message failed (salesperson still created): {e}")

    return {"status": "created", "salesperson": {"id": sp.id, "name": sp.name, "phone": sp.phone, "active": sp.active}}


@router.put("/salespersons/{salesperson_id}")
def update_salesperson(salesperson_id: int, payload: SalespersonUpdate, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    sp = db.query(Salesperson).filter(Salesperson.id == salesperson_id).first()
    if not sp:
        raise HTTPException(status_code=404, detail="Salesperson not found")
    if payload.name   is not None: sp.name   = payload.name.strip()
    if payload.phone  is not None: sp.phone  = normalize_phone(payload.phone)
    if payload.active is not None: sp.active = payload.active
    db.commit(); db.refresh(sp)
    return {"status": "updated", "salesperson": {"id": sp.id, "name": sp.name, "phone": sp.phone, "active": sp.active}}


@router.delete("/salespersons/{salesperson_id}")
def deactivate_salesperson(salesperson_id: int, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    sp = db.query(Salesperson).filter(Salesperson.id == salesperson_id).first()
    if not sp:
        raise HTTPException(status_code=404, detail="Salesperson not found")
    sp.active = False; db.commit()
    affected = db.query(Customer).filter(Customer.salesperson_id == salesperson_id).count()
    return {"status": "deactivated", "salesperson_id": salesperson_id, "affected_customers": affected}


# ── Hard Delete Extension for Salespersons (Added to support full CRUD without modifying original logic) ──
@router.delete("/salespersons/{salesperson_id}/purge")
def hard_delete_salesperson(salesperson_id: int, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    sp = db.query(Salesperson).filter(Salesperson.id == salesperson_id).first()
    if not sp:
        raise HTTPException(status_code=404, detail="Salesperson not found")
    
    # Unassign customers first to prevent foreign key issues
    db.query(Customer).filter(Customer.salesperson_id == salesperson_id).update({Customer.salesperson_id: None})
    db.delete(sp)
    db.commit()
    return {"status": "purged", "salesperson_id": salesperson_id}


# ── Customers ─────────────────────────────────────────────────────────────────

def _customer_row(c: Customer, db: Session) -> dict:
    sp_name = None
    if c.salesperson_id:
        sp = db.query(Salesperson).filter(Salesperson.id == c.salesperson_id).first()
        sp_name = sp.name if sp else None
    return {
        "id": c.id, "restaurant_name": c.restaurant_name,
        "phone_number": c.phone_number, "area": c.area,
        "salesperson_id": c.salesperson_id, "salesperson_name": sp_name,
        "is_active": c.is_active,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("/customers")
def list_customers(db: Session = Depends(get_db), username: str = Depends(require_auth)):
    today_str = get_current_business_date_str()
    customers = (
        db.query(Customer)
        .filter(Customer.onboarding_status == "active")
        .order_by(Customer.restaurant_name)
        .all()
    )

    ordered_today = {
    row[0]
    for row in db.query(Order.customer_phone)
    .filter(
        Order.business_date == today_str,
        Order.is_cancelled == False,
    )
    .all()
    }


    result = []
    for c in customers:
        row = _customer_row(c, db)
        row["ordered_today"] = c.phone_number in ordered_today
        result.append(row)
    return {"customers": result, "total": len(result)}


@router.get("/customers/unassigned")
def list_unassigned_customers(db: Session = Depends(get_db), username: str = Depends(require_auth)):
    customers = (
        db.query(Customer)
        .filter(Customer.onboarding_status == "active", Customer.salesperson_id == None)
        .order_by(Customer.created_at.desc())
        .all()
    )
    return {"customers": [_customer_row(c, db) for c in customers], "total": len(customers)}

@router.post("/customers")
def add_customer_manually(
    payload: CustomerCreate,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    try:
        customer = create_customer_manually(
            db=db,
            phone=payload.phone,
            restaurant_name=payload.restaurant_name,
            area=payload.area,
            salesperson_id=payload.salesperson_id,
        )
        return {"status": "created", "customer": _customer_row(customer, db)}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

@router.get("/customers/{customer_id}/orders")
def get_customer_orders(customer_id: int, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    orders = (
        db.query(Order)
        .filter(Order.customer_phone == customer.phone_number)
        .order_by(Order.created_at.desc())
        .limit(60)
        .all()
    )

    result = []
    for o in orders:
        unclear_items = json.loads(o.unclear_items) if getattr(o, 'unclear_items', None) else []
        result.append({
            "id"            : o.id,
            "delivery_date" : o.delivery_date,
            "delivery_time" : o.delivery_time,
            "status"        : o.status,
            "is_cancelled"  : o.is_cancelled,
            "is_unclear"    : o.is_unclear,
            "unclear_reason": o.unclear_reason,
            "unclear_items" : unclear_items,
            "raw_message"   : o.raw_message,
            "items"         : json.loads(o.parsed_items) if o.parsed_items else [],
            "created_at"    : o.created_at.isoformat() if o.created_at else None,
        })

    return {
        "customer_id"    : customer_id,
        "restaurant_name": customer.restaurant_name,
        "phone_number"   : customer.phone_number,
        "total_orders"   : len(result),
        "orders"         : result,
    }


@router.post("/customers/{customer_id}/assign")
def assign_customer(customer_id: int, payload: CustomerAssign, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    if payload.area is not None:
        customer.area = payload.area.strip()
    if payload.salesperson_id is not None:
        sp = db.query(Salesperson).filter(Salesperson.id == payload.salesperson_id).first()
        if not sp:
            raise HTTPException(status_code=404, detail=f"Salesperson id={payload.salesperson_id} not found")
        customer.salesperson_id = payload.salesperson_id
    db.commit(); db.refresh(customer)
    sp_name = None
    if customer.salesperson_id:
        sp = db.query(Salesperson).filter(Salesperson.id == customer.salesperson_id).first()
        sp_name = sp.name if sp else None
    return {"status": "assigned", "customer": {"id": customer.id, "restaurant_name": customer.restaurant_name, "area": customer.area, "salesperson_id": customer.salesperson_id, "salesperson_name": sp_name}}


@router.put("/customers/{customer_id}/status")
def update_customer_status(customer_id: int, payload: CustomerStatus, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    customer.is_active = payload.is_active
    db.commit(); db.refresh(customer)
    return {"status": "updated", "customer": {"id": customer.id, "restaurant_name": customer.restaurant_name, "phone_number": customer.phone_number, "is_active": customer.is_active}}


# ── Pending orders ────────────────────────────────────────────────────────────

@router.get("/pending")
def get_pending_now(delivery_date: Optional[str] = None, db: Session = Depends(get_db), username: str = Depends(require_auth)):
    if delivery_date:
        try:
            target_date = date.fromisoformat(delivery_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    else:
        target_date = get_delivery_date_for_now()

    grouped = get_pending_customers(db, target_date)
    result  = []
    total   = 0

    for sp_id, customers in grouped.items():
        sp_name = "Unassigned"; sp_phone = None
        if sp_id is not None:
            sp = db.query(Salesperson).filter(Salesperson.id == sp_id).first()
            sp_name = sp.name if sp else f"Unknown (id={sp_id})"
            sp_phone = sp.phone if sp else None
        result.append({
            "salesperson_id": sp_id, "salesperson_name": sp_name,
            "salesperson_phone": sp_phone, "pending_count": len(customers),
            "customers": [{"id": c.id, "restaurant_name": c.restaurant_name, "phone_number": c.phone_number, "area": c.area} for c in customers],
        })
        total += len(customers)

    return {"delivery_date": target_date.isoformat(), "total_pending": total, "groups": result}



@router.put("/orders/{order_id}/next-day")
def set_next_day_override(
    order_id: int,
    payload: NextDayOverride,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    now_ist = datetime.now(IST)
    if now_ist.hour >= RESET_HOUR:
        raise HTTPException(
            status_code=403,
            detail="Override window closed. Orders are locked after 8 PM IST."
        )

    order_ist = order.created_at.astimezone(IST)
    cutoff = order_ist.replace(hour=RESET_HOUR, minute=0, second=0, microsecond=0)
    if order_ist >= cutoff:
        raise HTTPException(
            status_code=400,
            detail="This order was placed after 8 PM IST and is already assigned to the next day."
        )

    if payload.is_next_day:
        next_day = (order_ist.date() + timedelta(days=1)).strftime("%Y-%m-%d")
        order.business_date = next_day
        order.is_next_day_override = True
    else:
        order.business_date = order_ist.date().strftime("%Y-%m-%d")
        order.is_next_day_override = False

    db.commit()
    db.refresh(order)
    return {"success": True, "order_id": order.id, "business_date": order.business_date}

# ── WhatsApp Window Status ────────────────────────────────────────────────────

def _window_status(last_inbound: datetime | None, now: datetime) -> dict:
    if last_inbound is None:
        return {
            "status": "CLOSED",
            "status_label": "Never messaged",
            "hours_remaining": 0,
            "minutes_remaining": 0,
            "last_seen_ist": None,
            "last_seen_display": "Never",
        }

    if last_inbound.tzinfo is None:
        last_inbound = last_inbound.replace(tzinfo=timezone.utc)

    window_expires = last_inbound + timedelta(hours=24)
    remaining = window_expires - now

    if remaining.total_seconds() <= 0:
        return {
            "status": "CLOSED",
            "status_label": "Closed",
            "hours_remaining": 0,
            "minutes_remaining": 0,
            "last_seen_ist": last_inbound.astimezone(IST).isoformat(),
            "last_seen_display": last_inbound.astimezone(IST).strftime("%d %b %H:%M"),
        }

    total_minutes = int(remaining.total_seconds() // 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    status = "OPEN" if hours >= 4 else "AT_RISK"

    return {
        "status": status,
        "status_label": f"{hours}h {minutes}m left",
        "hours_remaining": hours,
        "minutes_remaining": minutes,
        "last_seen_ist": last_inbound.astimezone(IST).isoformat(),
        "last_seen_display": last_inbound.astimezone(IST).strftime("%d %b %H:%M"),
    }


@router.get("/window-status")
def get_window_status(db: Session = Depends(get_db), username: str = Depends(require_auth)):
    now = datetime.now(timezone.utc)
    stakeholders = []

    if MANAGER_PHONE:
        stakeholders.append({"name": "Manager", "phone": MANAGER_PHONE, "role": "manager"})

    for sp in db.query(Salesperson).filter(Salesperson.active == True).all():
        stakeholders.append({"name": sp.name, "phone": sp.phone, "role": "salesperson"})

    for c in (
        db.query(Customer)
        .filter(Customer.is_active == True, Customer.onboarding_status == "active")
        .order_by(Customer.restaurant_name)
        .all()
    ):
        stakeholders.append({"name": c.restaurant_name or c.phone_number, "phone": c.phone_number, "role": "customer"})

    all_phones = [s["phone"] for s in stakeholders]
    last_seen_map = {}
    if all_phones:
        rows = (
            db.query(InboundMessage.customer_phone, func.max(InboundMessage.received_at).label("last_seen"))
            .filter(InboundMessage.customer_phone.in_(all_phones))
            .group_by(InboundMessage.customer_phone)
            .all()
        )
        last_seen_map = {row.customer_phone: row.last_seen for row in rows}

    result = []
    closed_count = at_risk_count = 0
    for s in stakeholders:
        window = _window_status(last_seen_map.get(s["phone"]), now)
        result.append({"name": s["name"], "phone": s["phone"], "role": s["role"], **window})
        if window["status"] == "CLOSED":   closed_count += 1
        elif window["status"] == "AT_RISK": at_risk_count += 1

    return {
        "stakeholders" : result,
        "total"        : len(result),
        "closed_count" : closed_count,
        "at_risk_count": at_risk_count,
        "checked_at"   : datetime.now(IST).strftime("%d %b %Y %H:%M IST"),
    }


# ── Unclear Items ─────────────────────────────────────────────────────────────

@router.get("/unclear-items")
def get_unclear_items(db: Session = Depends(get_db), username: str = Depends(require_auth)):
    """Orders that have unresolved unclear items."""
    orders = (
        db.query(Order)
        .filter(
            Order.unclear_items.isnot(None),
            Order.unclear_items != "[]",
            Order.unclear_items != "null",
            Order.is_cancelled == False,
        )
        .order_by(Order.created_at.desc())
        .all()
    )
    result = []
    for o in orders:
        unclear = json.loads(o.unclear_items) if o.unclear_items else []
        if not unclear:
            continue
        result.append({
            "order_id"      : o.id,
            "customer_name" : o.customer_name,
            "customer_phone": o.customer_phone,
            "raw_message"   : o.raw_message,
            "unclear_items" : unclear,
            "parsed_items"  : json.loads(o.parsed_items) if o.parsed_items else [],
            "delivery_date" : o.delivery_date,
            "created_at"    : o.created_at.isoformat() if o.created_at else None,
        })
    return result


@router.get("/unclear-items/aliases")
def get_aliases(db: Session = Depends(get_db), username: str = Depends(require_auth)):
    """List all saved aliases."""
    aliases = db.query(UnclearItemAlias).order_by(UnclearItemAlias.raw_text).all()
    return [
        {
            "id"                    : a.id,
            "raw_text"              : a.raw_text,
            "canonical_product_name": a.canonical_product_name,
            "created_at"            : a.created_at.isoformat() if a.created_at else None,
        }
        for a in aliases
    ]


@router.get("/product-names")
def get_product_names(username: str = Depends(require_auth)):
    """Valid canonical product names for the alias dropdown."""
    return sorted([display for display, _, _ in PRODUCT_DEFINITIONS])


# ── Helper: extract product name part from a raw unclear line ─────────────────

LINE_RE = re.compile(
    r"^(.+?)\s*[-:]?\s*([\d\.]+)\s*(kg|kgs|nos|pcs|pc|pis|pieces|piece|k)?\s*$",
    re.IGNORECASE,
)

def _extract_product_name(raw_line: str) -> tuple[str, float]:
    """
    Given a raw unclear line like "Raan -5" or "kaleji 2kg",
    returns (product_name_lower, quantity).
    Falls back to (full_line_lower, 1.0) if no quantity found.
    """
    line_clean = re.sub(r'__+', '', raw_line).strip()
    line_clean = re.sub(r'(\d+)\s*k\b', r'\1 kg', line_clean)
    m = LINE_RE.match(line_clean)
    if m:
        name = m.group(1).strip().lower()
        try:
            qty = float(m.group(2))
        except (TypeError, ValueError):
            qty = 1.0
        return name, qty
    return line_clean.lower(), 1.0


# ── Missing Helper Function: _lookup_alias (Added back based on code documentation requirements) ──
def _lookup_alias(raw_text: str, db: Session) -> Optional[str]:
    """
    Helper utility referenced by resolve_unclear_item documentation.
    Looks up canonical product names directly via active alias tags.
    """
    alias_match = db.query(UnclearItemAlias).filter(UnclearItemAlias.raw_text == raw_text.strip().lower()).first()
    return alias_match.canonical_product_name if alias_match else None


@router.post("/unclear-items/resolve")
def resolve_unclear_item(
    payload: dict,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """
    Save raw_text → canonical_product_name alias and retroactively
    patch all past orders that have that raw_text in their unclear_items.

    raw_text is the PRODUCT NAME PART ONLY (e.g. "raan"), not the full
    raw line with quantity (e.g. "raan -5"). The dashboard strips the
    quantity before sending. This ensures the alias matches correctly
    in both _lookup_alias() and future retroactive patches.
    """
    raw_text  = payload.get("raw_text", "").strip().lower()
    canonical = payload.get("canonical_product_name", "").strip()

    if not raw_text or not canonical:
        raise HTTPException(status_code=400, detail="raw_text and canonical_product_name are required")

    valid_names = {display for display, _, _ in PRODUCT_DEFINITIONS}
    if canonical not in valid_names:
        raise HTTPException(status_code=400, detail=f"'{canonical}' is not a valid product name")

    # Find unit for this canonical product
    unit = next((u for d, u, _ in PRODUCT_DEFINITIONS if d == canonical), "kg")

    # Upsert alias — key is product name only (no quantity)
    existing = db.query(UnclearItemAlias).filter(UnclearItemAlias.raw_text == raw_text).first()
    if existing:
        existing.canonical_product_name = canonical
        existing.updated_at = datetime.now(IST)
    else:
        db.add(UnclearItemAlias(raw_text=raw_text, canonical_product_name=canonical))
    db.commit()

    # Retroactive patch — scan all orders with unclear_items
    # Match by extracting the product name part from each raw line,
    # then compare against raw_text (product name only).
    orders_to_patch = (
        db.query(Order)
        .filter(
            Order.unclear_items.isnot(None),
            Order.unclear_items != "[]",
            Order.unclear_items != "null",
            Order.is_cancelled == False,
        )
        .all()
    )

    patched_count = 0
    for order in orders_to_patch:
        try:
            unclear    = json.loads(order.unclear_items or "[]")
            remaining  = []
            qty_to_add = 0.0

            for raw_line in unclear:
                product_name, qty = _extract_product_name(raw_line)
                if product_name == raw_text:
                    qty_to_add += qty
                else:
                    remaining.append(raw_line)

            if qty_to_add > 0:
                parsed = json.loads(order.parsed_items or "[]")
                merged = False
                for item in parsed:
                    if item["product"] == canonical and item["unit"] == unit:
                        item["quantity"] += qty_to_add
                        merged = True
                        break
                if not merged:
                    parsed.append({"product": canonical, "quantity": qty_to_add, "unit": unit})
                order.parsed_items  = json.dumps(parsed)
                order.unclear_items = json.dumps(remaining) if remaining else None
                patched_count += 1

        except Exception as e:
            print(f"⚠️ Retroactive patch failed for order {order.id}: {e}")
            continue

    db.commit()

    return {
        "status"        : "ok",
        "alias_saved"   : True,
        "orders_patched": patched_count,
        "raw_text"      : raw_text,
        "mapped_to"     : canonical,
    }


@router.delete("/unclear-items/aliases/{alias_id}")
def delete_alias(
    alias_id: int,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """Delete a saved alias."""
    alias = db.query(UnclearItemAlias).filter(UnclearItemAlias.id == alias_id).first()
    if not alias:
        raise HTTPException(status_code=404, detail="Alias not found")
    db.delete(alias)
    db.commit()
    return {"status": "deleted", "id": alias_id}


# ── Test notifications ────────────────────────────────────────────────────────

from app.services.pending_notifier import (
    send_customer_reminders,
    notify_salespersons_pending,
    send_management_summary,
)
from app.services.reporter import send_daily_report

@router.post("/test-notifications/customer-reminders")
def test_customer_reminders(db: Session = Depends(get_db), username: str = Depends(require_auth)):
    send_customer_reminders(db)
    return {"status": "sent"}

@router.post("/test-notifications/salesperson-pending")
def test_salesperson_pending(db: Session = Depends(get_db), username: str = Depends(require_auth)):
    notify_salespersons_pending(db)
    return {"status": "sent"}

@router.post("/test-notifications/management-summary")
def test_management_summary(db: Session = Depends(get_db), username: str = Depends(require_auth)):
    send_management_summary(db)
    return {"status": "sent"}

@router.post("/test-notifications/daily-report")
def test_daily_report(db: Session = Depends(get_db), username: str = Depends(require_auth)):
    send_daily_report(db)
    return {"status": "sent"}