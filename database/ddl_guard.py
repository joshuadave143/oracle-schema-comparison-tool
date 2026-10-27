"""Allow only the DDL this tool generates for DB2.

The statements are created from DB1 metadata. This guard rejects anything else,
including DROP, DELETE, and multi-statement text, before it can be sent.
"""

import re

from errors import DatabaseConnectionError

_ALLOWED_START = re.compile(
    r"^(create\s+(global\s+temporary\s+)?table|alter\s+table|comment\s+on\s+(table|column))\b",
    re.IGNORECASE,
)
_ALTER_ACTION = re.compile(r"\b(add|modify)\s*\(", re.IGNORECASE)
_BANNED_SQL = re.compile(
    r"\b(drop|delete|truncate|insert|update|merge|grant|revoke|purge|rename)\b",
    re.IGNORECASE,
)


def assert_applicable_ddl(sql: str) -> str:
    """Return the statement to execute, or raise if it is not allowed."""

    stripped = sql.strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].strip()
    if not stripped:
        raise DatabaseConnectionError("Refusing to execute an empty SQL statement.")
    outside_literals = _mask_string_literals(stripped)
    if ";" in outside_literals:
        raise DatabaseConnectionError("Multiple SQL statements are not permitted.")
    if not _ALLOWED_START.match(outside_literals):
        raise DatabaseConnectionError(
            "Only CREATE TABLE, ALTER TABLE ADD, ALTER TABLE MODIFY, "
            "and COMMENT statements generated for DB2 are permitted."
        )
    if outside_literals.lower().startswith("alter") and not _ALTER_ACTION.search(outside_literals):
        raise DatabaseConnectionError(
            "ALTER TABLE is limited to ADD and MODIFY. Dropping columns is not permitted."
        )
    if _BANNED_SQL.search(outside_literals):
        raise DatabaseConnectionError(
            "Destructive or data-changing SQL is not permitted. "
            "Objects that exist only in DB2 are left in place."
        )
    return stripped


def _mask_string_literals(sql: str) -> str:
    """Replace quoted string contents so words inside comments are not keywords."""

    masked: list[str] = []
    index = 0
    while index < len(sql):
        if sql[index] != "'":
            masked.append(sql[index])
            index += 1
            continue
        masked.append("''")
        index += 1
        while index < len(sql):
            if sql[index] == "'" and index + 1 < len(sql) and sql[index + 1] == "'":
                index += 2
                continue
            if sql[index] == "'":
                index += 1
                break
            index += 1
    return "".join(masked)
