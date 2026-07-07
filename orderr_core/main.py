from contextlib import asynccontextmanager
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import os
from datetime import datetime, timezone, timedelta

from orderr_core.database import engine, Base, SessionLocal
from orderr_core.routes import webhook, dashboard
from orderr_core.routes.admin import router as admin_router
from orderr_core.routes.ledger import router as ledger_router          # ← ADD
# Billing module routers (absolute paths: /billing/*, /dashboard/*, /webhook/rates)
from orderr_core.routes.invoices import router as invoices_router
from orderr_core.routes.rates import router as rates_router
from orderr_core.routes.billing import router as billing_router
# Staff Ledger module router (absolute paths: /staff, /staff/api/*)
from orderr_core.routes.staff import router as staff_router
from orderr_core.services.reporter import send_daily_report
from orderr_core.services.pending_notifier import (
    send_customer_reminders,
    notify_salespersons_pending,
    send_management_summary,
)
from orderr_core.services.retry_scheduler import retry_failed_messages
from orderr_core.services.webhook_health import check_webhook_health

# Import ALL models — order matters for FK resolution
from orderr_core.models.salesperson import Salesperson
from orderr_core.models.customer import Customer
from orderr_core.models.order import Order
from orderr_core.models.inbound_message import InboundMessage  # ← reliability layer
from orderr_core.models.customer_product_alias import CustomerProductAlias  # noqa: F401
from orderr_core.models.customer_product_stats import CustomerProductStats  # noqa: F401  ← unit inference (FRD §5.1)

# Billing module models — billing owns these tables; shares OrdeRR's Base/metadata
from orderr_core.models.daily_rate import DailyRate                # noqa: F401
from orderr_core.models.rate_override import CustomerRateOverride  # noqa: F401
from orderr_core.models.actuals import OrderItemActual             # noqa: F401
from orderr_core.models.invoice import Invoice, InvoiceItem        # noqa: F401
from orderr_core.models.rate_unclear import RateUnclearItem        # noqa: F401
from orderr_core.models.ocr_unmatched import OcrUnmatchedLine      # noqa: F401

# Staff Ledger module models — owns employees/advances/leaves; shares Base/metadata
from orderr_core.models.employee import Employee                   # noqa: F401
from orderr_core.models.advance import Advance                     # noqa: F401
from orderr_core.models.advance_repayment import AdvanceRepayment   # noqa: F401
from orderr_core.models.leave import Leave                         # noqa: F401


Base.metadata.create_all(bind=engine)


def _ensure_leaves_paid_column():
    """
    Lightweight migration: add leaves.paid to a pre-existing table.
    create_all() only creates missing tables, never alters existing ones, so
    the complementary-leave column must be added explicitly on older DBs.
    Idempotent and safe on both SQLite (local) and PostgreSQL (prod).
    """
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "leaves" not in insp.get_table_names():
        return  # fresh DB — create_all already made the column
    cols = {c["name"] for c in insp.get_columns("leaves")}
    if "paid" in cols:
        return
    default = "false" if engine.dialect.name == "postgresql" else "0"
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE leaves ADD COLUMN paid BOOLEAN NOT NULL DEFAULT {default}"))
    print("✅ Migration: added leaves.paid column")


_ensure_leaves_paid_column()


