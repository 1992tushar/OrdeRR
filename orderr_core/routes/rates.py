"""
routes/rates.py

POST /webhook/rates              — OrdeRR (or whoever forwards the WhatsApp
                                    rate message) calls this over HTTP at
                                    runtime. Billing parses it and writes.
GET  /dashboard/rates            — mobile-first form, every active product,
                                    yesterday's (or most recent) rate pre-filled.
POST /dashboard/rates            — saves edited/confirmed rates for today.
GET  /dashboard/rates/unclear    — list of unresolved unclear rate lines.
POST /dashboard/rates/unclear/{id}/resolve — manager resolves one line.
"""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import select

from orderr_core.services.order_service import get_current_business_date_str

from orderr_core.database import get_db
from orderr_core.auth import require_auth
from orderr_core.models.daily_rate import DailyRate
from orderr_core.models.rate_unclear import RateUnclearItem
from orderr_core.services.rate_parser import parse_rate_message, ACTIVE_PRODUCTS
from orderr_core.services.rate_lookup import get_rate

router = APIRouter()
templates = Jinja2Templates(directory="orderr_core/templates")


def _today() -> date:
    return date.fromisoformat(get_current_business_date_str())


# ── Webhook: receives the forwarded rate message ──────────────────────────
@router.post("/webhook/rates")
async def webhook_rates(request: Request, db: Session = Depends(get_db)):
    """
    Body can be JSON {"message": "..."} or a plain text body — accept both
    so OrdeRR's forwarding call doesn't need a special content-type.
    """
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        message = body.get("message", "")
    else:
        raw = await request.body()
        message = raw.decode("utf-8", errors="ignore")

    today = _today()
    parsed = parse_rate_message(message)

    written = []
    for item in parsed["confirmed"]:
        row = DailyRate(
            product=item["product"],
            business_date=today,
            rate_per_unit=item["rate"],
            unit=item["unit"],
            source="whatsapp",
            created_by="webhook",
        )
        db.add(row)
        written.append(item["product"])

    queued = []
    for u in parsed["unclear"]:
        row = RateUnclearItem(
            raw_line=u["raw_line"],
            business_date=today,
            reason=u["reason"],
        )
        db.add(row)
        queued.append(u["raw_line"])

    db.commit()

    return {
        "business_date": today.isoformat(),
        "confirmed_count": len(written),
        "confirmed_products": written,
        "unclear_count": len(queued),
        "unclear_lines": queued,
    }


# ── Dashboard: rate entry form ─────────────────────────────────────────────
@router.get("/dashboard/rates", response_class=HTMLResponse)
async def dashboard_rates_form(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_auth),
):
    today = _today()
    rows = []
    for display_name, default_unit in ACTIVE_PRODUCTS:
        result = get_rate(db, display_name, today)
        rows.append({
            "product": display_name,
            "unit": result.unit or default_unit,
            "rate": result.rate_per_unit,  # may be None if truly never set
            "not_confirmed_today": result.not_confirmed_today,
            "rate_date": result.rate_date.isoformat() if result.rate_date else None,
        })


    return templates.TemplateResponse(
    request,
    "dashboard_rates.html",
    {"rows": rows, "today": today.isoformat()},
    )


@router.post("/dashboard/rates")
async def dashboard_rates_save(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_auth),
):
    form = await request.form()
    today = _today()

    for display_name, default_unit in ACTIVE_PRODUCTS:
        key = f"rate__{display_name}"
        if key not in form:
            continue
        raw_value = (form.get(key) or "").strip()
        if not raw_value:
            # Left blank on purpose — don't write a ₹0 row, just skip;
            # the existing "most recent prior rate" fallback still applies.
            continue
        try:
            rate_value = float(raw_value)
        except ValueError:
            continue
        if rate_value <= 0:
            # Rule: never ₹0. Skip rather than write a bad row.
            continue

        unit_key = f"unit__{display_name}"
        unit = (form.get(unit_key) or default_unit).strip()

        existing = db.execute(
            select(DailyRate).where(
                DailyRate.product == display_name,
                DailyRate.business_date == today,
            )
        ).scalars().first()

        if existing:
            existing.rate_per_unit = rate_value
            existing.unit = unit
            existing.source = "dashboard"
            existing.created_by = "dashboard"
        else:
            db.add(DailyRate(
                product=display_name,
                business_date=today,
                rate_per_unit=rate_value,
                unit=unit,
                source="dashboard",
                created_by="dashboard",
            ))

    db.commit()
    return RedirectResponse(url="/dashboard/rates", status_code=303)


# ── Dashboard: unclear rate-line queue ──────────────────────────────────────
@router.get("/dashboard/rates/unclear", response_class=HTMLResponse)
async def dashboard_unclear_list(
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_auth),
):
    items = db.execute(
        select(RateUnclearItem)
        .where(RateUnclearItem.resolved.is_(False))
        .order_by(RateUnclearItem.created_at.desc())
    ).scalars().all()

    
    return templates.TemplateResponse(
    request,
    "dashboard_unclear.html",
    {
        "items": items,
        "active_products": [p for p, _ in ACTIVE_PRODUCTS],
    },
    )




@router.post("/dashboard/rates/unclear/{item_id}/resolve")
async def dashboard_unclear_resolve(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _=Depends(require_auth),
):
    form = await request.form()
    product = (form.get("product") or "").strip()
    rate_raw = (form.get("rate") or "").strip()
    unit = (form.get("unit") or "kg").strip()

    item = db.get(RateUnclearItem, item_id)
    if item is None:
        return RedirectResponse(url="/dashboard/rates/unclear", status_code=303)

    if product and rate_raw:
        try:
            rate_value = float(rate_raw)
        except ValueError:
            rate_value = 0
        if rate_value > 0:
            db.add(DailyRate(
                product=product,
                business_date=item.business_date,
                rate_per_unit=rate_value,
                unit=unit,
                source="dashboard",
                created_by="unclear_resolve",
            ))
            item.resolved = True
            item.resolved_product = product
            item.resolved_rate = rate_value
            item.resolved_at = datetime.now(timezone.utc)

    db.commit()
    return RedirectResponse(url="/dashboard/rates/unclear", status_code=303)
