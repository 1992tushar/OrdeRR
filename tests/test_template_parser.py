#!/usr/bin/env python
"""
Regression suite for the WhatsApp order parser (template_parser.py).

WHY THIS EXISTS
---------------
Each parser fix has historically been verified by ad-hoc manual runs, so a fix
for one messy-input shape kept re-breaking another. This file pins down every
tricky real-world input we've hit so a future change that regresses one is
caught immediately.

No pytest dependency — run directly:

    DATABASE_URL=sqlite:///orderr.db venv/Scripts/python tests/test_template_parser.py

Every case runs with db=None, so it exercises ONLY the static catalog + regex
pipeline (no learned aliases / no unit-inference history). Cases that depend on
learned aliases or a customer's history are out of scope here by design.
"""
import os
import sys

# Make the repo root importable when run as a bare script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orderr_core.services.template_parser import parse_template_order  # noqa: E402

_FAILURES = []


def _items(msg):
    """(product, quantity, unit) tuples parsed from msg, order-independent."""
    r = parse_template_order("0000000000", msg, db=None)
    return {(i["product"], i["quantity"], i["unit"]) for i in r["items"]}


def check(msg, expected, note=""):
    got = _items(msg)
    want = set(expected)
    if got != want:
        _FAILURES.append((msg, want, got, note))
        print(f"FAIL {msg!r}\n     want {want}\n     got  {got}  {note}")
    else:
        print(f"ok   {msg!r} -> {got}")


# ── The AMRAI bug: size annotation next to a ".." separator ────────────────────
# "(250 gm)" is stripped as a size note, leaving "Tangdi ..4kg"; the ".." must
# not be swallowed into the quantity ("..4" → unparseable → silently dropped).
check("2) full Tangdi(250 gm)..4kg", {("Whole Leg", 4.0, "kg")},
      "AMRAI regression: size note + '..' separator")
check("Tangdi..4kg", {("Whole Leg", 4.0, "kg")}, "'..' separator")
check("wings...5 kg", {("Wings", 5.0, "kg")}, "'...' separator")

# ── Decimals / fractions must survive the dot-collapse ─────────────────────────
check("wings 1.5 kg", {("Wings", 1.5, "kg")}, "single-dot decimal")
check("wings .5 kg", {("Wings", 0.5, "kg")}, "leading-dot decimal")
check("1/2 kg wings", {("Wings", 0.5, "kg")}, "fraction")

# ── "full/whole tangdi" → Whole Leg (alias gap fixed for the AMRAI order) ──────
check("full tangdi 4kg", {("Whole Leg", 4.0, "kg")})
check("whole tangdi 4kg", {("Whole Leg", 4.0, "kg")})
check("tangdi 4kg", {("Whole Leg", 4.0, "kg")})

# ── Size annotation must not be read as the quantity (commit 6359a68) ──────────
check("chicken breast 30 kg( 900 gm )", {("Breast Boneless", 30.0, "kg")},
      "size note after qty")

# ── List markers stripped, quantity intact (commit e396a53) ────────────────────
check("1) wings 3 kg", {("Wings", 3.0, "kg")})
check("2. breast 5kg", {("Breast Boneless", 5.0, "kg")})

# ── Grams → kg (÷1000) ─────────────────────────────────────────────────────────
check("wings 500 g", {("Wings", 0.5, "kg")})

# ── Quantity-first lines ───────────────────────────────────────────────────────
check("5 kg breast", {("Breast Boneless", 5.0, "kg")})

# ── Multiple items on one line ─────────────────────────────────────────────────
check("wings 2kg breast 3kg", {("Wings", 2.0, "kg"), ("Breast Boneless", 3.0, "kg")})

# ── Skin-ambiguous whole chicken → NOT auto-matched (routes to Unclear) ─────────
# With db=None there's no learned alias, so it must produce zero items.
check("chicken big 10 kg", set(), "skin-ambiguous → unclear, never guessed")


if __name__ == "__main__":
    print()
    if _FAILURES:
        print(f"\n{len(_FAILURES)} FAILURE(S)")
        sys.exit(1)
    print("\nAll parser regression cases passed.")
