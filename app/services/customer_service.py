from sqlalchemy.orm import Session

from app.models.customer import Customer


def normalize_phone(phone: str) -> str:

    phone = (
        phone
        .replace("+", "")
        .replace(" ", "")
        .strip()
    )

    if not phone.startswith("91"):

        phone = f"91{phone}"

    return phone


def get_customer_by_phone(
    db: Session,
    phone: str
):

    normalized_phone = normalize_phone(phone)

    return db.query(Customer).filter(
        Customer.phone_number == normalized_phone
    ).first()


def create_new_customer(
    db: Session,
    phone: str
):

    normalized_phone = normalize_phone(phone)

    customer = Customer(
        phone_number=normalized_phone,
        onboarding_status="awaiting_name"
    )

    db.add(customer)

    db.commit()

    db.refresh(customer)

    return customer