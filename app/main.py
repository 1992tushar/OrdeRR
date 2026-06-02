from contextlib import asynccontextmanager
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import os
from datetime import datetime, timezone, timedelta

from app.database import engine, Base, SessionLocal
from app.routes import webhook, dashboard
from app.routes.admin import router as admin_router
from app.services.reporter import send_daily_report
from app.services.pending_notifier import (
    send_customer_reminders,
    notify_salespersons_pending,
    send_management_summary,
)
from app.services.retry_scheduler import retry_failed_messages
from app.services.webhook_health import check_webhook_health

# Import ALL models — order matters for FK resolution
from app.models.salesperson import Salesperson
from app.models.customer import Customer
from app.models.order import Order
from app.models.inbound_message import InboundMessage  # ← reliability layer

Base.metadata.create_all(bind=engine)

IST = timezone(timedelta(hours=5, minutes=30))

# Track last report time for health check
_last_report_time: str = "Never"


# ── Scheduler job wrappers ────────────────────────────────────────────────────

def daily_report_job():
    global _last_report_time
    db = SessionLocal()
    try:
        print("\n⏰ AUTO SCHEDULER — Daily Report triggered")
        send_daily_report(db)
        _last_report_time = datetime.now(IST).strftime("%d %b %Y %I:%M %p IST")
    finally:
        db.close()


def customer_reminders_job():
    db = SessionLocal()
    try:
        print("\n⏰ AUTO SCHEDULER — Customer Reminders triggered")
        send_customer_reminders(db)
    finally:
        db.close()


def salesperson_notification_job():
    db = SessionLocal()
    try:
        print("\n⏰ AUTO SCHEDULER — Salesperson Notifications triggered")
        notify_salespersons_pending(db)
    finally:
        db.close()


def management_summary_job():
    db = SessionLocal()
    try:
        print("\n⏰ AUTO SCHEDULER — Management Summary triggered")
        send_management_summary(db)
    finally:
        db.close()


def retry_failed_messages_job():
    retry_failed_messages()  # manages its own DB session internally


def webhook_health_job():
    check_webhook_health()



# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):

    report_time  = os.getenv("REPORT_TIME", "22:00")
    hour, minute = map(int, report_time.split(":"))
    scheduler    = BackgroundScheduler()

    # Daily report (configurable via REPORT_TIME env var)
    scheduler.add_job(
        daily_report_job,
        CronTrigger(hour=hour, minute=minute, timezone="Asia/Kolkata"),
        id="daily_report", name=f"Daily Report at {report_time} IST",
    )

    # Customer reminders — PROD: hour=22, minute=50
    scheduler.add_job(
        customer_reminders_job,
        CronTrigger(hour=22, minute=00, timezone="Asia/Kolkata"),
        id="customer_reminders", name="Customer Reminders at 22:00 IST",
    )

    # Salesperson notifications PROD: hour=23, minute=00
    scheduler.add_job(
        salesperson_notification_job,
        CronTrigger(hour=23, minute=00, timezone="Asia/Kolkata"),
        id="salesperson_notifications", name="Salesperson Notifications at 23:05 IST",
    )

    # Management summary — TEST: 10:32 IST  (PROD: hour=23, minute=00)
    scheduler.add_job(
        management_summary_job,
        CronTrigger(hour=23, minute=00, timezone="Asia/Kolkata"),
        id="management_summary", name="Management Summary at 23:10 IST",
    )

    # Keep-alive ping — every 10 min (prevents Render spin-down)
    scheduler.add_job(
        lambda: print("💓 keep-alive ping"),
        IntervalTrigger(minutes=10),
        id="keep_alive", name="Keep-Alive Ping",
    )

    # Retry failed messages — every 1 min (reliability layer)
    scheduler.add_job(
        retry_failed_messages_job,
        IntervalTrigger(minutes=1),
        id="retry_failed_messages", name="Retry Failed Messages",
    )

    # Webhook health check — every 30 min (reliability layer)
    scheduler.add_job(
        webhook_health_job,
        IntervalTrigger(minutes=30),
        id="webhook_health", name="Webhook Health Check",
    )

    scheduler.start()
    app.state.scheduler = scheduler

    print("\n✅ OrdeRR Scheduler Started!")
    print(f"   📅 Daily report          → Every day at {report_time} IST")
    print(f"   🔔 Customer reminders    → Every day at 22:00 IST")
    print(f"   📋 Salesperson alerts    → Every day at 23:05 IST")
    print(f"   📊 Management summary    → Every day at 23:10 IST")
    print(f"   💓 Keep-alive ping       → Every 10 minutes")
    print(f"   🔁 Retry failed msgs     → Every 1 minute")
    print(f"   🩺 Webhook health check  → Every 30 minutes\n")

    yield

    app.state.scheduler.shutdown()
    print("\n🛑 OrdeRR Scheduler Stopped")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="OrdeRR",
    description="WhatsApp Order Automation for Fluffy Plant",
    version="2.2.0",
    lifespan=lifespan,
)

app.include_router(webhook.router,   prefix="/webhook",   tags=["Webhook"])
app.include_router(dashboard.router, prefix="/dashboard", tags=["Dashboard"])
app.include_router(admin_router,     prefix="/admin",     tags=["Admin"])


@app.get("/")
def root():
    return {
        "app"   : "OrdeRR",
        "plant" : os.getenv("PLANT_NAME", "Fluffy"),
        "status": "running",
    }


@app.get("/health")
def health_check():
    from app.database import SessionLocal
    from sqlalchemy import text

    db_status = "ok"
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
    except Exception as e:
        db_status = f"error: {str(e)}"

    scheduler    = app.state.scheduler if hasattr(app.state, "scheduler") else None
    job_count    = len(scheduler.get_jobs()) if scheduler else 0
    sched_status = "running" if (scheduler and scheduler.running) else "stopped"

    return {
        "status"          : "ok",
        "app"             : "OrdeRR",
        "plant"           : os.getenv("PLANT_NAME", "Fluffy"),
        "database"        : db_status,
        "scheduler"       : sched_status,
        "scheduler_jobs"  : job_count,
        "last_report_time": _last_report_time,
        "time_ist"        : datetime.now(IST).strftime("%d %b %Y %I:%M %p"),
    }