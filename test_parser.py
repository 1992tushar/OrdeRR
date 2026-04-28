from app.services.parser import parse_order
import json

test_orders = [
    {
        "phone": "919876543210",
        "message": "Bhai kal subah 6 baje chahiye CC without skin 20kg"
    },
    {
        "phone": "919876543211",
        "message": "Boilers 5, drum stik 1"
    },
    {
        "phone": "919876543212",
        "message": "Tandoor chicken with skin 10 Nos"
    },
    {
        "phone": "919876543213",
        "message": "breast boneless 10kg aur kheema 5kg tomorrow 5am"
    },
    {
        "phone": "919876543214",
        "message": "1100gm chi 20 nos kal subah 5 baje"
    },
    {
        "phone": "919876543215",
        "message": "Bhai aaj chahiye - liver 2kg, gizzard 2kg aur lollipop 5kg"
    }
]

print("🧠 Testing OrdeRR AI Parser — No Product List\n")
print("=" * 50)

for order in test_orders:
    print(f"\n📱 Customer: {order['phone']}")
    print(f"💬 Message: {order['message']}")
    result = parse_order(order['phone'], order['message'])
    print(f"✅ Parsed: {json.dumps(result, indent=2)}")
    print("-" * 50)