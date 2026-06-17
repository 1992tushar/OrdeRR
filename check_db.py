import sqlite3

conn = sqlite3.connect("orderr.db")
cur = conn.cursor()

cur.execute("""
    SELECT id, customer_name, created_at, business_date, delivery_date FROM orders ORDER BY created_at DESC LIMIT 3;""")

for row in cur.fetchall():
    print(row)