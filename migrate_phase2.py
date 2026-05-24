"""
migrate_phase2.py
-----------------
Run this ONCE on your existing database before deploying the new code.

It adds the new columns to the customers table and creates the
salespersons table. Safe to run on both SQLite (local) and
PostgreSQL (Render).

Usage:
    python migrate_phase2.py

The script is idempotent — running it twice won't break anything.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///orderr.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

IS_SQLITE = DATABASE_URL.startswith("sqlite")
IS_POSTGRES = DATABASE_URL.startswith("postgresql")

print(f"\n🔧 OrdeRR Phase 2 Migration")
print(f"   Database: {'SQLite' if IS_SQLITE else 'PostgreSQL'}")
print(f"   URL: {DATABASE_URL[:50]}...\n")


# ── SQLite migration ──────────────────────────────────────────────────────────

def migrate_sqlite():
    import sqlite3

    db_path = DATABASE_URL.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get existing columns in customers table
    cursor.execute("PRAGMA table_info(customers)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    # Create salespersons table if not exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS salespersons (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            phone      TEXT NOT NULL UNIQUE,
            active     INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("✅ salespersons table — ready")

    # Add new columns to customers (SQLite doesn't support ADD COLUMN IF NOT EXISTS)
    new_columns = {
        "area":                    "TEXT",
        "salesperson_id":          "INTEGER REFERENCES salespersons(id)",
        "is_daily_order_customer": "INTEGER NOT NULL DEFAULT 1",
    }

    for col_name, col_type in new_columns.items():
        if col_name not in existing_cols:
            cursor.execute(
                f"ALTER TABLE customers ADD COLUMN {col_name} {col_type}"
            )
            print(f"✅ customers.{col_name} — added")
        else:
            print(f"⏭  customers.{col_name} — already exists, skipped")

    conn.commit()
    conn.close()
    print("\n✅ SQLite migration complete!\n")


# ── PostgreSQL migration ──────────────────────────────────────────────────────

def migrate_postgres():
    import psycopg2
    from urllib.parse import urlparse

    result = urlparse(DATABASE_URL)
    conn = psycopg2.connect(
        dbname=result.path[1:],
        user=result.username,
        password=result.password,
        host=result.hostname,
        port=result.port
    )
    conn.autocommit = True
    cursor = conn.cursor()

    # Create salespersons table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS salespersons (
            id         SERIAL PRIMARY KEY,
            name       VARCHAR NOT NULL,
            phone      VARCHAR NOT NULL UNIQUE,
            active     BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    print("✅ salespersons table — ready")

    # Add columns using DO $$ blocks (idempotent)
    migrations = [
        ("area",
         "ALTER TABLE customers ADD COLUMN area VARCHAR"),

        ("salesperson_id",
         "ALTER TABLE customers ADD COLUMN salesperson_id INTEGER REFERENCES salespersons(id)"),

        ("is_daily_order_customer",
         "ALTER TABLE customers ADD COLUMN is_daily_order_customer BOOLEAN NOT NULL DEFAULT TRUE"),
    ]

    for col_name, alter_sql in migrations:
        cursor.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'customers' AND column_name = %s
        """, (col_name,))

        if cursor.fetchone() is None:
            cursor.execute(alter_sql)
            print(f"✅ customers.{col_name} — added")
        else:
            print(f"⏭  customers.{col_name} — already exists, skipped")

    cursor.close()
    conn.close()
    print("\n✅ PostgreSQL migration complete!\n")


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        if IS_SQLITE:
            migrate_sqlite()
        elif IS_POSTGRES:
            migrate_postgres()
        else:
            print(f"❌ Unknown database type: {DATABASE_URL}")
            sys.exit(1)
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        sys.exit(1)
