from enum import Enum


class Intent(Enum):
    ADHOC_REPORT     = "adhoc_report"
    ONBOARDING       = "onboarding"
    CANCEL           = "cancel"
    REPEAT_LAST      = "repeat_last"
    CONFIRM_YES      = "confirm_yes"
    CONFIRM_NO       = "confirm_no"
    ORDER            = "order"


# ── Keyword sets ──────────────────────────────────────────────────────────────
# Single source of truth — order_service.py imports from here.

CANCEL_KEYWORDS = {
    "cancel", "cancel order", "cancel my order", "order cancel",
    "cancel karo", "cancel kar", "band karo", "mat bhejo",
    "order band", "no order", "no order today", "aaj nahi",
    "order nahi", "nahi chahiye",
}

REPEAT_KEYWORDS = {
    "same", "repeat", "same order", "repeat order",
    "same as yesterday", "same as last time",
    "wahi bhejo", "wahi order", "same bhejo",
}

CONFIRM_YES_WORDS = {"yes", "haan", "ha", "haa", "ok", "okay", "confirm", "okk"}
CONFIRM_NO_WORDS  = {"no", "nahi", "nope", "cancel", "don't", "dont"}

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
      3. Cancel / Repeat keywords
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

    # 5. Everything else is treated as an order attempt
    return Intent.ORDER
