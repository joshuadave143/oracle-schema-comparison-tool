"""Compare tables that share a case-insensitive name."""

from models.schema_models import DiffStatus, Difference, Severity, TableDefinition
from comparison.differences import make_difference, warning_difference
from comparison.normalization import normalize_comment, normalize_token


def compare_tables(
    left_tables: list[TableDefinition],
    right_tables: list[TableDefinition],
    *,
    db1_owner: str,
    db2_owner: str,
    compare_comments: bool,
) -> tuple[list[Difference], list[tuple[TableDefinition, TableDefinition]]]:
    """Return table differences and the pairs that should be column-compared.

    Tables are matched by case-insensitive name. The stored table name is
    preserved on each difference. A table that exists only in DB2 is reported
    separately from a table that still needs to be created in DB2.
    """

    left_index, differences = _index_tables(left_tables, database_label="DB1")
    right_index, right_warnings = _index_tables(right_tables, database_label="DB2")
    differences.extend(right_warnings)

    matched: list[tuple[TableDefinition, TableDefinition]] = []
    for key in sorted(set(left_index) | set(right_index)):
        left = left_index.get(key)
        right = right_index.get(key)
        if left is None and right is not None:
            differences.append(_only_in_db2(right))
            continue
        if left is not None and right is None:
            differences.append(_missing_in_db2(left, db1_owner=db1_owner, db2_owner=db2_owner))
            continue
        if left is None or right is None:
            continue
        matched.append((left, right))
        if left.table_name != right.table_name:
            differences.append(
                make_difference(
                    status=DiffStatus.DIFFERENT,
                    severity=Severity.LOW,
                    object_type="TABLE",
                    table_name=left.table_name,
                    object_name=left.table_name,
                    property_name="table_name",
                    db1_value=left.table_name,
                    db2_value=right.table_name,
                    suggested_action=(
                        f"Review the quoted identifier case for table {left.table_name}. "
                        f"DB2 stores it as {right.table_name}."
                    ),
                )
            )
        differences.extend(_compare_pair(left, right, compare_comments=compare_comments))
    return differences, matched


def _index_tables(
    tables: list[TableDefinition],
    *,
    database_label: str,
) -> tuple[dict[str, TableDefinition], list[Difference]]:
    indexed: dict[str, TableDefinition] = {}
    warnings: list[Difference] = []
    for table in sorted(tables, key=lambda item: (item.table_name.upper(), item.table_name)):
        key = table.table_name.upper()
        existing = indexed.get(key)
        if existing is not None:
            warnings.append(
                warning_difference(
                    f"{database_label} schema {table.owner} has multiple tables for "
                    f"case-insensitive name {key}: {existing.table_name} and "
                    f"{table.table_name}. Only {existing.table_name} was compared.",
                    table_name=existing.table_name,
                )
            )
            continue
        indexed[key] = table
    return indexed, warnings


def _missing_in_db2(
    table: TableDefinition,
    *,
    db1_owner: str,
    db2_owner: str,
) -> Difference:
    return make_difference(
        status=DiffStatus.MISSING_IN_DB2,
        severity=Severity.HIGH,
        object_type="TABLE",
        table_name=table.table_name,
        object_name=table.table_name,
        property_name="existence",
        db1_value=f"present ({db1_owner})",
        db2_value=None,
        suggested_action=(
            f"Add table {table.table_name} to schema {db2_owner} on DB2. "
            f"It exists in schema {db1_owner} on DB1."
        ),
    )


def _only_in_db2(table: TableDefinition) -> Difference:
    return make_difference(
        status=DiffStatus.ONLY_IN_DB2,
        severity=Severity.INFO,
        object_type="TABLE",
        table_name=table.table_name,
        object_name=table.table_name,
        property_name="existence",
        db1_value=None,
        db2_value=f"present ({table.owner})",
        suggested_action=(
            f"Table {table.table_name} is only in DB2 schema {table.owner}. "
            "It is not a missing change from DB1. Review it before removing anything."
        ),
    )


def _compare_pair(
    left: TableDefinition,
    right: TableDefinition,
    *,
    compare_comments: bool,
) -> list[Difference]:
    differences: list[Difference] = []
    if compare_comments:
        _append_if_changed(
            differences,
            table=left,
            property_name="comment",
            left_value=normalize_comment(left.comment),
            right_value=normalize_comment(right.comment),
            severity=Severity.LOW,
        )
    _append_if_changed(
        differences,
        table=left,
        property_name="temporary",
        left_value="YES" if left.temporary else "NO",
        right_value="YES" if right.temporary else "NO",
        severity=Severity.HIGH,
    )
    _append_if_changed(
        differences,
        table=left,
        property_name="duration",
        left_value=normalize_token(left.duration),
        right_value=normalize_token(right.duration),
        severity=Severity.MEDIUM,
    )
    _append_if_changed(
        differences,
        table=left,
        property_name="iot_type",
        left_value=normalize_token(left.iot_type),
        right_value=normalize_token(right.iot_type),
        severity=Severity.HIGH,
    )
    _append_if_changed(
        differences,
        table=left,
        property_name="partitioned",
        left_value="YES" if left.partitioned else "NO",
        right_value="YES" if right.partitioned else "NO",
        severity=Severity.HIGH,
    )
    _append_if_changed(
        differences,
        table=left,
        property_name="nested",
        left_value="YES" if left.nested else "NO",
        right_value="YES" if right.nested else "NO",
        severity=Severity.HIGH,
    )
    _append_if_changed(
        differences,
        table=left,
        property_name="status",
        left_value=normalize_token(left.status),
        right_value=normalize_token(right.status),
        severity=Severity.MEDIUM,
    )
    _append_if_changed(
        differences,
        table=left,
        property_name="cluster",
        left_value=normalize_token(left.cluster_name),
        right_value=normalize_token(right.cluster_name),
        severity=Severity.HIGH,
    )
    return differences


def _append_if_changed(
    differences: list[Difference],
    *,
    table: TableDefinition,
    property_name: str,
    left_value: str | None,
    right_value: str | None,
    severity: Severity,
) -> None:
    if left_value == right_value:
        return
    differences.append(
        make_difference(
            status=DiffStatus.DIFFERENT,
            severity=severity,
            object_type="TABLE",
            table_name=table.table_name,
            object_name=table.table_name,
            property_name=property_name,
            db1_value=left_value,
            db2_value=right_value,
            suggested_action=(
                f"Align {property_name} for table {table.table_name} on DB2 with DB1."
            ),
        )
    )
