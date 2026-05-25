"""
migrate_phase3.py
-----------------
Adds is_cancelled and cancelled_at columns to the orders table.
Safe to run multiple times (idempotent).

Usage:
    python migrate_phase3.py
    # or against Render:
    $env:DATABASE_URL="postgresql://..."; python migrate_phase3.py
"""

import os, sys
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///orderr.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

IS_SQLITE   = DATABASE_URL.startswith("sqlite")
IS_POSTGRES = DATABASE_URL.startswith("postgresql")

print(f"\n🔧 OrdeRR Phase 3 Migration")
print(f"   Database: {'SQLite' if IS_SQLITE else 'PostgreSQL'}")
print(f"   URL: {DATABASE_URL[:50]}...\n")


def migrate_sqlite():
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    conn    = sqlite3.connect(db_path)
    cursor  = conn.cursor()

    cursor.execute("PRAGMA table_info(orders)")
    existing = {row[1] for row in cursor.fetchall()}

    new_columns = {
        "is_cancelled": "INTEGER NOT NULL DEFAULT 0",
        "cancelled_at": "DATETIME",
    }
    for col, definition in new_columns.items():
        if col not in existing:
            cursor.execute(f"ALTER TABLE orders ADD COLUMN {col} {definition}")
            print(f"✅ orders.{col} — added")
        else:
            print(f"⏭  orders.{col} — already exists, skipped")

    conn.commit(); conn.close()
    print("\n✅ SQLite migration complete!\n")


def migrate_postgres():
    import psycopg2
    from urllib.parse import urlparse
    r    = urlparse(DATABASE_URL)
    conn = psycopg2.connect(dbname=r.path[1:], user=r.username, password=r.password, host=r.hostname, port=r.port)
    conn.autocommit = True
    cursor = conn.cursor()

    migrations = [
        ("is_cancelled", "ALTER TABLE orders ADD COLUMN is_cancelled BOOLEAN NOT NULL DEFAULT FALSE"),
        ("cancelled_at",  "ALTER TABLE orders ADD COLUMN cancelled_at TIMESTAMPTZ"),
    ]
    for col, sql in migrations:
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='orders' AND column_name=%s", (col,))
        if cursor.fetchone() is None:
            cursor.execute(sql)
            print(f"✅ orders.{col} — added")
        else:
            print(f"⏭  orders.{col} — already exists, skipped")

    cursor.close(); conn.close()
    print("\n✅ PostgreSQL migration complete!\n")


if __name__ == "__main__":
    try:
        if IS_SQLITE:   migrate_sqlite()
        elif IS_POSTGRES: migrate_postgres()
        else:
            print(f"❌ Unknown DB type"); sys.exit(1)
    except Exception as e:
        print(f"\n❌ Migration failed: {e}"); sys.exit(1)
