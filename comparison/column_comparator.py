"""Compare columns of one table that exists in both databases."""

from models.schema_models import (
    ColumnDefinition,
    DiffStatus,
    Difference,
    SchemaCapabilities,
    Severity,
    TableDefinition,
)
from comparison.differences import make_difference, warning_difference
from comparison.formatting import (
    CHAR_TYPES,
    NUMBER_TYPES,
    base_type,
    character_length,
    character_semantics,
    format_column_definition,
    format_data_type,
    type_owner,
)
from comparison.normalization import (
    display_metadata,
    fractional_precision,
    normalize_comment,
    normalize_default,
    normalize_identity_options,
    normalize_token,
)


def compare_columns(
    left_table: TableDefinition,
    right_table: TableDefinition,
    capabilities: SchemaCapabilities,
    *,
    db2_owner: str,
) -> list[Difference]:
    """Compare columns by case-insensitive name and preserve the stored names."""

    left_columns, differences = _index_columns(left_table, database_label="DB1")
    right_columns, right_warnings = _index_columns(right_table, database_label="DB2")
    differences.extend(right_warnings)

    for key in _column_order(left_columns, right_columns):
        left = left_columns.get(key)
        right = right_columns.get(key)
        if left is not None and right is None:
            differences.append(_missing_column(left, db2_owner=db2_owner))
            continue
        if left is None and right is not None:
            differences.append(_only_db2_column(right))
            continue
        if left is None or right is None:
            continue
        if left.column_name != right.column_name:
            differences.append(
                make_difference(
                    status=DiffStatus.DIFFERENT,
                    severity=Severity.LOW,
                    object_type="COLUMN",
                    table_name=left.table_name,
                    object_name=left.column_name,
                    property_name="column_name",
                    db1_value=left.column_name,
                    db2_value=right.column_name,
                    suggested_action=(
                        f"Review the quoted identifier case for "
                        f"{left.table_name}.{left.column_name}. "
                        f"DB2 stores it as {right.column_name}."
                    ),
                )
            )
        differences.extend(_compare_pair(left, right, capabilities))
    return differences


def _index_columns(
    table: TableDefinition,
    *,
    database_label: str,
) -> tuple[dict[str, ColumnDefinition], list[Difference]]:
    indexed: dict[str, ColumnDefinition] = {}
    warnings: list[Difference] = []
    ordered = sorted(
        table.columns,
        key=lambda column: (column.column_name.upper(), column.column_name),
    )
    for column in ordered:
        key = column.column_name.upper()
        existing = indexed.get(key)
        if existing is not None:
            warnings.append(
                warning_difference(
                    f"{database_label} table {table.table_name} has multiple columns for "
                    f"case-insensitive name {key}: {existing.column_name} and "
                    f"{column.column_name}. Only {existing.column_name} was compared.",
                    table_name=table.table_name,
                    object_name=existing.column_name,
                )
            )
            continue
        indexed[key] = column
    return indexed, warnings


def _column_order(
    left: dict[str, ColumnDefinition],
    right: dict[str, ColumnDefinition],
) -> list[str]:
    def sort_key(name: str) -> tuple[int, int, str]:
        column = left.get(name) or right[name]
        source = 0 if name in left else 1
        position = column.column_id if column.column_id is not None else 10**9
        return (source, position, name)

    return sorted(set(left) | set(right), key=sort_key)


def _missing_column(column: ColumnDefinition, *, db2_owner: str) -> Difference:
    definition = format_column_definition(column)
    return make_difference(
        status=DiffStatus.MISSING_IN_DB2,
        severity=Severity.HIGH,
        object_type="COLUMN",
        table_name=column.table_name,
        object_name=column.column_name,
        property_name="existence",
        db1_value=definition,
        db2_value=None,
        suggested_action=(
            f"Add column {column.column_name} to {db2_owner}.{column.table_name}. "
            f"DB1 definition: {definition}."
        ),
    )


def _only_db2_column(column: ColumnDefinition) -> Difference:
    return make_difference(
        status=DiffStatus.ONLY_IN_DB2,
        severity=Severity.INFO,
        object_type="COLUMN",
        table_name=column.table_name,
        object_name=column.column_name,
        property_name="existence",
        db1_value=None,
        db2_value=format_column_definition(column),
        suggested_action=(
            f"Column {column.table_name}.{column.column_name} exists only in DB2. "
            "It is not a missing change from DB1. Review it before removing anything."
        ),
    )