def _ensure_customer_outstanding_and_nullable_phone():
    """
    Lightweight migration for the customer-outstanding import feature:

      1. Add `customers.outstanding` (receivables snapshot) if missing.
      2. Drop the NOT NULL constraint on `customers.phone_number` so customers
         imported from the outstanding sheet without a phone number can be
         stored (they're flagged RED on the dashboard).

    create_all() only creates missing tables, never alters existing ones, so
    both changes must be applied explicitly on older DBs. Idempotent and safe
    on both SQLite (local) and PostgreSQL (prod).
    """
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "customers" not in insp.get_table_names():
        return  # fresh DB — create_all already made the current schema

    dialect = engine.dialect.name
    cols = {c["name"]: c for c in insp.get_columns("customers")}

    # ── 1. add outstanding column ──────────────────────────────────────────
    if "outstanding" not in cols:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE customers ADD COLUMN outstanding "
                "NUMERIC(12,2) NOT NULL DEFAULT 0"
            ))
        print("✅ Migration: added customers.outstanding column")

    # ── 2. make phone_number nullable ──────────────────────────────────────
    phone_col = cols.get("phone_number")
    phone_is_notnull = phone_col is not None and not phone_col.get("nullable", True)

    if phone_is_notnull:
        if dialect == "postgresql":
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE customers ALTER COLUMN phone_number DROP NOT NULL"
                ))
            print("✅ Migration: customers.phone_number is now nullable")
        elif dialect == "sqlite":
            # SQLite can't ALTER a column's NOT NULL in place — rebuild the
            # table. Batched inside one transaction; PRAGMA disables FK checks
            # during the swap. The tiny local test DB makes this cheap.
            with engine.begin() as conn:
                conn.execute(text("PRAGMA foreign_keys=OFF"))
                conn.execute(text("""
                    CREATE TABLE customers_new (
                        id INTEGER NOT NULL PRIMARY KEY,
                        restaurant_name VARCHAR,
                        owner_name VARCHAR,
                        phone_number VARCHAR,
                        address VARCHAR,
                        city VARCHAR,
                        onboarding_status VARCHAR,
                        is_active BOOLEAN,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        area VARCHAR,
                        salesperson_id INTEGER,
                        is_daily_order_customer BOOLEAN,
                        ledger_token VARCHAR,
                        outstanding NUMERIC(12,2) NOT NULL DEFAULT 0,
                        FOREIGN KEY(salesperson_id) REFERENCES salespersons (id)
                    )
                """))
                conn.execute(text("""
                    INSERT INTO customers_new (
                        id, restaurant_name, owner_name, phone_number, address,
                        city, onboarding_status, is_active, created_at, area,
                        salesperson_id, is_daily_order_customer, ledger_token,
                        outstanding
                    )
                    SELECT
                        id, restaurant_name, owner_name, phone_number, address,
                        city, onboarding_status, is_active, created_at, area,
                        salesperson_id, is_daily_order_customer, ledger_token,
                        outstanding
                    FROM customers
                """))
                conn.execute(text("DROP TABLE customers"))
                conn.execute(text("ALTER TABLE customers_new RENAME TO customers"))
                # recreate the indexes create_all() originally made
                conn.execute(text(
                    "CREATE UNIQUE INDEX ix_customers_phone_number "
                    "ON customers (phone_number)"
                ))
                conn.execute(text(
                    "CREATE INDEX ix_customers_salesperson_id "
                    "ON customers (salesperson_id)"
                ))
                conn.execute(text(
                    "CREATE UNIQUE INDEX ix_customers_ledger_token "
                    "ON customers (ledger_token)"
                ))
                conn.execute(text(
                    "CREATE INDEX ix_customers_id ON customers (id)"
                ))
                conn.execute(text("PRAGMA foreign_keys=ON"))
            print("✅ Migration: rebuilt customers table (phone_number nullable)")


_ensure_customer_outstanding_and_nullable_phone()


def _ensure_invoice_vasy_columns():
    """
    Lightweight migration: add the Vasy-ERP sync columns to `invoices` so the
    nightly Vasy bot can mark what it has pushed (idempotency). create_all()
    won't alter an existing table, so add them explicitly. Idempotent; safe on
    both SQLite (local) and PostgreSQL (prod).
    """
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "invoices" not in insp.get_table_names():
        return  # fresh DB — create_all already made the current schema

    cols = {c["name"] for c in insp.get_columns("invoices")}
    ts_type = "TIMESTAMPTZ" if engine.dialect.name == "postgresql" else "DATETIME"
    additions = [
        ("vasy_status",     "VARCHAR(20) NOT NULL DEFAULT 'pending'"),
        ("vasy_voucher_no", "VARCHAR(50)"),
        ("vasy_error",      "TEXT"),
        ("vasy_pushed_at",  ts_type),
    ]
    added = []
    with engine.begin() as conn:
        for name, ddl in additions:
            if name not in cols:
                conn.execute(text(f"ALTER TABLE invoices ADD COLUMN {name} {ddl}"))
                added.append(name)
    if added:
        print(f"✅ Migration: added invoices columns {added}")


_ensure_invoice_vasy_columns()

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
        CronTrigger(hour=22, minute=10, timezone="Asia/Kolkata"),
        id="customer_reminders", name="Customer Reminders at 22:00 IST",
    )

    # Salesperson notifications PROD: hour=23, minute=00
    scheduler.add_job(
        salesperson_notification_job,
        CronTrigger(hour=23, minute=15, timezone="Asia/Kolkata"),
        id="salesperson_notifications", name="Salesperson Notifications at 23:05 IST",
    )

    # Management summary — TEST: 10:32 IST  (PROD: hour=23, minute=00)
    scheduler.add_job(
        management_summary_job,
        CronTrigger(hour=23, minute=17, timezone="Asia/Kolkata"),
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
    print(f"   📋 Salesperson alerts    → Every day at 23:00 IST")
    print(f"   📊 Management summary    → Every day at 23:00 IST")
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
app.include_router(ledger_router,    prefix="/ledger",    tags=["Ledger"])   # ← ADD

# ── Billing module (merged) — routers carry their own absolute paths ──────────
app.include_router(invoices_router, tags=["Billing"])
app.include_router(rates_router,    tags=["Billing"])
app.include_router(billing_router,  tags=["Billing"])

# ── Staff Ledger module (merged) — router carries its own absolute paths ───────
app.include_router(staff_router,    tags=["Staff"])


@app.get("/")
def root():
    return {
        "app"   : "OrdeRR",
        "plant" : os.getenv("PLANT_NAME", "Fluffy"),
        "status": "running",
    }


@app.get("/health")
def health_check():
    from orderr_core.database import SessionLocal
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
