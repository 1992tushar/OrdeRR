import re

from app.services.product_catalog import PRODUCT_CATALOG


def normalize_text(text: str):

    return (
        text.lower()
        .replace("*", "")
        .strip()
    )


def parse_template_order(customer_phone: str, message: str):

    items = []

    normalized_message = normalize_text(message)

    lines = normalized_message.splitlines()

    for line in lines:

        line = line.strip()

        if not line:
            continue

        for catalog_item in PRODUCT_CATALOG:

            product_name = catalog_item["name"]
            unit = catalog_item["unit"]

            normalized_product = normalize_text(product_name)

            pattern = rf"^{re.escape(normalized_product)}\s*\({unit.lower()}\)\s*[-:]\s*(\d+(?:\.\d+)?)$"

            match = re.match(pattern, line)

            if match:

                quantity = float(match.group(1))

                items.append({
                    "product": product_name,
                    "quantity": quantity,
                    "unit": unit
                })

    return {
        "customer_phone": customer_phone,
        "items": items,
        "delivery_date": None,
        "delivery_time": None,
        "is_unclear": len(items) == 0,
        "unclear_reason": "No valid items found" if len(items) == 0 else ""
    }