def _compare_pair(
    left: ColumnDefinition,
    right: ColumnDefinition,
    capabilities: SchemaCapabilities,
) -> list[Difference]:
    differences: list[Difference] = []
    _append_property(
        differences,
        column=left,
        property_name="column_position",
        left_value=display_metadata(left.column_id),
        right_value=display_metadata(right.column_id),
        severity=Severity.LOW,
    )
    differences.extend(_type_differences(left, right, capabilities))
    _append_property(
        differences,
        column=left,
        property_name="nullable",
        left_value="YES" if left.nullable else "NO",
        right_value="YES" if right.nullable else "NO",
        severity=Severity.HIGH,
    )
    if capabilities.data_default and not (left.identity and right.identity):
        _append_property(
            differences,
            column=left,
            property_name="data_default",
            left_value=display_metadata(normalize_default(left.data_default)),
            right_value=display_metadata(normalize_default(right.data_default)),
            severity=Severity.MEDIUM,
        )
    if capabilities.virtual_column:
        _append_property(
            differences,
            column=left,
            property_name="virtual_column",
            left_value="YES" if left.virtual else "NO",
            right_value="YES" if right.virtual else "NO",
            severity=Severity.HIGH,
        )
    if capabilities.hidden_column:
        _append_property(
            differences,
            column=left,
            property_name="hidden_column",
            left_value="YES" if left.hidden else "NO",
            right_value="YES" if right.hidden else "NO",
            severity=Severity.MEDIUM,
        )
    if capabilities.identity_column:
        _append_property(
            differences,
            column=left,
            property_name="identity",
            left_value="YES" if left.identity else "NO",
            right_value="YES" if right.identity else "NO",
            severity=Severity.HIGH,
        )
    if capabilities.identity_details and left.identity and right.identity:
        _append_property(
            differences,
            column=left,
            property_name="identity_generation",
            left_value=normalize_token(left.identity_generation),
            right_value=normalize_token(right.identity_generation),
            severity=Severity.HIGH,
        )
        _append_property(
            differences,
            column=left,
            property_name="identity_options",
            left_value=display_metadata(normalize_identity_options(left.identity_options)),
            right_value=display_metadata(normalize_identity_options(right.identity_options)),
            severity=Severity.MEDIUM,
        )
    if capabilities.default_on_null:
        _append_property(
            differences,
            column=left,
            property_name="default_on_null",
            left_value="YES" if left.default_on_null else "NO",
            right_value="YES" if right.default_on_null else "NO",
            severity=Severity.MEDIUM,
        )
    if capabilities.column_comments:
        _append_property(
            differences,
            column=left,
            property_name="comment",
            left_value=display_metadata(normalize_comment(left.comment)),
            right_value=display_metadata(normalize_comment(right.comment)),
            severity=Severity.LOW,
        )
    return differences


def _type_differences(
    left: ColumnDefinition,
    right: ColumnDefinition,
    capabilities: SchemaCapabilities,
) -> list[Difference]:
    left_base = base_type(left.data_type)
    right_base = base_type(right.data_type)
    if left_base != right_base or type_owner(left) != type_owner(right):
        return [
            _property(
                left,
                "data_type",
                format_data_type(left),
                format_data_type(right),
                Severity.HIGH,
            )
        ]

    differences: list[Difference] = []
    if left_base in CHAR_TYPES:
        if capabilities.char_length or capabilities.char_used:
            if capabilities.char_length:
                left_length = character_length(left)
                right_length = character_length(right)
                if left_length != right_length:
                    differences.append(
                        _property(
                            left,
                            "character_length",
                            display_metadata(left_length),
                            display_metadata(right_length),
                            _length_severity(left_length, right_length),
                        )
                    )
            if capabilities.char_used:
                _maybe_append(
                    differences,
                    left,
                    "character_semantics",
                    character_semantics(left),
                    character_semantics(right),
                    Severity.HIGH,
                )
        elif left.data_length != right.data_length:
            differences.append(
                _property(
                    left,
                    "data_length",
                    display_metadata(left.data_length),
                    display_metadata(right.data_length),
                    _length_severity(left.data_length, right.data_length),
                )
            )
        return differences

    if left_base in NUMBER_TYPES:
        _maybe_append(
            differences,
            left,
            "data_precision",
            left.data_precision,
            right.data_precision,
            Severity.HIGH,
        )
        _maybe_append(
            differences,
            left,
            "data_scale",
            left.data_scale,
            right.data_scale,
            Severity.HIGH,
        )
        return differences

    if left_base == "FLOAT":
        _maybe_append(
            differences,
            left,
            "data_precision",
            left.data_precision,
            right.data_precision,
            Severity.HIGH,
        )
        return differences

    if left_base == "RAW":
        if left.data_length != right.data_length:
            differences.append(
                _property(
                    left,
                    "data_length",
                    display_metadata(left.data_length),
                    display_metadata(right.data_length),
                    _length_severity(left.data_length, right.data_length),
                )
            )
        return differences

    if left_base.startswith("TIMESTAMP"):
        left_precision = fractional_precision(left.data_type, left.data_scale)
        right_precision = fractional_precision(right.data_type, right.data_scale)
        _maybe_append(
            differences,
            left,
            "fractional_seconds_precision",
            left_precision,
            right_precision,
            Severity.HIGH,
        )
        return differences

    if format_data_type(left) != format_data_type(right):
        differences.append(
            _property(
                left,
                "data_type",
                format_data_type(left),
                format_data_type(right),
                Severity.HIGH,
            )
        )
    return differences


def _length_severity(left: int | None, right: int | None) -> Severity:
    if left is None or right is None:
        return Severity.HIGH
    if right < left:
        return Severity.HIGH
    return Severity.MEDIUM


def _maybe_append(
    differences: list[Difference],
    column: ColumnDefinition,
    property_name: str,
    left_value: object,
    right_value: object,
    severity: Severity,
) -> None:
    if left_value == right_value:
        return
    differences.append(
        _property(
            column,
            property_name,
            display_metadata(left_value),
            display_metadata(right_value),
            severity,
        )
    )


def _append_property(
    differences: list[Difference],
    *,
    column: ColumnDefinition,
    property_name: str,
    left_value: str,
    right_value: str,
    severity: Severity,
) -> None:
    if left_value == right_value:
        return
    differences.append(_property(column, property_name, left_value, right_value, severity))


def _property(
    column: ColumnDefinition,
    property_name: str,
    left_value: str,
    right_value: str,
    severity: Severity,
) -> Difference:
    return make_difference(
        status=DiffStatus.DIFFERENT,
        severity=severity,
        object_type="COLUMN",
        table_name=column.table_name,
        object_name=column.column_name,
        property_name=property_name,
        db1_value=left_value,
        db2_value=right_value,
        suggested_action=(
            f"Align {property_name} for {column.table_name}.{column.column_name} "
            "on DB2 with DB1."
        ),
    )
