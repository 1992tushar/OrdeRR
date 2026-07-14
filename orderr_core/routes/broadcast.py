"""
📣 Broadcast routes — owner-curated order-reminder list + manual send.
Mounted under /dashboard (like the reminders router). Replaces the removed
22:00 auto customer reminder.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from orderr_core.auth import require_auth
from orderr_core.config import PLANT_NAME
from orderr_core.constants import IST
from orderr_core.database import get_db
from orderr_core.services import broadcast_service
from orderr_core.templating import make_templates

router = APIRouter()
templates = make_templates()


@router.get("/broadcast", response_class=HTMLResponse)
def broadcast_screen(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """📣 Broadcast — manage the reminder list and send on demand."""
    from orderr_core.config import report_url
    data = broadcast_service.overview(db)
    return templates.TemplateResponse(
        request=request,
        name="dashboard_broadcast.html",
        context={
            "plant_name": PLANT_NAME,
            "current_time": datetime.now(IST).strftime("%d %b %Y, %I:%M %p"),
            "members": data["members"],
            "addable": data["addable"],
            "pending": data["pending"],
            "status_url": report_url(),
        },
    )


def _ok_or_400(err):
    if err:
        raise HTTPException(status_code=400, detail=err)
    return JSONResponse({"status": "ok"})


@router.post("/broadcast/add")
async def broadcast_add(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    body = await request.json()
    return _ok_or_400(broadcast_service.add(db, body.get("customer_id")))


@router.post("/broadcast/remove")
async def broadcast_remove(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    body = await request.json()
    return _ok_or_400(broadcast_service.remove(db, body.get("customer_id")))


@router.post("/broadcast/send")
def broadcast_send(
    db: Session = Depends(get_db),
    username: str = Depends(require_auth),
):
    """Send the reminder template to everyone on the list."""
    summary = broadcast_service.send_reminders(db)
    return JSONResponse(summary)
