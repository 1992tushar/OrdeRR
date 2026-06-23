"""
word_quantity.py
----------------
Parses word-based quantity expressions from multilingual order messages.
Supports Hindi, Marathi, Urdu and English fraction/number words.

Designed to be called ONLY when no ASCII digit is found in a line.
Always returns a result with is_confident=False so the caller routes
to unclear — manager confirmation is required for all word quantities.

Usage:
    from app.services.word_quantity import parse_word_quantity

    result = parse_word_quantity("डेढ़ किलो lollipop")
    if result:
        # result.quantity  → 1.5
        # result.unit_hint → "kg"
        # result.raw_word  → "डेढ़"
        # result.hint_str  → "1.5 kg (word qty — confirm)"
"""

import re
from dataclasses import dataclass


@dataclass
class WordQtyResult:
    quantity:   float        # parsed numeric value
    unit_hint:  str | None   # "kg", "nos", or None if not detected
    raw_word:   str          # the word that was matched
    hint_str:   str          # human-readable hint for the unclear tab


# ── Word → value map ──────────────────────────────────────────────────────────
# Keys are lowercase, stripped of diacritics where needed.
# Ordered so compound matches (e.g. "saade teen") are checked before
# their components ("saade", "teen").

# Simple fractions and small integers
_SIMPLE: dict[str, float] = {
    # ── Halves ────────────────────────────────────────────────────────────────
    "डेढ़":   1.5,
    "देढ़":   1.5,
    "डेढ":    1.5,   # without chandrabindu variant
    "dedh":   1.5,
    "dедh":   1.5,

    "ढाई":    2.5,
    "dhāī":   2.5,
    "dhai":   2.5,

    "आधा":    0.5,
    "आधी":    0.5,
    "adha":   0.5,
    "aadha":  0.5,
    "ardha":  0.5,   # Marathi
    "half":   0.5,

    # ── Quarters ──────────────────────────────────────────────────────────────
    "पाव":    0.25,
    "पव":     0.25,
    "paav":   0.25,
    "pav":    0.25,
    "paava":  0.25,
    "quarter":0.25,
    "qtr":    0.25,

    # ── Three-quarters ────────────────────────────────────────────────────────
    "पौने":   0.75,   # "पौने एक" = 0.75, but standalone = 0.75
    "paune":  0.75,

    # ── Integers 1–10 (Hindi/Marathi/Urdu spoken forms) ──────────────────────
    "एक":     1.0,
    "ek":     1.0,
    "do":     2.0,
    "दो":     2.0,
    "don":    2.0,   # Marathi
    "दोन":    2.0,   # Marathi
    "teen":   3.0,
    "तीन":    3.0,
    "tin":    3.0,   # Marathi
    "char":   4.0,
    "चार":    4.0,
    "paanch": 5.0,
    "panch":  5.0,
    "पांच":   5.0,
    "paach":  5.0,   # Marathi
    "chhe":   6.0,   # Marathi
    "saha":   6.0,   # Marathi
    "सहा":    6.0,
    "chha":   6.0,
    "saat":   7.0,
    "sat":    7.0,
    "सात":    7.0,
    "aath":   8.0,
    "आठ":     8.0,
    "nav":    9.0,
    "nau":    9.0,
    "नौ":     9.0,
    "das":    10.0,
    "दस":     10.0,
}

# Compound patterns: "saade <number>" = number + 0.5
# e.g. "saade teen" = 3.5, "saade char" = 4.5
_SAADE_BASES: dict[str, float] = {
    "do":     2.0, "दो": 2.0, "don": 2.0, "दोन": 2.0,
    "teen":   3.0, "तीन": 3.0, "tin": 3.0,
    "char":   4.0, "चार": 4.0,
    "paanch": 5.0, "पांच": 5.0, "panch": 5.0,
}

_SAADE_PREFIXES = ("saade", "saadhe", "साढ़े", "साडे")

# Compound patterns: "paune <number>" = number - 0.25
# e.g. "paune do" = 1.75, "paune teen" = 2.75
_PAUNE_BASES: dict[str, float] = {
    "do":   2.0, "दो": 2.0, "don": 2.0, "दोन": 2.0,
    "teen": 3.0, "तीन": 3.0, "tin": 3.0,
    "char": 4.0, "चार": 4.0,
}

_PAUNE_PREFIXES = ("paune", "पौने")


# ── Unit hint words ───────────────────────────────────────────────────────────
# If one of these appears in the line, we can hint the unit to the manager.

_KG_WORDS  = {"kg", "kgs", "kilo", "किलो", "किलोग्राम", "kilogram"}
_NOS_WORDS = {"nos", "pcs", "pc", "piece", "pieces", "nag", "नग"}


def _detect_unit(tokens: list[str]) -> str | None:
    lower = {t.lower() for t in tokens}
    if lower & _KG_WORDS:
        return "kg"
    if lower & _NOS_WORDS:
        return "nos"
    return None


def _tokenize_line(text: str) -> list[str]:
    """Split on whitespace; keep original case for unit detection."""
    return text.strip().split()


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_word_quantity(line: str) -> WordQtyResult | None:
    """
    Try to extract a word-based quantity from a line that contains no
    ASCII digit.

    Returns WordQtyResult or None if no word quantity found.

    Never raises — all errors are swallowed and return None.
    """
    try:
        tokens     = _tokenize_line(line)
        unit_hint  = _detect_unit(tokens)
        lower_toks = [t.lower() for t in tokens]

        # ── 1. Compound: saade <base> ─────────────────────────────────────────
        for i, tok in enumerate(lower_toks):
            if tok in _SAADE_PREFIXES:
                if i + 1 < len(lower_toks):
                    base_tok = lower_toks[i + 1]
                    # also try original (for Devanagari)
                    base_orig = tokens[i + 1]
                    base_val = _SAADE_BASES.get(base_tok) or _SAADE_BASES.get(base_orig)
                    if base_val is not None:
                        qty      = base_val + 0.5
                        raw_word = f"{tokens[i]} {tokens[i+1]}"
                        return _make_result(qty, unit_hint, raw_word)

        # ── 2. Compound: paune <base> ─────────────────────────────────────────
        for i, tok in enumerate(lower_toks):
            if tok in _PAUNE_PREFIXES:
                if i + 1 < len(lower_toks):
                    base_tok  = lower_toks[i + 1]
                    base_orig = tokens[i + 1]
                    base_val  = _PAUNE_BASES.get(base_tok) or _PAUNE_BASES.get(base_orig)
                    if base_val is not None:
                        qty      = base_val - 0.25
                        raw_word = f"{tokens[i]} {tokens[i+1]}"
                        return _make_result(qty, unit_hint, raw_word)

        # ── 3. Simple lookup ──────────────────────────────────────────────────
        # Try each token (original then lowercased) against _SIMPLE map.
        for orig, low in zip(tokens, lower_toks):
            val = _SIMPLE.get(orig) or _SIMPLE.get(low)
            if val is not None:
                return _make_result(val, unit_hint, orig)

        return None

    except Exception:
        return None


def _make_result(qty: float, unit_hint: str | None, raw_word: str) -> WordQtyResult:
    qty_str  = int(qty) if qty == int(qty) else qty
    unit_str = unit_hint or "?"
    hint     = f"{qty_str} {unit_str} (word qty — confirm)"
    return WordQtyResult(
        quantity=qty,
        unit_hint=unit_hint,
        raw_word=raw_word,
        hint_str=hint,
    )
