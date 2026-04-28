from app.database import SessionLocal, engine, Base
from app.services.order_service import process_incoming_order, get_all_orders
import json

# Create all tables
Base.metadata.create_all(bind=engine)

# Get database session
db = SessionLocal()

print("💾 Testing OrdeRR Database Storage\n")
print("=" * 50)

# Test orders
test_orders = [
    {
        "phone": "919876543210",
        "message": "Bhai kal subah 6 baje chahiye CC without skin 20kg"
    },
    {
        "phone": "919876543211",
        "message": "Boilers 5, drum stik 1kg"
    },
    {
        "phone": "919876543213",
        "message": "breast boneless 10kg aur kheema 5kg tomorrow 5am"
    }
]

# Process each order
for order in test_orders:
    print(f"\n📱 Processing order from: {order['phone']}")
    print(f"💬 Message: {order['message']}")
    
    result = process_incoming_order(
        db=db,
        customer_phone=order['phone'],
        message=order['message']
    )
    print(f"🔍 Raw parsed result: {result['parsed']}")
    print(f"✅ Saved to DB — Order ID: {result['order_id']}")
    print(f"📦 Items: {json.dumps(result['parsed']['items'], indent=2)}")
    print("-" * 50)

# Fetch all orders from database
print("\n\n📋 ALL ORDERS IN DATABASE:")
print("=" * 50)
all_orders = get_all_orders(db)
print(f"Total orders: {len(all_orders)}")
for o in all_orders:
    items = json.loads(o.parsed_items) if o.parsed_items else []
    print(f"\nOrder #{o.id}")
    print(f"  Customer : {o.customer_phone}")
    print(f"  Status   : {o.status}")
    print(f"  Delivery : {o.delivery_date} at {o.delivery_time}")
    print(f"  Items    : {len(items)} item(s)")
    print(f"  Unclear  : {o.is_unclear}")

db.close()
print("\n✅ Database test complete!")