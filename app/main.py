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

from app.services.reporter import (
    send_morning_report,
    send_evening_report
)

# Import models so SQLAlchemy creates tables
from app.models.order import Order
from app.models.customer import Customer

# Create all database tables automatically
Base.metadata.create_all(bind=engine)


def morning_report_job():
    """Runs automatically at 5am IST every day"""

    db = SessionLocal()

    try:
        print("\n⏰ AUTO SCHEDULER — 5AM Morning Report triggered")
        send_morning_report(db)

    finally:
        db.close()


def evening_report_job():
    """Runs automatically at 6pm IST every day"""

    db = SessionLocal()

    try:
        print("\n⏰ AUTO SCHEDULER — 6PM Evening Report triggered")
        send_evening_report(db)

    finally:
        db.close()


# Use lifespan instead of deprecated @app.on_event
@asynccontextmanager
async def lifespan(app: FastAPI):

    # ── Startup ──────────────────────────────────────────
    scheduler = BackgroundScheduler()

    # Explicitly set Asia/Kolkata so jobs fire at IST regardless of server timezone
    scheduler.add_job(
        morning_report_job,
        CronTrigger(hour=5, minute=0, timezone="Asia/Kolkata"),
        id="morning_report",
        name="5AM Morning Report"
    )

    scheduler.add_job(
        evening_report_job,
        CronTrigger(hour=18, minute=0, timezone="Asia/Kolkata"),
        id="evening_report",
        name="6PM Evening Report"
    )

    scheduler.start()

    # Store reference on app.state so shutdown can reach it
    app.state.scheduler = scheduler

    print("\n✅ OrdeRR Scheduler Started!")
    print("   📅 Morning report → Every day at 5:00 AM IST")
    print("   📅 Evening report → Every day at 6:00 PM IST\n")

    yield

    # ── Shutdown ─────────────────────────────────────────
    app.state.scheduler.shutdown()
    print("\n🛑 OrdeRR Scheduler Stopped")


# Initialize FastAPI app
app = FastAPI(
    title="OrdeRR",
    description="WhatsApp Order Automation for Fluffy Plant",
    version="1.0.0",
    lifespan=lifespan
)

# Register routes
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


# Health check endpoint
@app.get("/")
def root():
    return {
        "app": "OrdeRR",
        "plant": os.getenv("PLANT_NAME", "Fluffy"),
        "status": "running"
    }