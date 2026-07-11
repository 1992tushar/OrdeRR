"""
Wastage analytics route — kept in its own router (not dashboard.py) to avoid
entangling with the in-progress 5-Day-Close changes in that file. Included under
the /dashboard prefix in main.py, so the page lives at /dashboard/analytics/wastage.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from orderr_core.database import get_db
from orderr_core.auth import require_auth
from orderr_core.config import PLANT_NAME
from orderr_core.constants import IST
from orderr_core.services.order_service import get_current_business_date
from orderr_core.templating import make_templates

router = APIRouter()
templates = make_templates()


@router.get("/analytics/wastage", response_class=HTMLResponse)
def analytics_wastage(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """Tier-2 #5 — wastage KPI/trend (PLANT WASTAGE / WORKERS DAILY FOOD)."""
    from orderr_core.services import analytics_service

    today = get_current_business_date()
    data = analytics_service.wastage(db, today, days=30)

    return templates.TemplateResponse(
        request=request,
        name="dashboard_analytics_wastage.html",
        context={
            "plant_name": PLANT_NAME,
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "today_display": today.strftime("%d %b %Y"),
            "w": data,
            "analytics_view": "wastage",
        },
    )
