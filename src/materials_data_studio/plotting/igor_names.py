"""Shared lightweight Igor object-name allocation."""
import re
import unicodedata

_MAX_OBJECT_NAME = 31
_VALID_NAME = re.compile(r"[^A-Za-z0-9_]+")


def sanitize_igor_name(value: str, *, fallback: str = "MDSObject") -> str:
    """Create a conservative classic Igor object name from untrusted text."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = _VALID_NAME.sub("_", ascii_value).strip("_")
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f"w_{cleaned}"
    return cleaned[:_MAX_OBJECT_NAME]


def unique_igor_name(value: str, used: set[str], *, fallback: str = "wave") -> str:
    """Allocate a case-insensitively unique Igor name."""
    base = sanitize_igor_name(value, fallback=fallback)
    candidate = base
    suffix = 2
    while candidate.casefold() in used:
        tail = f"_{suffix}"
        candidate = f"{base[: _MAX_OBJECT_NAME - len(tail)]}{tail}"
        suffix += 1
    used.add(candidate.casefold())
    return candidate


