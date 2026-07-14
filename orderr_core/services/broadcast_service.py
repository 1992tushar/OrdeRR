"""
broadcast_service.py — owner-curated order-reminder broadcast list.

Replaces the scheduled 22:00 reminder-to-every-pending-customer (removed
2026-07-14 — it spammed the whole base). The owner maintains a list on the
📣 Broadcast screen and fires the approved `customer_order_reminder_v2` Meta
template (UTILITY category, so no 24-hour window) to everyone on it with one
button.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from orderr_core.constants import IST
from orderr_core.config import PLANT_NAME
from orderr_core.models.customer import Customer
from orderr_core.models.broadcast_recipient import BroadcastRecipient
from orderr_core.services.notifier import send_whatsapp_template

# Approved Meta template — {{1}} = restaurant name, {{2}} = plant name
TEMPLATE_CUSTOMER_REMINDER = "customer_order_reminder_v2"


def overview(db: Session) -> dict:
    """Members of the list (with customer details) + active customers with a
    phone number who can still be added."""
    rows = (db.query(BroadcastRecipient, Customer)
            .join(Customer, Customer.id == BroadcastRecipient.customer_id)
            .order_by(Customer.restaurant_name).all())
    members = [{
        "customer_id": c.id,
        "name": c.restaurant_name or c.owner_name or f"#{c.id}",
        "phone": c.phone_number,
        "area": c.area,
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

    return {"members": members, "addable": addable}


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
    """Send the reminder template to everyone on the list. Returns a summary
    {total, sent, failed, failures:[{name, reason}]}. A send counts as failed
    when Meta returns an error payload (no `messages` key)."""
    rows = (db.query(BroadcastRecipient, Customer)
            .join(Customer, Customer.id == BroadcastRecipient.customer_id)
            .order_by(Customer.restaurant_name).all())

    sent, failures = 0, []
    now = datetime.now(IST)
    for rec, cust in rows:
        name = cust.restaurant_name or cust.owner_name or f"#{cust.id}"
        if not cust.phone_number:
            failures.append({"name": name, "reason": "no phone number"})
            continue
        result = send_whatsapp_template(
            cust.phone_number, TEMPLATE_CUSTOMER_REMINDER, [name, PLANT_NAME])
        if result and ("messages" in result or result.get("status") == "simulated"):
            sent += 1
            rec.last_sent_at = now
        else:
            reason = (result or {}).get("error", {}).get("message", "send failed")
            failures.append({"name": name, "reason": reason})
    db.commit()

    return {"total": len(rows), "sent": sent,
            "failed": len(failures), "failures": failures}
