PRODUCT_CATALOG = [
    {
        "name": "Whole Broiler Chicken",
        "unit": "KG"
    },
    {
        "name": "Breast Boneless",
        "unit": "KG"
    },
    {
        "name": "Leg Boneless",
        "unit": "KG"
    },
    {
        "name": "Wings",
        "unit": "KG"
    },
    {
        "name": "Drumsticks",
        "unit": "KG"
    }
]


def generate_menu_template() -> str:

    lines = [
        "🐔 *Place Your Order*\n"
    ]

    for item in PRODUCT_CATALOG:
        lines.append(
            f"{item['name']} ({item['unit']}) -"
        )

    lines.append("\nExample:")
    lines.append("Wings (KG) - 2")

    return "\n".join(lines)