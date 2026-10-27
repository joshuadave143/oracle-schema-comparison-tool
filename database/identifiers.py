"""Validation and escaping for Oracle identifiers and table prefixes."""

import re

_UNQUOTED_OWNER = re.compile(r"[A-Za-z][A-Za-z0-9_$#]{0,127}\Z")
_PREFIX = re.compile(r"[\w$#]+\Z", re.UNICODE)
_LIKE_ESCAPE = "!"


def parse_prefix(value: str | None) -> str:
    """Validate a table-name prefix and return it unchanged apart from trimming.

    Matching is case-insensitive later. The original prefix is preserved for
    reports. An empty prefix is rejected so the tool cannot accidentally read
    every table in a schema.
    """

    if value is None:
        raise ValueError(
            "A table prefix is required. Set TABLE_PREFIX in the environment "
            "or pass --prefix."
        )
    text = value.strip()
    if not text:
        raise ValueError(
            "Table prefix must not be empty. "
            "Refusing to compare every table in the schema."
        )
    if len(text) > 128:
        raise ValueError("Table prefix must be 128 characters or fewer.")
    if "." in text:
        raise ValueError(
            "Table prefix must be a table-name prefix, not a schema-qualified name. "
            "Set the schema with --db1-owner and --db2-owner."
        )
    if not _PREFIX.fullmatch(text):
        raise ValueError(
            "Table prefix contains unsupported characters. "
            "Use letters, digits, underscore, dollar, and hash."
        )
    return text


def prefix_to_like_pattern(prefix: str) -> str:
    """Turn a literal prefix into an uppercase LIKE pattern.

    Underscore is escaped because it is a single-character wildcard in Oracle
    LIKE. The escape character is ``!`` and the SQL must use ``ESCAPE '!'``.
    """

    normalized = parse_prefix(prefix).upper()
    escaped = (
        normalized.replace(_LIKE_ESCAPE, _LIKE_ESCAPE + _LIKE_ESCAPE)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )
    return escaped + "%"


def matches_prefix(table_name: str, prefix: str) -> bool:
    """Return whether a table name matches a prefix, ignoring case."""

    return table_name.upper().startswith(parse_prefix(prefix).upper())


def normalize_owner(owner: str) -> str:
    """Normalize a schema owner.

    Unquoted owners are uppercased, matching Oracle's rule for unquoted
    identifiers. A double-quoted owner keeps its case and the quotes are
    removed because dictionary views store the identifier without quotes.
    """

    text = owner.strip()
    if not text:
        raise ValueError("Schema owner must not be empty.")
    if text.startswith('"'):
        if len(text) < 2 or not text.endswith('"'):
            raise ValueError("Quoted schema owner is missing a closing quote.")
        inner = text[1:-1]
        if not inner or '"' in inner:
            raise ValueError(
                "Quoted schema owner must contain the identifier only, "
                "without embedded quotes."
            )
        if len(inner) > 128:
            raise ValueError("Schema owner must be 128 characters or fewer.")
        return inner
    if not _UNQUOTED_OWNER.fullmatch(text):
        raise ValueError(
            "Schema owner must be an Oracle identifier: a letter, followed by "
            "letters, digits, underscore, dollar, or hash. "
            "Quote a mixed-case owner, for example \"Rowis\"."
        )
    return text.upper()


def default_owner_for_user(user: str) -> str:
    """Use the login user as the schema owner when no owner was supplied."""

    return normalize_owner(user)


def quote_identifier(name: str) -> str:
    """Return an Oracle identifier safe to place in generated DDL.

    Unquoted uppercase identifiers are emitted as-is. Any other name is
    double-quoted, with embedded quotes doubled.
    """

    if re.fullmatch(r"[A-Z][A-Z0-9_$#]{0,127}", name):
        return name
    return '"' + name.replace('"', '""') + '"'


def quote_string_literal(value: str) -> str:
    """Return a single-quoted Oracle string literal."""

    return "'" + value.replace("'", "''") + "'"
