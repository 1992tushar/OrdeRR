from fastapi import FastAPI
from app.database import engine, Base, SessionLocal
from app.routes import webhook, dashboard
from app.services.reporter import send_morning_report, send_evening_report
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
import os

load_dotenv()

# Create all database tables automatically
Base.metadata.create_all(bind=engine)

# Initialize FastAPI app
app = FastAPI(
    title="OrdeRR",
    description="WhatsApp Order Automation for Fluffy Plant",
    version="1.0.0"
)

# Register routes
app.include_router(webhook.router, prefix="/webhook", tags=["Webhook"])
app.include_router(dashboard.router, prefix="/dashboard", tags=["Dashboard"])

# Health check endpoint
@app.get("/")
def root():
    return {
        "app": "OrdeRR",
        "plant": os.getenv("PLANT_NAME", "Fluffy"),
        "status": "running"
    }

# Scheduler jobs
def morning_report_job():
    """Runs automatically at 5am every day"""
    db = SessionLocal()
    try:
        print("\n⏰ AUTO SCHEDULER — 5AM Morning Report triggered")
        send_morning_report(db)
    finally:
        db.close()

def evening_report_job():
    """Runs automatically at 6pm every day"""
    db = SessionLocal()
    try:
        print("\n⏰ AUTO SCHEDULER — 6PM Evening Report triggered")
        send_evening_report(db)
    finally:
        db.close()

# Start scheduler when app starts
@app.on_event("startup")
def start_scheduler():
    scheduler = BackgroundScheduler()

    # Morning report — every day at 5:00 AM
    scheduler.add_job(
        morning_report_job,
        CronTrigger(hour=5, minute=0),
        id="morning_report",
        name="5AM Morning Report"
    )

    # Evening report — every day at 6:00 PM
    scheduler.add_job(
        evening_report_job,
        CronTrigger(hour=18, minute=0),
        id="evening_report",
        name="6PM Evening Report"
    )

    scheduler.start()
    print("\n✅ OrdeRR Scheduler Started!")
    print("   📅 Morning report → Every day at 5:00 AM")
    print("   📅 Evening report → Every day at 6:00 PM\n")

# Stop scheduler when app stops
@app.on_event("shutdown")
def stop_scheduler():
    print("\n🛑 OrdeRR Scheduler Stopped")