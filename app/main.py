from contextlib import asynccontextmanager

from fastapi import FastAPI

from dotenv import load_dotenv

# Load env vars ONCE here — no need to call load_dotenv() in other files
load_dotenv()

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import os

from app.database import engine, Base, SessionLocal

from app.routes import webhook, dashboard
from app.routes.admin import router as admin_router

from app.services.reporter import send_daily_report
from app.services.pending_notifier import (
    send_customer_reminders,
    notify_salespersons_pending,
    send_management_summary
)

# Import ALL models so SQLAlchemy creates tables in the right order
from app.models.salesperson import Salesperson   # must be before Customer
from app.models.order import Order
from app.models.customer import Customer

# Create all database tables automatically
Base.metadata.create_all(bind=engine)


# ── Scheduler job wrappers ────────────────────────────────────────────────────
# Each wrapper opens its own DB session — APScheduler runs these in a
# background thread where FastAPI's Depends(get_db) is not available.

def daily_report_job():
    """Runs at REPORT_TIME IST — consolidated order report to manager."""
    db = SessionLocal()
    try:
        print("\n⏰ AUTO SCHEDULER — Daily Report triggered")
        send_daily_report(db)
    finally:
        db.close()


def customer_reminders_job():
    """Runs at 22:00 IST — WhatsApp reminder to customers who haven't ordered."""
    db = SessionLocal()
    try:
        print("\n⏰ AUTO SCHEDULER — Customer Reminders triggered")
        send_customer_reminders(db)
    finally:
        db.close()


def salesperson_notification_job():
    """Runs at 23:05 IST — pending order list sent to each salesperson."""
    db = SessionLocal()
    try:
        print("\n⏰ AUTO SCHEDULER — Salesperson Notifications triggered")
        notify_salespersons_pending(db)
    finally:
        db.close()


def management_summary_job():
    """Runs at 23:10 IST — daily completion summary sent to manager."""
    db = SessionLocal()
    try:
        print("\n⏰ AUTO SCHEDULER — Management Summary triggered")
        send_management_summary(db)
    finally:
        db.close()


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):

    # ── Startup ───────────────────────────────────────────
    report_time = os.getenv("REPORT_TIME", "22:00")
    hour, minute = map(int, report_time.split(":"))

    scheduler = BackgroundScheduler()

    # Existing daily report (configurable time)
    scheduler.add_job(
        daily_report_job,
        CronTrigger(hour=hour, minute=minute, timezone="Asia/Kolkata"),
        id="daily_report",
        name=f"Daily Report at {report_time} IST"
    )

    # Phase 4 — customer reminders at 22:00 IST
    scheduler.add_job(
        customer_reminders_job,
        CronTrigger(hour=22, minute=0, timezone="Asia/Kolkata"),
        id="customer_reminders",
        name="Customer Order Reminders at 22:00 IST"
    )

    # Phase 3 — salesperson notifications at 23:05 IST
    scheduler.add_job(
        salesperson_notification_job,
        CronTrigger(hour=23, minute=5, timezone="Asia/Kolkata"),
        id="salesperson_notifications",
        name="Salesperson Pending Notifications at 23:05 IST"
    )

    # Phase 5 — management summary at 23:10 IST
    scheduler.add_job(
        management_summary_job,
        CronTrigger(hour=23, minute=10, timezone="Asia/Kolkata"),
        id="management_summary",
        name="Management Summary at 23:10 IST"
    )

    scheduler.start()
    app.state.scheduler = scheduler

    print("\n✅ OrdeRR Scheduler Started!")
    print(f"   📅 Daily report          → Every day at {report_time} IST")
    print(f"   🔔 Customer reminders    → Every day at 22:00 IST")
    print(f"   📋 Salesperson alerts    → Every day at 23:05 IST")
    print(f"   📊 Management summary    → Every day at 23:10 IST\n")

    yield

    # ── Shutdown ──────────────────────────────────────────
    app.state.scheduler.shutdown()
    print("\n🛑 OrdeRR Scheduler Stopped")


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="OrdeRR",
    description="WhatsApp Order Automation for Fluffy Plant",
    version="2.0.0",
    lifespan=lifespan
)

# Existing routes
app.include_router(
    webhook.router,
    prefix="/webhook",
    tags=["Webhook"]
)

app.include_router(
    dashboard.router,
    prefix="/dashboard",
    tags=["Dashboard"]
)

# New admin routes
app.include_router(
    admin_router,
    prefix="/admin",
    tags=["Admin"]
)


@app.get("/")
def root():
    return {
        "app": "OrdeRR",
        "plant": os.getenv("PLANT_NAME", "Fluffy"),
        "status": "running"
    }
