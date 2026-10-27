"""Oracle dictionary SQL used to read table and column metadata.

Every statement is a single SELECT. Names interpolated into the SQL come from
the fixed column lists in this module, never from the prefix, owner, or
another user-supplied string. Owner and prefix are bind variables.

``ALL_*`` views are used because they show objects the connected account can
access and they include an owner column. That lets DB1 and DB2 use different
schema owners without requiring a DBA account.

``DBA_*`` views would be appropriate for an account with catalog privileges
that must see tables it has not been granted. They are not queried here.

``USER_*`` views would be appropriate only when the connected user owns the
schema. They have no owner column, so they cannot represent two different
owners. They are not queried here.
"""

import re
from collections.abc import Collection

from errors import MetadataAccessError

_COLUMN_NAME = re.compile(r"[A-Z][A-Z0-9_$#]{0,127}\Z")

LIKE_PREDICATE = "UPPER(table_name) LIKE :prefix_pattern ESCAPE '!'"

TABLE_COLUMNS = (
    "OWNER",
    "TABLE_NAME",
    "TABLESPACE_NAME",
    "TEMPORARY",
    "DURATION",
    "IOT_TYPE",
    "PARTITIONED",
    "NESTED",
    "STATUS",
    "CLUSTER_NAME",
    "DROPPED",
)

COLUMN_COLUMNS = (
    "OWNER",
    "TABLE_NAME",
    "COLUMN_NAME",
    "COLUMN_ID",
    "DATA_TYPE",
    "DATA_TYPE_OWNER",
    "DATA_LENGTH",
    "DATA_PRECISION",
    "DATA_SCALE",
    "CHAR_LENGTH",
    "CHAR_USED",
    "NULLABLE",
    "HIDDEN_COLUMN",
    "VIRTUAL_COLUMN",
    "IDENTITY_COLUMN",
    "DEFAULT_ON_NULL",
    "USER_GENERATED",
)

FALLBACK_COLUMNS = {
    "ALL_TABLES": {
        "OWNER",
        "TABLE_NAME",
        "TABLESPACE_NAME",
        "TEMPORARY",
        "DURATION",
        "IOT_TYPE",
        "PARTITIONED",
        "NESTED",
        "STATUS",
        "CLUSTER_NAME",
    },
    "ALL_TAB_COLUMNS": {
        "OWNER",
        "TABLE_NAME",
        "COLUMN_NAME",
        "COLUMN_ID",
        "DATA_TYPE",
        "DATA_TYPE_OWNER",
        "DATA_LENGTH",
        "DATA_PRECISION",
        "DATA_SCALE",
        "NULLABLE",
        "DATA_DEFAULT",
        "CHAR_LENGTH",
        "CHAR_USED",
    },
}

PROBE_VIEWS = frozenset(
    {
        "ALL_TABLES",
        "ALL_TAB_COLUMNS",
        "ALL_TAB_COMMENTS",
        "ALL_COL_COMMENTS",
        "ALL_TAB_IDENTITY_COLS",
    }
)


def build_dictionary_probe_query(*, any_owner: bool) -> str:
    """List column names for one dictionary view."""

    if any_owner:
        return (
            "SELECT DISTINCT column_name FROM all_tab_columns "
            "WHERE table_name = :view_name"
        )
    return (
        "SELECT column_name FROM all_tab_columns "
        "WHERE table_name = :view_name AND owner = 'SYS'"
    )


def build_table_query(available: Collection[str]) -> str:
    columns = _available(available)
    selected = [column for column in TABLE_COLUMNS if column in columns]
    _require("ALL_TABLES", selected, ("OWNER", "TABLE_NAME"))
    predicates = ["owner = :owner", LIKE_PREDICATE]
    if "DROPPED" in columns:
        predicates.append("NVL(dropped, 'NO') = 'NO'")
    if "IOT_TYPE" in columns:
        predicates.append("(iot_type IS NULL OR iot_type <> 'IOT_OVERFLOW')")
    if "SECONDARY" in columns:
        predicates.append("NVL(secondary, 'N') = 'N'")
    return (
        f"SELECT {_identifiers(selected)} FROM all_tables WHERE "
        + " AND ".join(predicates)
        + " ORDER BY table_name"
    )


def build_column_query(available: Collection[str]) -> str:
    """Column structure query. ``DATA_DEFAULT`` is selected separately.

    ``DATA_DEFAULT`` is a LONG column. Selecting it on its own avoids the
    Oracle restriction that a LONG must be fetched in a narrow query.
    """

    columns = _available(available)
    selected = [column for column in COLUMN_COLUMNS if column in columns]
    _require(
        "ALL_TAB_COLUMNS",
        selected,
        ("OWNER", "TABLE_NAME", "COLUMN_NAME", "DATA_TYPE", "NULLABLE"),
    )
    predicates = ["owner = :owner", LIKE_PREDICATE]
    if "USER_GENERATED" in columns:
        predicates.append("user_generated = 'YES'")
    return (
        f"SELECT {_identifiers(selected)} FROM all_tab_columns WHERE "
        + " AND ".join(predicates)
        + " ORDER BY table_name, column_id, column_name"
    )


def build_default_query(available: Collection[str]) -> str | None:
    columns = _available(available)
    if "DATA_DEFAULT" not in columns:
        return None
    predicates = ["owner = :owner", LIKE_PREDICATE]
    if "USER_GENERATED" in columns:
        predicates.append("user_generated = 'YES'")
    return (
        "SELECT table_name, column_name, data_default FROM all_tab_columns WHERE "
        + " AND ".join(predicates)
    )


def build_table_comment_query() -> str:
    return (
        "SELECT table_name, comments FROM all_tab_comments "
        "WHERE owner = :owner AND table_type = 'TABLE' AND "
        + LIKE_PREDICATE
    )


def build_column_comment_query() -> str:
    return (
        "SELECT table_name, column_name, comments FROM all_col_comments "
        "WHERE owner = :owner AND "
        + LIKE_PREDICATE
    )


def build_identity_query() -> str:
    return (
        "SELECT table_name, column_name, generation_type, identity_options "
        "FROM all_tab_identity_cols WHERE owner = :owner AND "
        + LIKE_PREDICATE
    )


def _available(available: Collection[str]) -> set[str]:
    return {column.upper() for column in available}


def _require(view: str, selected: list[str], required: tuple[str, ...]) -> None:
    missing = [column for column in required if column not in selected]
    if missing:
        names = ", ".join(missing)
        raise MetadataAccessError(
            f"{view} is missing required column(s): {names}. "
            "The account cannot read the expected dictionary metadata.",
            view=view,
        )


def _identifiers(columns: list[str]) -> str:
    for column in columns:
        if not _COLUMN_NAME.fullmatch(column):
            raise MetadataAccessError(
                f"Refusing to query an unexpected column name: {column}."
            )
    return ", ".join(columns)
