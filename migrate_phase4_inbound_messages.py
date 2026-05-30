"""
migrate_phase4_inbound_messages.py
------------------------------------
Adds the `inbound_messages` table to your existing Render PostgreSQL DB.
Safe to run multiple times (idempotent).

Usage:
    python migrate_phase4_inbound_messages.py

Or against Render DB directly:
    DATABASE_URL="postgresql://..." python migrate_phase4_inbound_messages.py
"""

import os, sys
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///orderr.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

IS_SQLITE   = DATABASE_URL.startswith("sqlite")
IS_POSTGRES = DATABASE_URL.startswith("postgresql")

print(f"\n{'='*60}")
print(f"  OrdeRR — Phase 4 Migration: Zero Order Loss Layer")
print(f"{'='*60}")
print(f"  DB type : {'SQLite' if IS_SQLITE else 'PostgreSQL'}\n")


def migrate_sqlite():
    import sqlite3
    db_path = DATABASE_URL.replace("sqlite:///", "")
    conn    = sqlite3.connect(db_path)
    cur     = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='inbound_messages'")
    if cur.fetchone():
        print("  ✓  inbound_messages already exists — checking columns...")
        _sqlite_add_missing(cur)
    else:
        print("  +  Creating inbound_messages table...")
        cur.execute("""
            CREATE TABLE inbound_messages (
                id                        INTEGER PRIMARY KEY AUTOINCREMENT,
                meta_message_id           TEXT UNIQUE,
                customer_phone            TEXT NOT NULL,
                raw_message               TEXT,
                payload_json              TEXT,
                message_type              TEXT,
                received_at               DATETIME DEFAULT CURRENT_TIMESTAMP,
                processing_status         TEXT NOT NULL DEFAULT 'RECEIVED',
                processing_attempts       INTEGER NOT NULL DEFAULT 0,
                last_retry_at             DATETIME,
                failure_reason            TEXT,
                parser_confidence         TEXT,
                linked_order_id           INTEGER,
                acknowledged_to_customer  INTEGER NOT NULL DEFAULT 0,
                ack_attempts              INTEGER NOT NULL DEFAULT 0,
                ack_failed                INTEGER NOT NULL DEFAULT 0,
                is_duplicate              INTEGER NOT NULL DEFAULT 0,
                created_at                DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at                DATETIME DEFAULT CURRENT_TIMESTAMP
            )""")
        for idx, col in [
            ("ix_inbound_meta",   "meta_message_id"),
            ("ix_inbound_phone",  "customer_phone"),
            ("ix_inbound_status", "processing_status"),
        ]:
            cur.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON inbound_messages ({col})")
        print("  ✓  Table created")

    conn.commit()
    conn.close()
    print("\n  Migration complete.\n")


def _sqlite_add_missing(cur):
    cur.execute("PRAGMA table_info(inbound_messages)")
    existing = {row[1] for row in cur.fetchall()}
    wanted = {
        "meta_message_id": "TEXT", "customer_phone": "TEXT NOT NULL DEFAULT ''",
        "raw_message": "TEXT", "payload_json": "TEXT", "message_type": "TEXT",
        "received_at": "DATETIME DEFAULT CURRENT_TIMESTAMP",
        "processing_status": "TEXT NOT NULL DEFAULT 'RECEIVED'",
        "processing_attempts": "INTEGER NOT NULL DEFAULT 0",
        "last_retry_at": "DATETIME", "failure_reason": "TEXT",
        "parser_confidence": "TEXT", "linked_order_id": "INTEGER",
        "acknowledged_to_customer": "INTEGER NOT NULL DEFAULT 0",
        "ack_attempts": "INTEGER NOT NULL DEFAULT 0",
        "ack_failed": "INTEGER NOT NULL DEFAULT 0",
        "is_duplicate": "INTEGER NOT NULL DEFAULT 0",
        "created_at": "DATETIME DEFAULT CURRENT_TIMESTAMP",
        "updated_at": "DATETIME DEFAULT CURRENT_TIMESTAMP",
    }
    for col, defn in wanted.items():
        if col not in existing:
            cur.execute(f"ALTER TABLE inbound_messages ADD COLUMN {col} {defn.replace(' UNIQUE','')}")
            print(f"  +  Added column: {col}")
        else:
            print(f"  ✓  {col}")


