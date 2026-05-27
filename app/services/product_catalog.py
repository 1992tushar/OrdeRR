import os

PLANT_NAME = os.getenv("PLANT_NAME", "Fluffy")

PRODUCT_CATALOG = [
    {"id": "prod_whole_broiler",   "name": "Whole Broiler Chicken", "unit": "kg"},
    {"id": "prod_breast_boneless", "name": "Breast Boneless",       "unit": "kg"},
    {"id": "prod_leg_boneless",    "name": "Leg Boneless",          "unit": "kg"},
    {"id": "prod_wings",           "name": "Wings",                 "unit": "kg"},
    {"id": "prod_drumsticks",      "name": "Drumsticks",            "unit": "kg"},
]


def generate_order_template() -> str:
    return (
        f"🛒 *Place Your Order — {PLANT_NAME}*\n\n"
        "Copy below, fill in quantity, delete what you don't need, and send:\n\n"
        "Whole Broiler Chicken - __ kg\n"
        "Breast Boneless - __ kg\n"
        "Leg Boneless - __ kg\n"
        "Wings - __ kg\n"
        "Drumsticks - __ kg\n\n"
        "🕒 Delivery Time: __ (optional)\n\n"
        "✅ *Example:*\n"
        "Wings - 2 kg\n"
        "Drumsticks - 5 kg\n"
        "🕒 Delivery Time: 6 AM"
    )


# Keep old name as alias so nothing else breaks
def generate_menu_template() -> str:
    return generate_order_template()


def get_interactive_list_items() -> list:
    return [
        {"id": p["id"], "title": p["name"], "description": f"Unit: {p['unit']}"}
        for p in PRODUCT_CATALOG
    ]


def get_product_by_id(product_id: str) -> dict | None:
    return next((p for p in PRODUCT_CATALOG if p["id"] == product_id), None)


def get_quantity_buttons(product_name: str) -> list:
    return [
        {"id": "qty_1",      "title": "1 kg"},
        {"id": "qty_2",      "title": "2 kg"},
        {"id": "qty_custom", "title": "Other qty"},
    ]