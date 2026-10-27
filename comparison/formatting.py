"""Display forms for Oracle column definitions."""

import re

from comparison.normalization import normalize_default
from models.schema_models import ColumnDefinition

CHAR_TYPES = frozenset(
    {
        "VARCHAR2",
        "VARCHAR",
        "CHAR",
        "CHARACTER",
        "NCHAR",
        "NVARCHAR2",
        "CHARACTER VARYING",
        "CHAR VARYING",
    }
)

NUMBER_TYPES = frozenset({"NUMBER", "DECIMAL", "NUMERIC", "DEC"})

_BUILTIN_OWNERS = frozenset({"SYS", "PUBLIC"})
_PAREN_PRECISION = re.compile(r"\(\d+\)")


def base_type(data_type: str) -> str:
    """Return the Oracle type name without length or precision suffixes."""

    text = " ".join(data_type.strip().upper().split())
    if text.startswith("TIMESTAMP") or text.startswith("INTERVAL"):
        return " ".join(_PAREN_PRECISION.sub("", text).split())
    return text.split("(", 1)[0].strip()


def type_owner(column: ColumnDefinition) -> str | None:
    """Return a user-defined type owner. Built-in SYS types are ignored."""

    if column.data_type_owner is None:
        return None
    owner = column.data_type_owner.strip().upper()
    if not owner or owner in _BUILTIN_OWNERS:
        return None
    return owner


def character_length(column: ColumnDefinition) -> int | None:
    if base_type(column.data_type) not in CHAR_TYPES:
        return None
    if column.char_length is not None:
        return column.char_length
    return column.data_length


def character_semantics(column: ColumnDefinition) -> str | None:
    if base_type(column.data_type) not in CHAR_TYPES:
        return None
    if column.char_used == "C":
        return "CHAR"
    if column.char_used == "B":
        return "BYTE"
    return None


def format_data_type(column: ColumnDefinition) -> str:
    """Format a type, including length and BYTE/CHAR semantics."""

    base = base_type(column.data_type)
    raw = " ".join(column.data_type.strip().upper().split())
    if base in CHAR_TYPES:
        rendered = _format_character(base, column)
    elif base == "NUMBER":
        rendered = _format_number(column)
    elif base == "FLOAT":
        rendered = f"FLOAT({column.data_precision})" if column.data_precision is not None else "FLOAT"
    elif base == "RAW":
        rendered = f"RAW({column.data_length})" if column.data_length is not None else "RAW"
    else:
        rendered = raw or base
    owner = type_owner(column)
    if owner and base not in CHAR_TYPES and base not in NUMBER_TYPES and base != "FLOAT":
        return f"{owner}.{rendered}"
    return rendered


def format_column_definition(column: ColumnDefinition) -> str:
    """Format the structural column definition used in missing-column rows."""

    parts = [format_data_type(column)]
    if column.virtual:
        parts.append("VIRTUAL")
    if column.identity:
        generation = (column.identity_generation or "IDENTITY").strip().upper()
        parts.append(f"GENERATED {generation}")
    else:
        default = normalize_default(column.data_default)
        if default is not None:
            parts.append(f"DEFAULT {default}")
        if column.default_on_null:
            parts.append("DEFAULT ON NULL")
    parts.append("NULL" if column.nullable else "NOT NULL")
    if column.hidden:
        parts.append("INVISIBLE")
    return " ".join(parts)


def _format_character(base: str, column: ColumnDefinition) -> str:
    length = character_length(column)
    semantics = character_semantics(column)
    if length is None:
        return base
    if semantics:
        return f"{base}({length} {semantics})"
    return f"{base}({length})"


def _format_number(column: ColumnDefinition) -> str:
    precision = column.data_precision
    scale = column.data_scale
    if precision is None and scale is None:
        return "NUMBER"
    if precision is None:
        return f"NUMBER(*,{scale})"
    if scale is None:
        return f"NUMBER({precision})"
    return f"NUMBER({precision},{scale})"
