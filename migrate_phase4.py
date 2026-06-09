import os
import sqlite3
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///orderr.db")
DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

IS_SQLITE   = DATABASE_URL.startswith("sqlite")
IS_POSTGRES = DATABASE_URL.startswith("postgresql")


def migrate_sqlite():
    db_path = DATABASE_URL.replace("sqlite:///", "")
    conn   = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(orders)")
    existing = {row[1] for row in cursor.fetchall()}

    new_columns = {
        "business_date":        "ALTER TABLE orders ADD COLUMN business_date TEXT",
        "is_next_day_override": "ALTER TABLE orders ADD COLUMN is_next_day_override BOOLEAN DEFAULT 0",
    }
    for col, sql in new_columns.items():
        if col not in existing:
            cursor.execute(sql)
            print(f"  Added column: {col}")
        else:
            print(f"  Already exists: {col}")

    # Backfill business_date for existing orders
    print("Backfilling business_date...")
    cursor.execute("""
        UPDATE orders
        SET business_date = CASE
            WHEN CAST(strftime('%H', datetime(created_at, '+5 hours', '+30 minutes')) AS INTEGER) >= 20
            THEN date(datetime(created_at, '+5 hours', '+30 minutes', '+1 day'))
            ELSE date(datetime(created_at, '+5 hours', '+30 minutes'))
        END
        WHERE business_date IS NULL
    """)
    print(f"  Backfilled {cursor.rowcount} rows")

    conn.commit()
    conn.close()
    print("SQLite migration complete.")


def migrate_postgres():
    import psycopg2
    r    = urlparse(DATABASE_URL)
    conn = psycopg2.connect(
        dbname=r.path[1:], user=r.username,
        password=r.password, host=r.hostname, port=r.port
    )
    cursor = conn.cursor()

    migrations = [
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS business_date TEXT",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS is_next_day_override BOOLEAN DEFAULT FALSE",
    ]
    for sql in migrations:
        cursor.execute(sql)
        print(f"  Ran: {sql}")

    # Backfill business_date for existing orders
    print("Backfilling business_date...")
    cursor.execute("""
        UPDATE orders
        SET business_date = CASE
            WHEN EXTRACT(HOUR FROM (created_at AT TIME ZONE 'Asia/Kolkata')) >= 20
            THEN ((created_at AT TIME ZONE 'Asia/Kolkata') + INTERVAL '1 day')::date::text
            ELSE (created_at AT TIME ZONE 'Asia/Kolkata')::date::text
        END
        WHERE business_date IS NULL
    """)
    print(f"  Backfilled {cursor.rowcount} rows")

    conn.commit()
    cursor.close()
    conn.close()
    print("PostgreSQL migration complete.")


if __name__ == "__main__":
    if IS_SQLITE:
        migrate_sqlite()
    elif IS_POSTGRES:
        migrate_postgres()
    else:
        print("Unknown database type.")