import os

PLANT_NAME = os.getenv("PLANT_NAME", "Fluffy")

PRODUCT_CATALOG = [
    # Whole Chicken
    {"id": "prod_ws_tandoor",       "name": "WS Tandoor Chicken",       "unit": "nos", "display": "With Skin Tandoor (900–1100g)"},
    {"id": "prod_ws_regular",       "name": "WS Regular Chicken",       "unit": "nos", "display": "With Skin Regular (1100–1500g)"},
    {"id": "prod_wos_tandoor",      "name": "W/O Skin Tandoor Chicken",  "unit": "nos", "display": "W/O Skin Tandoor (700–900g)"},
    {"id": "prod_wos_regular",      "name": "W/O Skin Regular Chicken",  "unit": "nos", "display": "W/O Skin Regular (1000–1500g)"},
    # Boneless
    {"id": "prod_breast_boneless",  "name": "Breast Boneless",          "unit": "kg"},
    {"id": "prod_leg_boneless",     "name": "Leg Boneless",             "unit": "kg"},
    # Wings
    {"id": "prod_wings",            "name": "Wings",                    "unit": "kg"},
    # Ready
    {"id": "prod_lollipop",         "name": "Ready Lollipop",           "unit": "nos"},
    # Bone
    {"id": "prod_carcass",          "name": "Carcass",                  "unit": "nos"},
    # Cut Variants
    {"id": "prod_curry_cut",        "name": "Curry Cut",                "unit": "kg"},
    {"id": "prod_biryani_cut",      "name": "Biryani Cut",              "unit": "kg"},
    # Leg Parts
    {"id": "prod_drumstick",        "name": "Drumstick",                "unit": "kg"},
    {"id": "prod_whole_leg",        "name": "Whole Leg",                "unit": "nos"},
    # Organ Meat
    {"id": "prod_liver",            "name": "Liver",                    "unit": "kg"},
    {"id": "prod_gizzard",          "name": "Gizzard",                  "unit": "kg"},
]


def generate_order_template() -> str:
    return (
        f"🛒 *Place Your Order — {PLANT_NAME}*\n\n"
        "Copy below, fill in quantity, delete what you don't need, and send:\n\n"
        "*🐔 Whole Chicken (nos)*\n"
        "WS Tandoor (900–1100g) - __ nos\n"
        "WS Regular (1100–1500g) - __ nos\n"
        "W/O Skin Tandoor (700–900g) - __ nos\n"
        "W/O Skin Regular (1000–1500g) - __ nos\n\n"
        "*🥩 Boneless (kg)*\n"
        "Breast Boneless - __ kg\n"
        "Leg Boneless - __ kg\n\n"
        "*🍗 Wings & Ready (kg / nos)*\n"
        "Wings - __ kg\n"
        "Ready Lollipop - __ nos\n\n"
        "*🔪 Cut Variants (kg)*\n"
        "Curry Cut - __ kg\n"
        "Biryani Cut - __ kg\n\n"
        "*🦵 Leg Parts (kg / nos)*\n"
        "Drumstick - __ kg\n"
        "Whole Leg - __ nos\n\n"
        "*🫀 Organ Meat (kg)*\n"
        "Liver - __ kg\n"
        "Gizzard - __ kg\n\n"
        "*🦴 Bone (nos)*\n"
        "Carcass - __ nos\n\n"
        "🕒 Delivery Time: __ (optional)\n\n"
        "✅ *Example:*\n"
        "WS Tandoor - 10 nos\n"
        "Breast Boneless - 3 kg\n"
        "Curry Cut - 5 kg\n"
        "🕒 Delivery Time: 6 AM"
    )


# Keep old name as alias so nothing else breaks
def generate_menu_template() -> str:
    return generate_order_template()


def get_interactive_list_items() -> list:
    return [
        {
            "id":          p["id"],
            "title":       p["name"],
            "description": f"Unit: {p['unit']}" + (f" — {p['display']}" if p.get("display") else ""),
        }
        for p in PRODUCT_CATALOG
    ]


def get_product_by_id(product_id: str) -> dict | None:
    return next((p for p in PRODUCT_CATALOG if p["id"] == product_id), None)


def get_quantity_buttons(product_name: str) -> list:
    # Return nos-based buttons for whole chicken / lollipop / carcass / whole leg
    nos_products = {
        "WS Tandoor Chicken", "WS Regular Chicken",
        "W/O Skin Tandoor Chicken", "W/O Skin Regular Chicken",
        "Ready Lollipop", "Carcass", "Whole Leg",
    }
    if product_name in nos_products:
        return [
            {"id": "qty_5",      "title": "5 nos"},
            {"id": "qty_10",     "title": "10 nos"},
            {"id": "qty_custom", "title": "Other qty"},
        ]
    return [
        {"id": "qty_1",      "title": "1 kg"},
        {"id": "qty_2",      "title": "2 kg"},
        {"id": "qty_custom", "title": "Other qty"},
    ]