def migrate_postgres():
    import psycopg2
    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor()

    cur.execute("SELECT to_regclass('public.inbound_messages')")
    if cur.fetchone()[0]:
        print("  ✓  inbound_messages already exists — checking columns...")
        _postgres_add_missing(cur)
    else:
        print("  +  Creating inbound_messages table...")
        cur.execute("""
            CREATE TABLE inbound_messages (
                id                        SERIAL PRIMARY KEY,
                meta_message_id           TEXT UNIQUE,
                customer_phone            TEXT NOT NULL,
                raw_message               TEXT,
                payload_json              TEXT,
                message_type              TEXT,
                received_at               TIMESTAMPTZ DEFAULT NOW(),
                processing_status         TEXT NOT NULL DEFAULT 'RECEIVED',
                processing_attempts       INTEGER NOT NULL DEFAULT 0,
                last_retry_at             TIMESTAMPTZ,
                failure_reason            TEXT,
                parser_confidence         TEXT,
                linked_order_id           INTEGER,
                acknowledged_to_customer  BOOLEAN NOT NULL DEFAULT FALSE,
                ack_attempts              INTEGER NOT NULL DEFAULT 0,
                ack_failed                BOOLEAN NOT NULL DEFAULT FALSE,
                is_duplicate              BOOLEAN NOT NULL DEFAULT FALSE,
                created_at                TIMESTAMPTZ DEFAULT NOW(),
                updated_at                TIMESTAMPTZ DEFAULT NOW()
            )""")
        for idx, col in [
            ("ix_inbound_meta",   "meta_message_id"),
            ("ix_inbound_phone",  "customer_phone"),
            ("ix_inbound_status", "processing_status"),
        ]:
            cur.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON inbound_messages ({col})")
        print("  ✓  Table created")

    conn.commit()
    cur.close()
    conn.close()
    print("\n  Migration complete.\n")


def _postgres_add_missing(cur):
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='inbound_messages'")
    existing = {row[0] for row in cur.fetchall()}
    wanted = {
        "meta_message_id": "TEXT", "customer_phone": "TEXT NOT NULL DEFAULT ''",
        "raw_message": "TEXT", "payload_json": "TEXT", "message_type": "TEXT",
        "received_at": "TIMESTAMPTZ DEFAULT NOW()",
        "processing_status": "TEXT NOT NULL DEFAULT 'RECEIVED'",
        "processing_attempts": "INTEGER NOT NULL DEFAULT 0",
        "last_retry_at": "TIMESTAMPTZ", "failure_reason": "TEXT",
        "parser_confidence": "TEXT", "linked_order_id": "INTEGER",
        "acknowledged_to_customer": "BOOLEAN NOT NULL DEFAULT FALSE",
        "ack_attempts": "INTEGER NOT NULL DEFAULT 0",
        "ack_failed": "BOOLEAN NOT NULL DEFAULT FALSE",
        "is_duplicate": "BOOLEAN NOT NULL DEFAULT FALSE",
        "created_at": "TIMESTAMPTZ DEFAULT NOW()",
        "updated_at": "TIMESTAMPTZ DEFAULT NOW()",
    }
    for col, defn in wanted.items():
        if col not in existing:
            cur.execute(f"ALTER TABLE inbound_messages ADD COLUMN IF NOT EXISTS {col} {defn}")
            print(f"  +  Added column: {col}")
        else:
            print(f"  ✓  {col}")


if __name__ == "__main__":
    try:
        if IS_SQLITE:
            migrate_sqlite()
        elif IS_POSTGRES:
            migrate_postgres()
        else:
            print(f"  ✗  Unknown DB: {DATABASE_URL[:40]}")
            sys.exit(1)
    except Exception as e:
        print(f"\n  ✗  Migration failed: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)
