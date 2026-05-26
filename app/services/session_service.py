import json
from sqlalchemy.orm import Session
from app.models.session import OrderSession


def get_session(db: Session, phone: str) -> OrderSession | None:
    return db.query(OrderSession).filter(OrderSession.phone == phone).first()


def create_or_reset_session(db: Session, phone: str) -> OrderSession:
    session = get_session(db, phone)
    if session:
        session.step       = "selecting_item"
        session.items_json = "[]"
    else:
        session = OrderSession(phone=phone, step="selecting_item", items_json="[]")
        db.add(session)
    db.commit()
    db.refresh(session)
    return session


def set_step(db: Session, phone: str, step: str):
    session = get_session(db, phone)
    if session:
        session.step = step
        db.commit()


def add_item(db: Session, phone: str, product: str, unit: str) -> OrderSession:
    """Store selected product, move to awaiting_qty step."""
    session = get_session(db, phone)
    if not session:
        return None
    items = json.loads(session.items_json)
    # Temporarily store pending item without qty yet
    session.items_json = json.dumps(items)
    # Store the pending product in step field as "awaiting_qty:Wings:KG"
    session.step = f"awaiting_qty:{product}:{unit}"
    db.commit()
    db.refresh(session)
    return session


def confirm_quantity(db: Session, phone: str, quantity: float) -> OrderSession:
    """Extract pending product from step, add with quantity, move to add_more step."""
    session = get_session(db, phone)
    if not session or not session.step.startswith("awaiting_qty:"):
        return None
    _, product, unit = session.step.split(":", 2)
    items = json.loads(session.items_json)
    # Merge if same product already added
    for item in items:
        if item["product"] == product:
            item["quantity"] += quantity
            break
    else:
        items.append({"product": product, "quantity": quantity, "unit": unit})
    session.items_json = json.dumps(items)
    session.step       = "add_more"
    db.commit()
    db.refresh(session)
    return session


def get_items(db: Session, phone: str) -> list:
    session = get_session(db, phone)
    if not session:
        return []
    return json.loads(session.items_json)


def clear_session(db: Session, phone: str):
    session = get_session(db, phone)
    if session:
        db.delete(session)
        db.commit()