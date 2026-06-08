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

# ── Counts ────────────────────────────────────────────────────────────────────
run("COUNTS", """
    SELECT
        (SELECT COUNT(*) FROM orders)                                                        AS total_orders,
        (SELECT COUNT(*) FROM orders WHERE is_cancelled = false)                             AS active_orders,
        (SELECT COUNT(*) FROM orders WHERE unclear_items IS NOT NULL
                                      AND unclear_items != '[]')                             AS orders_with_unclear,
        (SELECT COUNT(*) FROM customers)                                                     AS total_customers,
        (SELECT COUNT(*) FROM customers WHERE is_active = true)                              AS active_customers,
        (SELECT COUNT(*) FROM salespersons)                                                  AS total_salespersons,
        (SELECT COUNT(*) FROM inbound_messages)                                              AS total_messages,
        (SELECT COUNT(*) FROM inbound_messages WHERE processing_status = 'FAILED')           AS failed_messages,
        (SELECT COUNT(*) FROM inbound_messages WHERE processing_status = 'MANUAL_REVIEW')   AS manual_review
""")

# ── Orders ────────────────────────────────────────────────────────────────────
run("ORDERS (last 20)", """
    SELECT id, customer_name, customer_phone, delivery_date, status,
           is_cancelled, is_unclear, unclear_items, parsed_items, created_at
    FROM orders
    ORDER BY created_at DESC
    LIMIT 20
""")

# ── Customers ─────────────────────────────────────────────────────────────────
run("CUSTOMERS", """
    SELECT id, restaurant_name, phone_number, area,
           is_active, is_daily_order_customer, salesperson_id, onboarding_status, created_at
    FROM customers
    ORDER BY created_at DESC
""")

# ── Salespersons ──────────────────────────────────────────────────────────────
run("SALESPERSONS", """
    SELECT id, name, phone, active, created_at
    FROM salespersons
    ORDER BY name
""")

# ── Inbound Messages ──────────────────────────────────────────────────────────
run("INBOUND MESSAGES (last 20)", """
    SELECT id, customer_phone, message_type, processing_status,
           processing_attempts, failure_reason, linked_order_id,
           is_duplicate, received_at
    FROM inbound_messages
    ORDER BY received_at DESC
    LIMIT 20
""")

# ── Unclear Item Aliases ──────────────────────────────────────────────────────
run("UNCLEAR ITEM ALIASES", """
    SELECT id, raw_text, canonical_product_name, created_at, updated_at
    FROM unclear_item_aliases
    ORDER BY raw_text
""")

# ── Orders table columns (migration check) ────────────────────────────────────
run("ORDERS TABLE COLUMNS", """
    SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_name = 'orders'
    ORDER BY ordinal_position
""")


conn.commit()
print("Changes committed.")

cur.close()
conn.close()
print("\n✅ Done.")