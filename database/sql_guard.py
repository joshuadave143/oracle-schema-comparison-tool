"""Guards that keep the tool limited to single read-only SELECT statements."""

import re

from errors import MetadataAccessError

_BANNED_SQL = re.compile(
    r"\b(insert|update|delete|merge|alter|drop|truncate|create|grant|revoke|comment)\b",
    re.IGNORECASE,
)


def assert_read_only_sql(sql: str) -> None:
    """Reject anything other than one SELECT statement.

    The comparison tool builds every statement itself. This guard is a second
    check so a later change cannot run DML, DDL, or PL/SQL through the
    metadata connection.
    """

    stripped = sql.strip()
    if not stripped:
        raise MetadataAccessError("Refusing to execute an empty SQL statement.")
    if ";" in stripped:
        raise MetadataAccessError("Multiple SQL statements are not permitted.")
    if not stripped.lower().startswith("select"):
        raise MetadataAccessError(
            "Only read-only SELECT statements are permitted. "
            "The comparison tool does not modify either database."
        )
    if _BANNED_SQL.search(stripped):
        raise MetadataAccessError(
            "Only read-only SELECT statements are permitted. "
            "The comparison tool does not modify either database."
        )
