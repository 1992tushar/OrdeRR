# ============================================================
# CONFIG — change this one line to switch between DBs
# ============================================================
DB_TYPE = "local"   # "remote" = Postgres (Render), "local" = SQLite

POSTGRES_URL = "postgresql://orderr_db_user:VORCj0qqoRQZO3H7fGwurX4EBLPr0ww1@dpg-d881pu1kh4rs73c969f0-a.singapore-postgres.render.com/orderr_db"
SQLITE_PATH  = "C:\\Imp Data\\Personal\\OrdeRR\\orderr.db"   # path to your local sqlite file
# ============================================================

if DB_TYPE == "remote":
    import psycopg2
    conn = psycopg2.connect(POSTGRES_URL, sslmode="require")
elif DB_TYPE == "local":
    import sqlite3
    conn = sqlite3.connect(SQLITE_PATH)
else:
    raise ValueError("DB_TYPE must be 'remote' or 'local'")

cur = conn.cursor()


def adapt(query):
    """Postgres uses %s placeholders, SQLite uses ?. Auto-convert."""
    return query.replace("%s", "?") if DB_TYPE == "local" else query


def run(title, query, params=None):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print("=" * 70)
    try:
        cur.execute(adapt(query), params or ())
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        print(" | ".join(cols))
        print("-" * 70)
        for row in rows:
            print(" | ".join(str(v) if v is not None else "NULL" for v in row))
        print(f"  ({len(rows)} rows)")
    except Exception as e:
        print(f"ERROR: {e}")
        conn.rollback()


def run_write(title, query, params=None):
    """For INSERT/UPDATE/DELETE — commits and prints rows affected."""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print("=" * 70)
    try:
        cur.execute(adapt(query), params or ())
        conn.commit()
        print(f"  ✅ Done. Rows affected: {cur.rowcount}")
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
        conn.rollback()


def truncate_tables(tables):
    """
    Delete all rows from the given tables and reset auto-increment IDs.
    Postgres: TRUNCATE ... RESTART IDENTITY CASCADE
    SQLite:   DELETE FROM each table + reset sqlite_sequence
    """
    title = f"Delete all tables: {', '.join(tables)}"
    print(f"\n{'='*70}")
    print(f"  {title}")
    print("=" * 70)
    try:
        if DB_TYPE == "remote":
            cur.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE;")
        else:
            # sqlite_sequence only exists if some table uses AUTOINCREMENT
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sqlite_sequence';"
            )
            has_seq_table = cur.fetchone() is not None

            for t in tables:
                cur.execute(f"DELETE FROM {t};")
                if has_seq_table:
                    cur.execute("DELETE FROM sqlite_sequence WHERE name = ?;", (t,))
        conn.commit()
        print("  ✅ Done.")
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
        conn.rollback()


# ============================================================
# Run your queries here
# ============================================================

# truncate_tables([
#     "inbound_messages", "customers", "salespersons", "orders", "unclear_item_aliases",
#     "noise_phrases", "customer_product_aliases","customer_product_stats"
# ])




run("inbound_messages", "SELECT * FROM inbound_messages ORDER BY id DESC LIMIT 20;")
run("orders", "SELECT * FROM orders ORDER BY id DESC LIMIT 20;")
run("customers", "SELECT * FROM customers ORDER BY id DESC LIMIT 20;")
run("salespersons", "SELECT * FROM salespersons ORDER BY id DESC LIMIT 20;")
run("unclear_item_aliases", "SELECT * FROM unclear_item_aliases ORDER BY id DESC LIMIT 20;")
run("noise_phrases", "SELECT * FROM noise_phrases ORDER BY id DESC LIMIT 20;")
run("customer_product_aliases", "SELECT * FROM customer_product_aliases ORDER BY id DESC LIMIT 20;")
run("customer_product_stats", "SELECT * FROM customer_product_stats ORDER BY id DESC LIMIT 20;")



cur.close()
conn.close()
print("\n✅ Done.")