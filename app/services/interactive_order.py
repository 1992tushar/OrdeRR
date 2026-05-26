"""
interactive_order.py
--------------------
Handles the tap-based interactive ordering flow.
Called from webhook.py when message type is "interactive".

Flow:
  selecting_item  → customer taps product from list
  awaiting_qty    → customer taps or types a quantity
  add_more        → customer taps "Add more" or "Done"
  confirming      → customer taps "Confirm" or "Edit"
"""

import json
import os

from sqlalchemy.orm import Session

from app.services.session_service import (
    get_session,
    create_or_reset_session,
    add_item,
    confirm_quantity,
    get_items,
    clear_session,
)
from app.services.product_catalog import (
    get_interactive_list_items,
    get_product_by_id,
    get_quantity_buttons,
)
from app.services.notifier import (
    send_whatsapp_message,
    send_interactive_list,
    send_quick_reply_buttons,
)
from app.services.order_service import process_incoming_order

PLANT_NAME = os.getenv("PLANT_NAME", "Fluffy")


def start_interactive_order(db: Session, phone: str):
    """Called when customer sends 'order' or 'menu'. Starts fresh session."""
    create_or_reset_session(db, phone)
    send_interactive_list(phone, get_interactive_list_items())


def handle_interactive_message(db: Session, phone: str, interactive: dict):
    """
    Main router. Called from webhook when type == 'interactive'.
    interactive: the raw 'interactive' dict from Meta payload.
    """
    msg_type = interactive.get("type")  # "list_reply" or "button_reply"

    if msg_type == "list_reply":
        _handle_list_reply(db, phone, interactive["list_reply"])

    elif msg_type == "button_reply":
        _handle_button_reply(db, phone, interactive["button_reply"])


def _handle_list_reply(db: Session, phone: str, reply: dict):
    """Customer tapped a product from the list."""
    product_id = reply.get("id", "")
    product    = get_product_by_id(product_id)

    if not product:
        send_whatsapp_message(phone, "⚠️ Sorry, that item wasn't recognised. Please type *order* to start again.")
        return

    # Store selection, move to awaiting_qty
    add_item(db, phone, product["name"], product["unit"])

    # Send quantity buttons
    send_quick_reply_buttons(
        phone,
        body_text=f"How many KG of *{product['name']}*?",
        buttons=get_quantity_buttons(product["name"]),
    )


def _handle_button_reply(db: Session, phone: str, reply: dict):
    """Customer tapped a quick-reply button."""
    btn_id    = reply.get("id", "")
    btn_title = reply.get("title", "")
    session   = get_session(db, phone)

    if not session:
        send_whatsapp_message(phone, "⚠️ Session expired. Please type *order* to start again.")
        return

    # ── Quantity button ───────────────────────────────────────────────────────
    if btn_id.startswith("qty_") and session.step.startswith("awaiting_qty:"):

        if btn_id == "qty_custom":
            # Ask them to type a number
            send_whatsapp_message(phone, "Please reply with the quantity in KG (e.g. *3* or *7.5*)")
            # Step stays as awaiting_qty so the next text message is caught
            return

        qty = float(btn_id.replace("qty_", ""))
        _after_quantity(db, phone, qty)

    # ── Add more ──────────────────────────────────────────────────────────────
    elif btn_id == "add_more":
        send_interactive_list(phone, get_interactive_list_items())

    # ── Done — show summary + confirm ─────────────────────────────────────────
    elif btn_id == "done":
        _send_summary_and_confirm(db, phone)

    # ── Confirm order ─────────────────────────────────────────────────────────
    elif btn_id == "confirm_order":
        _finalise_order(db, phone)

    # ── Edit — restart ────────────────────────────────────────────────────────
    elif btn_id == "edit_order":
        create_or_reset_session(db, phone)
        send_interactive_list(phone, get_interactive_list_items())


def handle_custom_quantity_text(db: Session, phone: str, message: str):
    """
    Called from webhook when a plain-text message arrives and
    the customer's session step is awaiting_qty (they chose 'Other qty').
    """
    session = get_session(db, phone)
    if not session or not session.step.startswith("awaiting_qty:"):
        return False  # Not in qty-entry mode — let normal flow handle it

    try:
        qty = float(message.strip().replace("kg", "").replace("KG", "").strip())
        if qty <= 0:
            raise ValueError
    except ValueError:
        send_whatsapp_message(phone, "⚠️ Please send a valid number (e.g. *3* or *7.5*)")
        return True  # Consumed — don't pass to normal order parser

    _after_quantity(db, phone, qty)
    return True  # Consumed


# ── Internal helpers ──────────────────────────────────────────────────────────

def _after_quantity(db: Session, phone: str, qty: float):
    confirm_quantity(db, phone, qty)
    send_quick_reply_buttons(
        phone,
        body_text="Got it! Anything else to add?",
        buttons=[
            {"id": "add_more", "title": "➕ Add item"},
            {"id": "done",     "title": "✅ Done"},
        ],
    )


def _send_summary_and_confirm(db: Session, phone: str):
    items = get_items(db, phone)
    if not items:
        send_whatsapp_message(phone, "⚠️ No items in your order. Type *order* to start.")
        return

    lines = "\n".join(
        f"• {i['product']} — {i['quantity']} {i['unit']}"
        for i in items
    )
    send_quick_reply_buttons(
        phone,
        body_text=f"📋 *Your Order:*\n\n{lines}\n\nConfirm?",
        buttons=[
            {"id": "confirm_order", "title": "✅ Confirm"},
            {"id": "edit_order",    "title": "✏️ Edit"},
        ],
    )


def _finalise_order(db: Session, phone: str):
    items = get_items(db, phone)
    if not items:
        send_whatsapp_message(phone, "⚠️ No items found. Type *order* to start again.")
        return

    # Build a structured message string and pass through existing pipeline
    # This reuses ALL existing save/confirm/notify logic — no duplication
    order_text = "\n".join(
        f"{i['product']} {i['quantity']} {i['unit']}"
        for i in items
    )

    clear_session(db, phone)

    # process_incoming_order handles save + confirmation + manager alert
    process_incoming_order(
        db=db,
        customer_phone=phone,
        message=order_text,
        is_photo=False,
    )
