"""Normalize Oracle metadata so formatting does not become a false difference."""

import re


def normalize_default(value: str | None) -> str | None:
    """Normalize a column default expression.

    Leading and trailing whitespace is removed. Whitespace outside quoted
    literals is collapsed. Unquoted text is uppercased so ``sysdate`` and
    ``SYSDATE`` match. Text inside quotes keeps its case. A default of NULL
    is treated as no default.
    """

    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    normalized = _collapse_unquoted_whitespace(_uppercase_unquoted(stripped))
    if normalized.upper() == "NULL":
        return None
    return normalized


def normalize_comment(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def normalize_token(value: str | None) -> str:
    """Normalize a dictionary keyword. Blank becomes ``(none)``."""

    if value is None:
        return "(none)"
    text = " ".join(value.strip().upper().split())
    return text or "(none)"


def normalize_identity_options(value: str | None) -> str | None:
    """Normalize identity options. Sequence names are not part of this text."""

    if value is None:
        return None
    text = " ".join(value.strip().upper().split())
    return text or None


def _uppercase_unquoted(text: str) -> str:
    parts: list[str] = []
    in_quote = False
    index = 0
    while index < len(text):
        character = text[index]
        if character == "'":
            if in_quote and index + 1 < len(text) and text[index + 1] == "'":
                parts.append("''")
                index += 2
                continue
            in_quote = not in_quote
            parts.append(character)
        elif in_quote:
            parts.append(character)
        else:
            parts.append(character.upper())
        index += 1
    return "".join(parts)


def _collapse_unquoted_whitespace(text: str) -> str:
    parts: list[str] = []
    in_quote = False
    pending_space = False
    index = 0
    while index < len(text):
        character = text[index]
        if character == "'":
            if pending_space and parts:
                parts.append(" ")
            pending_space = False
            if in_quote and index + 1 < len(text) and text[index + 1] == "'":
                parts.append("''")
                index += 2
                continue
            in_quote = not in_quote
            parts.append(character)
        elif in_quote:
            parts.append(character)
        elif character.isspace():
            pending_space = True
        else:
            if pending_space and parts:
                parts.append(" ")
            pending_space = False
            parts.append(character)
        index += 1
    return "".join(parts).strip()


def display_metadata(value: object) -> str:
    """Render a metadata value that exists but may be null."""

    if value is None:
        return "(none)"
    if isinstance(value, bool):
        return "YES" if value else "NO"
    return str(value)


_FRACTIONAL_PRECISION = re.compile(r"\((\d+)\)")


def fractional_precision(data_type: str, data_scale: int | None) -> str | None:
    """Return timestamp fractional-second precision when the type has one."""

    match = _FRACTIONAL_PRECISION.search(data_type)
    if match:
        return match.group(1)
    if data_scale is not None:
        return str(data_scale)
    return None
