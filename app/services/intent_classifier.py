from enum import Enum
import re


class Intent(Enum):
    ADHOC_REPORT     = "adhoc_report"
    ONBOARDING       = "onboarding"
    CANCEL           = "cancel"
    REPEAT_LAST      = "repeat_last"
    CONFIRM_YES      = "confirm_yes"
    CONFIRM_NO       = "confirm_no"
    HISTORY          = "history"
    GREETING         = "greeting"
    ORDER            = "order"


# ── Keyword sets ──────────────────────────────────────────────────────────────
# Single source of truth — order_service.py imports from here.

CANCEL_KEYWORDS = {
    "cancel", "cancel order", "cancel my order", "order cancel",
    "cancel karo", "cancel kar", "band karo", "mat bhejo",
    "order band", "no order", "no order today", "aaj nahi",
    "order nahi", "nahi chahiye", "रद्द करो", "रद्द कर दो",
}

REPEAT_KEYWORDS = {
    "same", "repeat", "same order", "repeat order",
    "same as yesterday", "same as last time",
    "wahi bhejo", "wahi order", "same bhejo",
}

CONFIRM_YES_WORDS = {"yes", "haan", "ha", "haa", "ok", "okay", "confirm", "okk"}
CONFIRM_NO_WORDS  = {"no", "nahi", "nope", "cancel", "don't", "dont"}

HISTORY_KEYWORDS = {
    "history",
    "my orders",
    "order history",
    "ledger",
    "past orders",
    "show history",
    "meri orders",
    "purani orders",
}

GREETINGS = {
    "hi", "hello", "hey", "hii", "hiii", "hiiii", "helo", "helloo",
    "ok", "okay", "okk", "okkk", "haan", "han", "ha", "haa",
    "yes", "no", "nahi", "nope", "yep", "yup",
    "thanks", "thank you", "thankyou", "thnx", "thx",
    "bye", "goodbye", "good morning", "good evening",
    "good night", "goodnight", "gm", "gn",
    "namaste", "namaskar", "jai hind",
    "test", "testing", "hello world",
    "who", "what", "where", "when", "why", "how",
}

FILLER_PHRASES = {
    "yes please", "yes pls", "yes sure", "yes ok", "yes okay",
    "ok sure", "ok fine", "ok thanks", "ok thank you",
    "sure sure", "fine fine", "no problem", "no worries",
    "go ahead", "please help", "help me", "i want", "i need",
    "send menu", "show menu", "place order", "start order", "new order",
    "haan ji", "haan bhai", "ha bhai", "ha ji", "ji haan", "ji han", "ji ha",
    "good morning", "good evening", "good night",
    "please proceed", "pls proceed", "pls help",
}


def classify_intent(
    message: str,
    *,
    onboarding: bool = False,
    has_pending_repeat: bool = False,
    has_pending_replace: bool = False,
) -> Intent:
    """
    Pure function — no DB access, no side effects.

    Args:
        message:             Raw message text from customer.
        onboarding:          True if customer is in awaiting_name state.
        has_pending_repeat:  True if a pending_repeat order exists for today.
        has_pending_replace: True if a pending_replace order exists for today.

    Returns the Intent that best matches the message.

    Priority order matters:
      1. Onboarding — anything they type is their restaurant name
      2. Pending confirmation — "ok" means confirm, not a filler
      3. Cancel / Repeat / History keywords
      4. Fallthrough to ORDER
    """
    lower = message.strip().lower()

    # 1. Customer hasn't given their restaurant name yet — everything is onboarding
    if onboarding:
        return Intent.ONBOARDING

    # 2. Waiting for yes/no on a pending repeat or replace —
    #    must be checked before GREETINGS since "ok", "yes", "no" live in both sets
    if has_pending_repeat or has_pending_replace:
        if lower in CONFIRM_YES_WORDS:
            return Intent.CONFIRM_YES
        if lower in CONFIRM_NO_WORDS:
            return Intent.CONFIRM_NO

    # 3. Explicit cancel
    if lower in CANCEL_KEYWORDS:
        return Intent.CANCEL

    # 4. Repeat last order
    if lower in REPEAT_KEYWORDS:
        return Intent.REPEAT_LAST

    # 5. Order history / ledger
    if lower in HISTORY_KEYWORDS:
        return Intent.HISTORY

    # 6. Greeting / filler — checked before the digit-free whole-word
    #    fallback so a plain "hi"/"thanks"/"ok sure" isn't misread as an
    #    order. Only fires on exact whole-message match (not substring),
    #    same as the other keyword-set checks above.
    if lower in GREETINGS or lower in FILLER_PHRASES:
        return Intent.GREETING

    # 7. Fallback — real customer messages rarely match a keyword set
    #    exactly (e.g. "please cancel my order today"). Allow whole-word
    #    matching for these short, free-text intents, but ONLY when the
    #    message has no digits — product orders almost always carry a
    #    quantity, so this avoids misclassifying catalog phrases like
    #    "No Skin Tandoor 5 kg" (which contains the word "no") as CANCEL.
    if not any(ch.isdigit() for ch in lower):
        if _contains_whole_word(lower, CANCEL_KEYWORDS):
            return Intent.CANCEL
        if _contains_whole_word(lower, REPEAT_KEYWORDS):
            return Intent.REPEAT_LAST
        if _contains_whole_word(lower, HISTORY_KEYWORDS):
            return Intent.HISTORY

    # 8. Everything else is treated as an order attempt
    return Intent.ORDER


def _contains_whole_word(text: str, phrases: set[str]) -> bool:
    """True if any phrase in `phrases` appears in `text` as a whole word
    (or whole phrase), not as a substring of a larger word."""
    for phrase in phrases:
        if re.search(rf"\b{re.escape(phrase)}\b", text):
            return True
    return False