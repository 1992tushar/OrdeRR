"""
migrate_unclear_items.py
------------------------
Run once to:
  1. Add `unclear_items` (TEXT/JSON) column to existing `orders` table
  2. Create `unclear_item_aliases` table

Safe to run multiple times — uses IF NOT EXISTS / column existence checks.

Usage:
    python migrate_unclear_items.py
"""

import os
import sys
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///orderr.db")

engine = create_engine(DATABASE_URL)

with engine.connect() as conn:

    # ── 1. Add unclear_items column to orders ─────────────────────────────────
    if "postgresql" in DATABASE_URL:
        result = conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='orders' AND column_name='unclear_items'
        """))
        exists = result.fetchone() is not None
    else:
        # SQLite
        result = conn.execute(text("PRAGMA table_info(orders)"))
        cols = [row[1] for row in result.fetchall()]
        exists = "unclear_items" in cols

    if not exists:
        conn.execute(text("ALTER TABLE orders ADD COLUMN unclear_items TEXT"))
        print("✅ Added `unclear_items` column to orders table")
    else:
        print("ℹ️  `unclear_items` column already exists — skipping")

    # ── 2. Create unclear_item_aliases table ──────────────────────────────────
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS unclear_item_aliases (
            id                     SERIAL PRIMARY KEY,
            raw_text               TEXT NOT NULL UNIQUE,
            canonical_product_name TEXT NOT NULL,
            created_at             TIMESTAMP DEFAULT NOW(),
            updated_at             TIMESTAMP DEFAULT NOW()
        )
    """)) if "postgresql" in DATABASE_URL else conn.execute(text("""
        CREATE TABLE IF NOT EXISTS unclear_item_aliases (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_text               TEXT NOT NULL UNIQUE,
            canonical_product_name TEXT NOT NULL,
            created_at             TEXT DEFAULT (datetime('now')),
            updated_at             TEXT DEFAULT (datetime('now'))
        )
    """))
    print("✅ `unclear_item_aliases` table ready")

    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_unclear_item_aliases_raw_text
        ON unclear_item_aliases (raw_text)
    """))
    print("✅ Index on raw_text ready")

    conn.commit()

print("\n✅ Migration complete.")
