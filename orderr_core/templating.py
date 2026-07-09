"""
Shared Jinja2Templates factory.

Every route that renders HTML gets its templates instance from make_templates()
so the app-wide Jinja globals (ERP product-name display helpers) are registered
in exactly one place instead of being copy-pasted per route module.
"""
from fastapi.templating import Jinja2Templates

from orderr_core.services.template_parser import erp_display_name, ERP_ITEMS

# friendly product name → exact Vasy ERP name, emitted to pages that render
# product names client-side (window.ERP_NAMES).
_ERP_NAMES_MAP = {name: item["erp_name"] for name, item in ERP_ITEMS.items()}


def make_templates() -> Jinja2Templates:
    """Return a Jinja2Templates instance with the shared globals registered."""
    templates = Jinja2Templates(directory="orderr_core/templates")
    templates.env.globals["erp_name"] = erp_display_name
    templates.env.globals["erp_names_map"] = _ERP_NAMES_MAP
    return templates
