import psycopg2

DATABASE_URL = 'postgresql://orderr_db_user:VORCj0qqoRQZO3H7fGwurX4EBLPr0ww1@dpg-d881pu1kh4rs73c969f0-a.singapore-postgres.render.com/orderr_db'

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

cur.execute("DELETE FROM unclear_item_aliases WHERE raw_text = 'raan -5';")
conn.commit()

print(f"Deleted {cur.rowcount} row(s)")

cur.close()
conn.close()