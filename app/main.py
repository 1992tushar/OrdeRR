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

from app.services.reporter import send_daily_report

# Import models so SQLAlchemy creates tables
from app.models.order import Order
from app.models.customer import Customer

# Create all database tables automatically
Base.metadata.create_all(bind=engine)


def daily_report_job():
    """Runs automatically once a day at configured REPORT_TIME IST"""

    db = SessionLocal()

    try:
        print("\n⏰ AUTO SCHEDULER — Daily Report triggered")
        send_daily_report(db)

    finally:
        db.close()


# Use lifespan instead of deprecated @app.on_event
@asynccontextmanager
async def lifespan(app: FastAPI):

    # ── Startup ──────────────────────────────────────────

    # Read from .env — format "HH:MM" in 24hr IST, e.g. "22:00"
    report_time = os.getenv("REPORT_TIME", "22:00")
    hour, minute = map(int, report_time.split(":"))

    scheduler = BackgroundScheduler()

    scheduler.add_job(
        daily_report_job,
        CronTrigger(hour=hour, minute=minute, timezone="Asia/Kolkata"),
        id="daily_report",
        name=f"Daily Report at {report_time} IST"
    )

    scheduler.start()

    # Store reference on app.state so shutdown can reach it
    app.state.scheduler = scheduler

    print("\n✅ OrdeRR Scheduler Started!")
    print(f"   📅 Daily report → Every day at {report_time} IST\n")

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