PRODUCT_CATALOG = [
    {"id": "prod_whole_broiler",   "name": "Whole Broiler Chicken", "unit": "KG"},
    {"id": "prod_breast_boneless", "name": "Breast Boneless",       "unit": "KG"},
    {"id": "prod_leg_boneless",    "name": "Leg Boneless",          "unit": "KG"},
    {"id": "prod_wings",           "name": "Wings",                 "unit": "KG"},
    {"id": "prod_drumsticks",      "name": "Drumsticks",            "unit": "KG"},
]

# Quantity quick-reply options per product (in KG)
QUANTITY_OPTIONS = [1, 2, 5, 10]


def get_interactive_list_items() -> list:
    """Returns product list formatted for send_interactive_list()."""
    return [
        {
            "id":          p["id"],
            "title":       p["name"],
            "description": f"Unit: {p['unit']}"
        }
        for p in PRODUCT_CATALOG
    ]


def get_product_by_id(product_id: str) -> dict | None:
    return next((p for p in PRODUCT_CATALOG if p["id"] == product_id), None)


def get_quantity_buttons(product_name: str) -> list:
    """Returns quick-reply buttons for quantity selection."""
    buttons = [
        {"id": f"qty_{q}", "title": f"{q} KG"}
        for q in QUANTITY_OPTIONS[:2]  # 1 KG, 2 KG
    ]
    buttons.append({"id": "qty_custom", "title": "Other qty"})
    return buttons


def generate_menu_template() -> str:
    """Fallback plain-text menu (used for non-interactive fallback)."""
    lines = ["🐔 *Place Your Order*\n"]
    for item in PRODUCT_CATALOG:
        lines.append(f"{item['name']} ({item['unit']}) -")
    lines.append("\nExample:\nWings (KG) - 2")
    return "\n".join(lines)
