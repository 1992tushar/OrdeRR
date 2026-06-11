import psycopg2

DB_URL = "postgresql://orderr_db_user:VORCj0qqoRQZO3H7fGwurX4EBLPr0ww1@dpg-d881pu1kh4rs73c969f0-a.singapore-postgres.render.com/orderr_db"

conn = psycopg2.connect(DB_URL, sslmode="require")
cur  = conn.cursor()

def run(title, query, params=None):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print("="*70)
    try:
        cur.execute(query, params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        print(" | ".join(cols))
        print("-"*70)
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
    print("="*70)
    try:
        cur.execute(query, params)
        conn.commit()
        print(f"  ✅ Done. Rows affected: {cur.rowcount}")
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
        conn.rollback()

run_write("Delete all tables", """
    TRUNCATE orders, unclear_item_aliases, noise_phrases   RESTART IDENTITY CASCADE;
""")

run("inbound_messages", "SELECT * FROM inbound_messages ORDER BY id DESC LIMIT 20;")
run("orders", "SELECT * FROM orders ORDER BY id DESC LIMIT 20;")
run("customers", "SELECT * FROM customers ORDER BY id DESC LIMIT 20;")
run("salespersons", "SELECT * FROM salespersons ORDER BY id DESC LIMIT 20;")
run("unclear_item_aliases", "SELECT * FROM unclear_item_aliases ORDER BY id DESC LIMIT 20;")
run("noise_phrases", "SELECT * FROM noise_phrases ORDER BY id DESC LIMIT 20;")


cur.close()
conn.close()
print("\n✅ Done.")