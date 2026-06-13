import os


def is_enabled(flag_name: str) -> bool:
    """
    Return True if the named environment variable is set to "true"
    (case-insensitive). Return False for any other value or if unset.
    """
    value = os.environ.get(flag_name, "")
    return value.strip().lower() == "true"