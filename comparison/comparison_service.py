"""Orchestrate a table and column comparison from two schema snapshots."""

from datetime import datetime, timezone

from comparison.column_comparator import compare_columns
from comparison.differences import sort_differences, warning_difference
from comparison.table_comparator import compare_tables
from models.schema_models import (
    ComparisonResult,
    Difference,
    SchemaCapabilities,
    SchemaSnapshot,
    intersect_capabilities,
)

COMPARISON_SCOPE = (
    "This version compares tables and columns only. "
    "Primary keys, foreign keys, unique constraints, check constraints, "
    "and indexes are not compared yet."
)

PRIVILEGE_NOTE = (
    "Results include only objects visible to each connected account in Oracle "
    "ALL_* dictionary views. Tables the account cannot see look the same as "
    "tables that do not exist. DBA_* views are not queried. USER_* views are "
    "not used, because the two schemas may have different owners."
)

_CAPABILITY_LABELS = {
    "table_comments": "Table comments",
    "column_comments": "Column comments",
    "char_length": "Character length",
    "char_used": "Character semantics",
    "hidden_column": "Hidden column flags",
    "virtual_column": "Virtual column flags",
    "identity_column": "Identity column flags",
    "identity_details": "Identity column options",
    "default_on_null": "DEFAULT ON NULL flags",
    "data_default": "Column defaults",
}


def compare_schemas(left: SchemaSnapshot, right: SchemaSnapshot) -> ComparisonResult:
    """Compare DB1 (``left``) with DB2 (``right``).

    Column differences are produced only for tables that exist on both sides.
    A missing table is one difference; its columns are not repeated.
    """

    if left.prefix.upper() != right.prefix.upper():
        raise ValueError("DB1 and DB2 snapshots were filtered with different prefixes.")

    capabilities = intersect_capabilities(left.capabilities, right.capabilities)
    differences: list[Difference] = []
    differences.extend(_snapshot_warnings(left, "DB1"))
    differences.extend(_snapshot_warnings(right, "DB2"))
    differences.extend(_capability_warnings(left.capabilities, right.capabilities))
    differences.extend(_empty_schema_warnings(left, right))

    table_differences, matched = compare_tables(
        left.tables,
        right.tables,
        db1_owner=left.database.owner,
        db2_owner=right.database.owner,
        compare_comments=capabilities.table_comments,
    )
    differences.extend(table_differences)
    for left_table, right_table in sorted(matched, key=lambda pair: pair[0].table_name.upper()):
        differences.extend(
            compare_columns(
                left_table,
                right_table,
                capabilities,
                db2_owner=right.database.owner,
            )
        )

    left_keys = {table.table_name.upper() for table in left.tables}
    right_keys = {table.table_name.upper() for table in right.tables}
    return ComparisonResult(
        compared_at=datetime.now(timezone.utc),
        prefix=left.prefix,
        db1=left.database,
        db2=right.database,
        db1_table_count=len(left.tables),
        db2_table_count=len(right.tables),
        matching_table_count=len(matched),
        missing_table_count=len(left_keys - right_keys),
        only_db2_table_count=len(right_keys - left_keys),
        differences=sort_differences(differences),
        scope=COMPARISON_SCOPE,
    )


def _snapshot_warnings(snapshot: SchemaSnapshot, label: str) -> list[Difference]:
    rendered: list[Difference] = []
    for message in snapshot.warnings:
        text = message if message.startswith(label) else f"{label}: {message}"
        rendered.append(warning_difference(text))
    return rendered


def _empty_schema_warnings(left: SchemaSnapshot, right: SchemaSnapshot) -> list[Difference]:
    warnings: list[Difference] = []
    for snapshot in (left, right):
        if snapshot.tables:
            continue
        warnings.append(
            warning_difference(
                f"{snapshot.database.label}: no tables in schema {snapshot.database.owner} "
                f"match prefix {snapshot.prefix!r}. This can be legitimate, or the account "
                "may not be able to see those tables in ALL_TABLES."
            )
        )
    return warnings


def _capability_warnings(
    left: SchemaCapabilities,
    right: SchemaCapabilities,
) -> list[Difference]:
    warnings: list[Difference] = []
    for field, label in _CAPABILITY_LABELS.items():
        left_available = bool(getattr(left, field))
        right_available = bool(getattr(right, field))
        if left_available and right_available:
            continue
        if left_available != right_available:
            available = "DB1" if left_available else "DB2"
            missing = "DB2" if left_available else "DB1"
            message = (
                f"{label} metadata is available on {available} but not on {missing}. "
                f"{label} were not compared."
            )
        else:
            message = (
                f"{label} metadata is not available on either database. "
                f"{label} were not compared."
            )
        warnings.append(warning_difference(message))
    return warnings
