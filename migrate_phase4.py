"""
migrate_phase4.py
-----------------
Creates the order_sessions table for interactive ordering flow.
Safe to run multiple times (idempotent).

Usage:
    python migrate_phase4.py
"""

import os, sys
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///orderr.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

IS_SQLITE   = DATABASE_URL.startswith("sqlite")
IS_POSTGRES = DATABASE_URL.startswith("postgresql")

print(f"\n🔧 OrdeRR Phase 4 Migration — Interactive Ordering Sessions")
print(f"   Database: {'SQLite' if IS_SQLITE else 'PostgreSQL'}")
print(f"   URL: {DATABASE_URL[:50]}...\n")


def migrate_sqlite():
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    conn    = sqlite3.connect(db_path)
    cursor  = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_sessions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            phone      TEXT NOT NULL UNIQUE,
            step       TEXT NOT NULL,
            items_json TEXT NOT NULL DEFAULT '[]',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("✅ order_sessions table — ready")
    conn.commit()
    conn.close()
    print("\n✅ SQLite migration complete!\n")


def migrate_postgres():
    import psycopg2
    from urllib.parse import urlparse
    r    = urlparse(DATABASE_URL)
    conn = psycopg2.connect(
        dbname=r.path[1:], user=r.username,
        password=r.password, host=r.hostname, port=r.port
    )
    conn.autocommit = True
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_sessions (
            id         SERIAL PRIMARY KEY,
            phone      VARCHAR NOT NULL UNIQUE,
            step       VARCHAR NOT NULL,
            items_json TEXT NOT NULL DEFAULT '[]',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    print("✅ order_sessions table — ready")
    cursor.close()
    conn.close()
    print("\n✅ PostgreSQL migration complete!\n")


if __name__ == "__main__":
    try:
        if IS_SQLITE:     migrate_sqlite()
        elif IS_POSTGRES: migrate_postgres()
        else:
            print(f"❌ Unknown DB type"); sys.exit(1)
    except Exception as e:
        print(f"\n❌ Migration failed: {e}"); sys.exit(1)