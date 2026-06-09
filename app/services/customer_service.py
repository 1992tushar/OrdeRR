from sqlalchemy.orm import Session

from app.models.customer import Customer


def normalize_phone(phone: str) -> str:
    """
    Normalize phone number to E.164 format without leading +.
    Handles numbers with/without country code.

    Examples:
        9876543210      → 919876543210
        919876543210    → 919876543210  (already correct, not double-prefixed)
        +919876543210   → 919876543210
    """

    # Strip whitespace, dashes, plus sign
    phone = (
        phone
        .replace("+", "")
        .replace(" ", "")
        .replace("-", "")
        .strip()
    )

    # Only add 91 prefix if the number is exactly 10 digits (raw Indian mobile)
    # This avoids the bug where 911234567890 would incorrectly get prefixed again
    if len(phone) == 10 and not phone.startswith("91"):
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


def create_customer_manually(
    db: Session,
    phone: str,
    restaurant_name: str,
    area: str = None,
    salesperson_id: int = None,
) -> Customer:
    """
    Create a customer record directly (no onboarding flow).
    Used by dashboard Add Customer form and manager WhatsApp command.
    Raises ValueError if phone already exists.
    """
    normalized = normalize_phone(phone)

    existing = db.query(Customer).filter(Customer.phone_number == normalized).first()
    if existing:
        raise ValueError(f"Customer with phone {normalized} already exists.")

    customer = Customer(
        phone_number=normalized,
        restaurant_name=restaurant_name.strip(),
        area=area.strip() if area else None,
        salesperson_id=salesperson_id,
        is_daily_order_customer=True,
        onboarding_status="active",   # skip onboarding since manager added them
        is_active=True,
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer