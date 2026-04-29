from app.database import SessionLocal, engine
from app.models.order import Order

db = SessionLocal()

# Delete all orders — fresh start
deleted = db.query(Order).delete()
db.commit()
db.close()

print(f"✅ Deleted {deleted} old test orders. Database is clean!")