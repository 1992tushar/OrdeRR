# ── UNCLEAR ITEMS ENDPOINTS ───────────────────────────────────────────────────
# Add these routes to app/routes/admin.py
# Import at top of admin.py:
#   from app.models.unclear_item_alias import UnclearItemAlias
#   from app.services.template_parser import VALID_PRODUCT_NAMES
#   import json

"""
Paste these routes into app/routes/admin.py.

Required new imports at top of admin.py:
    from app.models.unclear_item_alias import UnclearItemAlias
    from app.services.template_parser import VALID_PRODUCT_NAMES
    import json  (already imported in most files)
"""

UNCLEAR_ROUTES = '''

@router.get("/unclear-items")
def get_unclear_items(db: Session = Depends(get_db), _=Depends(require_auth)):
    """
    Returns all orders that have unclear (unresolved) items.
    Each entry includes: order_id, customer_name, customer_phone,
    raw_message, unclear_items list, created_at, delivery_date.
    """
    from app.services.order_service import get_unclear_orders
    import json

    orders = get_unclear_orders(db)
    result = []
    for o in orders:
        unclear = json.loads(o.unclear_items) if o.unclear_items else []
        if not unclear:
            continue
        result.append({
            "order_id":       o.id,
            "customer_name":  o.customer_name,
            "customer_phone": o.customer_phone,
            "raw_message":    o.raw_message,
            "unclear_items":  unclear,
            "parsed_items":   json.loads(o.parsed_items) if o.parsed_items else [],
            "delivery_date":  o.delivery_date,
            "created_at":     o.created_at.isoformat() if o.created_at else None,
        })
    return result


@router.get("/unclear-items/aliases")
def get_aliases(db: Session = Depends(get_db), _=Depends(require_auth)):
    """List all saved aliases."""
    from app.models.unclear_item_alias import UnclearItemAlias
    aliases = db.query(UnclearItemAlias).order_by(UnclearItemAlias.raw_text).all()
    return [
        {
            "id":                     a.id,
            "raw_text":               a.raw_text,
            "canonical_product_name": a.canonical_product_name,
            "created_at":             a.created_at.isoformat() if a.created_at else None,
        }
        for a in aliases
    ]


@router.get("/product-names")
def get_product_names(_=Depends(require_auth)):
    """Returns the list of valid canonical product names for the alias dropdown."""
    from app.services.template_parser import VALID_PRODUCT_NAMES
    return sorted(list(VALID_PRODUCT_NAMES))


@router.post("/unclear-items/resolve")
def resolve_unclear_item(
    payload: dict,
    db: Session = Depends(get_db),
    _=Depends(require_auth),
):
    """
    Save a raw_text → canonical_product_name alias and retroactively
    update all past orders containing that raw text in their unclear_items.

    Payload: { "raw_text": str, "canonical_product_name": str }
    """
    import json
    from datetime import datetime, timezone, timedelta
    from app.models.unclear_item_alias import UnclearItemAlias
    from app.models.order import Order
    from app.services.template_parser import VALID_PRODUCT_NAMES, PRODUCT_DEFINITIONS

    raw_text      = payload.get("raw_text", "").strip().lower()
    canonical     = payload.get("canonical_product_name", "").strip()

    if not raw_text or not canonical:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="raw_text and canonical_product_name are required")

    if canonical not in VALID_PRODUCT_NAMES:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"'{canonical}' is not a valid product name")

    IST = timezone(timedelta(hours=5, minutes=30))

    # ── Upsert alias ──────────────────────────────────────────────────────────
    existing_alias = db.query(UnclearItemAlias).filter(
        UnclearItemAlias.raw_text == raw_text
    ).first()

    if existing_alias:
        existing_alias.canonical_product_name = canonical
        existing_alias.updated_at = datetime.now(IST)
    else:
        db.add(UnclearItemAlias(
            raw_text               = raw_text,
            canonical_product_name = canonical,
        ))
    db.commit()

    # ── Retroactive update ────────────────────────────────────────────────────
    # Find the unit for this canonical product
    unit = "kg"
    for display, u, _ in PRODUCT_DEFINITIONS:
        if display == canonical:
            unit = u
            break

    # Find all non-cancelled orders with this raw_text in unclear_items
    orders_with_unclear = db.query(Order).filter(
        Order.unclear_items.isnot(None),
        Order.unclear_items != "[]",
        Order.is_cancelled == False,
    ).all()

    patched_count = 0
    for order in orders_with_unclear:
        try:
            unclear = json.loads(order.unclear_items or "[]")
            # Find matching unclear items (case-insensitive)
            remaining_unclear = []
            qty_to_add = 0.0

            for raw_line in unclear:
                # Extract quantity from the raw line the same way the parser does
                import re
                line_clean = re.sub(r'__+', '', raw_line).strip()
                line_clean = re.sub(r'(\d+)\s*k\b', r'\1 kg', line_clean)
                m = re.match(
                    r"^(.+?)\s*[-:]?\s*([\d\.]+)\s*(kg|kgs|nos|pcs|pc|pis|pieces|piece|k)?\s*$",
                    line_clean, re.IGNORECASE
                )
                if m:
                    extracted_name = m.group(1).strip().lower()
                    if extracted_name == raw_text:
                        try:
                            qty_to_add += float(m.group(2))
                        except ValueError:
                            qty_to_add += 1.0
                        # Don't add to remaining_unclear — it's resolved
                        continue
                remaining_unclear.append(raw_line)

            if qty_to_add > 0:
                # Add to parsed_items
                parsed = json.loads(order.parsed_items or "[]")
                # Merge with existing if same product+unit
                merged = False
                for item in parsed:
                    if item["product"] == canonical and item["unit"] == unit:
                        item["quantity"] += qty_to_add
                        merged = True
                        break
                if not merged:
                    parsed.append({
                        "product":  canonical,
                        "quantity": qty_to_add,
                        "unit":     unit,
                    })
                order.parsed_items  = json.dumps(parsed)
                order.unclear_items = json.dumps(remaining_unclear) if remaining_unclear else None
                patched_count += 1

        except Exception as e:
            print(f"⚠️ Retroactive patch failed for order {order.id}: {e}")
            continue

    db.commit()

    return {
        "status":        "ok",
        "alias_saved":   True,
        "orders_patched": patched_count,
        "raw_text":      raw_text,
        "mapped_to":     canonical,
    }


@router.delete("/unclear-items/aliases/{alias_id}")
def delete_alias(
    alias_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_auth),
):
    """Delete a saved alias."""
    from app.models.unclear_item_alias import UnclearItemAlias
    from fastapi import HTTPException
    alias = db.query(UnclearItemAlias).filter(UnclearItemAlias.id == alias_id).first()
    if not alias:
        raise HTTPException(status_code=404, detail="Alias not found")
    db.delete(alias)
    db.commit()
    return {"status": "deleted", "id": alias_id}
'''
