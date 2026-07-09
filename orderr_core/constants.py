"""
Shared, dependency-free constants.

This module must import ONLY from the standard library so it is safe to import
from anywhere (models, services, routes) without any circular-import risk.
"""
from datetime import timedelta, timezone

# India Standard Time (UTC+5:30) — the single source for all business-date,
# scheduling and display logic across the app.
IST = timezone(timedelta(hours=5, minutes=30))

# Sentinel written into a parsed item's "unit" field when the quantity's unit
# is ambiguous and needs manager resolution. order_service / admin / the parser
# all key on this exact value.
UNIT_AMBIGUOUS_MARKER = "__unit_ambiguous__"
