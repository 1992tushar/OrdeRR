from app.database import SessionLocal
from app.models.customer import Customer


db = SessionLocal()

customers = [
    {
        "restaurant_name": "Spice Garden",
        "phone_number": "919623882123"
    },
    {
        "restaurant_name": "Hotel Paradise",
        "phone_number": "919999999999"
    }
]

for item in customers:

    existing = db.query(Customer).filter(
        Customer.phone_number == item["phone_number"]
    ).first()

    if not existing:

        customer = Customer(**item)

        db.add(customer)

        print(
            f"Added: {item['restaurant_name']}"
        )

db.commit()

print("Done")