"""
broadcast_service.py — owner-curated order-reminder broadcast list.

Replaces the scheduled 22:00 reminder-to-every-pending-customer (removed
2026-07-14 — it spammed the whole base). The owner maintains a list on the
📣 Broadcast screen and fires the approved `customer_order_reminder_v2` Meta
template (UTILITY category, so no 24-hour window) to everyone on it with one
button.
"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from orderr_core.constants import IST
from orderr_core.config import PLANT_NAME
from orderr_core.models.customer import Customer
from orderr_core.models.broadcast_recipient import BroadcastRecipient
from orderr_core.models.inbound_message import InboundMessage
from orderr_core.services.customer_service import normalize_phone
from orderr_core.services.notifier import send_whatsapp_template, send_whatsapp_message

# Approved Meta template — {{1}} = restaurant name, {{2}} = plant name.
# MARKETING category (Meta reclassified it), so delivery is unreliable; it is
# only the fallback when the customer's 24-hour service window is closed.
TEMPLATE_CUSTOMER_REMINDER = "customer_order_reminder_v2"


def _reminder_text(name: str) -> str:
    """Free-form reminder — mirrors the approved template body."""
    return (f"Hi {name}, you haven't placed your order with {PLANT_NAME} yet "
            f"today. Reply with your order anytime.")


def _open_window_phones(db: Session) -> set[str]:
    """Normalized phones that messaged us in the last 24h — their WhatsApp
    service window is open, so a free-form message delivers (and is free,
    unlike paid template sends which Meta drops when prepaid balance is out)."""
    cutoff = datetime.now(IST) - timedelta(hours=24)
    rows = (db.query(InboundMessage.customer_phone)
            .filter(InboundMessage.received_at >= cutoff).distinct().all())
    return {normalize_phone(p) for (p,) in rows if p}


def overview(db: Session) -> dict:
    """Members of the list (with customer details + ordered-today state) +
    active customers with a phone number who can still be added. This list
    doubles as the daily roster for the live status page (/r/<key>)."""
    from orderr_core.dates import get_current_business_date
    from orderr_core.services.pending_orders import ordered_sets

    delivery_date = get_current_business_date()
    ordered_phones, invoiced_ids = ordered_sets(db, delivery_date)

    rows = (db.query(BroadcastRecipient, Customer)
            .join(Customer, Customer.id == BroadcastRecipient.customer_id)
            .order_by(Customer.restaurant_name).all())
    members = [{
        "customer_id": c.id,
        "name": c.restaurant_name or c.owner_name or f"#{c.id}",
        "phone": c.phone_number,
        "area": c.area,
        "ordered": (c.phone_number in ordered_phones) or (c.id in invoiced_ids),
        "last_sent": r.last_sent_at.astimezone(IST).strftime("%d %b, %I:%M %p")
                     if r.last_sent_at else None,
    } for r, c in rows]

    member_ids = {m["customer_id"] for m in members}
    addable = [{
        "customer_id": c.id,
        "name": c.restaurant_name or c.owner_name or f"#{c.id}",
        "area": c.area,
    } for c in (db.query(Customer)
                .filter(Customer.is_active == True,          # noqa: E712
                        Customer.phone_number != None)        # noqa: E711
                .order_by(Customer.restaurant_name).all())
        if c.id not in member_ids]

    pending = sum(1 for m in members if not m["ordered"])
    return {"members": members, "addable": addable, "pending": pending}


def add(db: Session, customer_id) -> str | None:
    """Add a customer to the list. Returns an error string or None."""
    try:
        cid = int(customer_id)
    except (TypeError, ValueError):
        return "Pick a customer to add."
    cust = db.query(Customer).filter(Customer.id == cid).first()
    if not cust:
        return "Customer not found."
    if not cust.phone_number:
        return f"{cust.restaurant_name or 'Customer'} has no phone number on record."
    if db.query(BroadcastRecipient).filter(BroadcastRecipient.customer_id == cid).first():
        return None  # already on the list — idempotent
    db.add(BroadcastRecipient(customer_id=cid))
    db.commit()
    return None


def remove(db: Session, customer_id) -> str | None:
    """Remove a customer from the list. Returns an error string or None."""
    try:
        cid = int(customer_id)
    except (TypeError, ValueError):
        return "Bad customer id."
    (db.query(BroadcastRecipient)
     .filter(BroadcastRecipient.customer_id == cid)
     .delete(synchronize_session=False))
    db.commit()
    return None


def send_reminders(db: Session) -> dict:
    """Send the reminder to list members who have NOT ordered yet for the
    current business date (the template says "you haven't placed your order
    yet" — nagging someone who already ordered would be wrong). Free-form
    text when the customer's 24-hour window is open (reliable + free), the
    approved template otherwise (paid; Meta drops it silently when prepaid
    balance is out, and the API still answers "accepted" — so template counts
    are attempts, not confirmed deliveries). Returns {total, sent, via_chat,
    via_template, skipped_ordered, failed, failures:[{name, reason}]}."""
    from orderr_core.dates import get_current_business_date
    from orderr_core.services.pending_orders import ordered_sets

    delivery_date = get_current_business_date()
    ordered_phones, invoiced_ids = ordered_sets(db, delivery_date)

    rows = (db.query(BroadcastRecipient, Customer)
            .join(Customer, Customer.id == BroadcastRecipient.customer_id)
            .order_by(Customer.restaurant_name).all())
    open_windows = _open_window_phones(db)

    via_chat, via_template, skipped_ordered, failures = 0, 0, 0, []
    now = datetime.now(IST)
    for rec, cust in rows:
        name = cust.restaurant_name or cust.owner_name or f"#{cust.id}"
        if not cust.phone_number:
            failures.append({"name": name, "reason": "no phone number"})
            continue
        if cust.phone_number in ordered_phones or cust.id in invoiced_ids:
            skipped_ordered += 1
            continue
        in_window = normalize_phone(cust.phone_number) in open_windows
        if in_window:
            result = send_whatsapp_message(cust.phone_number, _reminder_text(name))
        else:
            result = send_whatsapp_template(
                cust.phone_number, TEMPLATE_CUSTOMER_REMINDER, [name, PLANT_NAME])
        if result and ("messages" in result or result.get("status") == "simulated"):
            if in_window:
                via_chat += 1
            else:
                via_template += 1
            rec.last_sent_at = now
        else:
            reason = (result or {}).get("error", {}).get("message", "send failed")
            failures.append({"name": name, "reason": reason})
    db.commit()

    return {"total": len(rows), "sent": via_chat + via_template,
            "via_chat": via_chat, "via_template": via_template,
            "skipped_ordered": skipped_ordered,
            "failed": len(failures), "failures": failures}
