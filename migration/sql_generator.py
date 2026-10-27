"""Build DB2 DDL from DB1 table and column metadata.

Generated statements create missing tables, add missing columns, and modify
column definitions. They do not drop objects that exist only in DB2, and they
do not rename or rebuild tables for differences that cannot be applied safely.
"""

from dataclasses import dataclass, field

from comparison.column_comparator import compare_columns
from comparison.formatting import character_length, format_data_type
from comparison.normalization import normalize_comment, normalize_default
from comparison.table_comparator import compare_tables
from database.ddl_guard import assert_applicable_ddl
from database.identifiers import quote_identifier, quote_string_literal
from errors import DatabaseConnectionError
from models.schema_models import (
    ColumnDefinition,
    DiffStatus,
    SchemaCapabilities,
    SchemaSnapshot,
    TableDefinition,
    intersect_capabilities,
)

_MODIFY_PROPERTIES = frozenset(
    {
        "data_type",
        "character_length",
        "character_semantics",
        "data_precision",
        "data_scale",
        "data_length",
        "fractional_seconds_precision",
        "nullable",
        "data_default",
        "default_on_null",
        "hidden_column",
    }
)
_MANUAL_COLUMN_PROPERTIES = frozenset(
    {
        "virtual_column",
        "identity",
        "identity_generation",
        "identity_options",
        "column_name",
    }
)


@dataclass(frozen=True)
class SqlStatement:
    """One statement that can be executed on DB2."""

    sql: str
    description: str
    require_no_nulls: tuple[str, str, str] | None = None
    max_stored_length: tuple[str, str, str, int, str] | None = None


@dataclass(frozen=True)
class ManualReview:
    """A difference that is reported and not executed."""

    description: str
    reason: str


@dataclass
class ApplyPlan:
    """DDL selected for DB2, plus differences left for a person to review."""

    statements: list[SqlStatement] = field(default_factory=list)
    manual_reviews: list[ManualReview] = field(default_factory=list)


def plan_db2_changes(left: SchemaSnapshot, right: SchemaSnapshot) -> ApplyPlan:
    """Plan statements that make DB2 match DB1 for the compared tables and columns."""

    capabilities = intersect_capabilities(left.capabilities, right.capabilities)
    db2_owner = right.database.owner
    table_differences, matched = compare_tables(
        left.tables,
        right.tables,
        db1_owner=left.database.owner,
        db2_owner=db2_owner,
        compare_comments=capabilities.table_comments,
    )
    left_tables = _index_tables(left.tables)
    plan = ApplyPlan()

    for difference in table_differences:
        table_name = difference.table_name or ""
        if difference.status == DiffStatus.ONLY_IN_DB2:
            continue
        if difference.status == DiffStatus.MISSING_IN_DB2 and difference.property_name == "existence":
            source = left_tables.get(table_name.upper())
            if source is not None:
                _plan_create_table(plan, source, db2_owner, capabilities.column_comments)
            continue
        if difference.status != DiffStatus.DIFFERENT:
            continue
        if difference.property_name == "comment":
            source = left_tables.get(table_name.upper())
            if source is not None:
                _add_comment(
                    plan,
                    "TABLE",
                    f"{quote_identifier(db2_owner)}.{quote_identifier(source.table_name)}",
                    source.comment,
                    f"Set the table comment on {db2_owner}.{source.table_name}.",
                )
            continue
        if difference.property_name == "table_name":
            plan.manual_reviews.append(
                ManualReview(
                    description=f"Table name case differs for {table_name}.",
                    reason=(
                        "The tool does not rename tables. Review the quoted identifier "
                        "manually if the stored name must match."
                    ),
                )
            )
            continue
        plan.manual_reviews.append(
            ManualReview(
                description=f"Table {table_name} property {difference.property_name} differs.",
                reason=(
                    "Changing temporary, index-organized, partitioned, nested, cluster, "
                    "duration, or status requires a manual rebuild."
                ),
            )
        )

    for left_table, right_table in matched:
        _plan_columns(
            plan,
            left_table,
            right_table,
            db2_owner=db2_owner,
            capabilities=capabilities,
        )
    return plan


def _plan_create_table(
    plan: ApplyPlan,
    table: TableDefinition,
    db2_owner: str,
    include_column_comments: bool,
) -> None:
    storage = _storage_clause(table)
    qualified = f"{quote_identifier(db2_owner)}.{quote_identifier(table.table_name)}"
    if storage is None:
        plan.manual_reviews.append(
            ManualReview(
                description=f"Table {table.table_name} is missing from DB2.",
                reason=(
                    "Index-organized, partitioned, nested, clustered, or unsupported "
                    "temporary tables are not created automatically."
                ),
            )
        )
        return

    columns = sorted(
        table.columns,
        key=lambda column: (
            column.column_id is None,
            column.column_id if column.column_id is not None else 0,
            column.column_name,
        ),
    )
    rendered: list[str] = []
    for column in columns:
        clause = column_definition_sql(column)
        if clause is None:
            plan.manual_reviews.append(
                ManualReview(
                    description=f"Table {table.table_name} is missing from DB2.",
                    reason=(
                        f"Column {column.column_name} does not have enough metadata "
                        "to build a CREATE TABLE statement."
                    ),
                )
            )
            return
        rendered.append(clause)
    if not rendered:
        plan.manual_reviews.append(
            ManualReview(
                description=f"Table {table.table_name} is missing from DB2.",
                reason="The table has no columns, so CREATE TABLE was not generated.",
            )
        )
        return

    body = ",\n    ".join(rendered) if rendered else ""
    if table.temporary:
        sql = (
            f"CREATE GLOBAL TEMPORARY TABLE {qualified} (\n    {body}\n) {storage}"
        )
    else:
        sql = f"CREATE TABLE {qualified} (\n    {body}\n)"
    _add_sql(plan, sql, f"Create table {db2_owner}.{table.table_name} from DB1.")
    if normalize_comment(table.comment):
        _add_comment(
            plan,
            "TABLE",
            qualified,
            table.comment,
            f"Set the table comment on {db2_owner}.{table.table_name}.",
        )
    if include_column_comments:
        for column in columns:
            if normalize_comment(column.comment):
                _add_comment(
                    plan,
                    "COLUMN",
                    f"{qualified}.{quote_identifier(column.column_name)}",
                    column.comment,
                    f"Set the comment on {db2_owner}.{table.table_name}.{column.column_name}.",
                )


def _plan_columns(
    plan: ApplyPlan,
    left_table: TableDefinition,
    right_table: TableDefinition,
    *,
    db2_owner: str,
    capabilities: SchemaCapabilities,
) -> None:
    differences = compare_columns(
        left_table,
        right_table,
        capabilities,
        db2_owner=db2_owner,
    )
    left_columns = _index_columns(left_table)
    right_columns = _index_columns(right_table)
    grouped: dict[str, set[str]] = {}
    statuses: dict[str, DiffStatus] = {}
    for difference in differences:
        if difference.object_type != "COLUMN" or not difference.object_name:
            continue
        key = difference.object_name.upper()
        grouped.setdefault(key, set())
        if difference.property_name:
            grouped[key].add(difference.property_name)
        statuses[key] = difference.status

    qualified_table = (
        f"{quote_identifier(db2_owner)}.{quote_identifier(left_table.table_name)}"
    )
    for key in sorted(grouped):
        column = left_columns.get(key)
        properties = grouped[key]
        status = statuses[key]
        if status == DiffStatus.ONLY_IN_DB2 or column is None:
            continue
        target = f"{left_table.table_name}.{column.column_name}"
        if status == DiffStatus.MISSING_IN_DB2:
            clause = column_definition_sql(column)
            if clause is None:
                plan.manual_reviews.append(
                    ManualReview(
                        description=f"Column {target} is missing from DB2.",
                        reason="The column definition could not be converted into an ADD statement.",
                    )
                )
                continue
            _add_sql(
                plan,
                f"ALTER TABLE {qualified_table} ADD ({clause})",
                f"Add column {target} to DB2.",
            )
        elif _MANUAL_COLUMN_PROPERTIES & properties:
            plan.manual_reviews.append(
                ManualReview(
                    description=f"Column {target} has a difference that is not applied automatically.",
                    reason=(
                        "Identity, virtual-column, or quoted column-name changes are left "
                        "for manual review."
                    ),
                )
            )
        elif _MODIFY_PROPERTIES & properties:
            structural = properties & (_MODIFY_PROPERTIES - {"nullable"})
            if structural:
                clause = column_definition_sql(
                    column,
                    for_modify=True,
                    include_default="data_default" in properties or "default_on_null" in properties,
                    include_nullability=False,
                    include_visibility="hidden_column" in properties,
                )
                if clause is None:
                    plan.manual_reviews.append(
                        ManualReview(
                            description=f"Column {target} differs.",
                            reason="The column definition could not be converted into a MODIFY statement.",
                        )
                    )
                else:
                    _add_sql(
                        plan,
                        f"ALTER TABLE {qualified_table} MODIFY ({clause})",
                        f"Modify column {target} on DB2 to match DB1.",
                        max_stored_length=_shrinking_length(
                            column,
                            right_columns.get(key),
                            properties,
                            db2_owner,
                            left_table.table_name,
                        ),
                    )
            if "nullable" in properties:
                null_word = "NULL" if column.nullable else "NOT NULL"
                _add_sql(
                    plan,
                    (
                        f"ALTER TABLE {qualified_table} MODIFY "
                        f"({quote_identifier(column.column_name)} {null_word})"
                    ),
                    f"Make {target} {null_word} on DB2.",
                    require_no_nulls=(
                        None
                        if column.nullable
                        else (db2_owner, left_table.table_name, column.column_name)
                    ),
                )
            if "column_position" in properties:
                plan.manual_reviews.append(
                    ManualReview(
                        description=f"Column {target} is in a different position.",
                        reason="The column definition was updated. Oracle column order was not changed.",
                    )
                )
        elif "column_position" in properties:
            plan.manual_reviews.append(
                ManualReview(
                    description=f"Column {target} is in a different position.",
                    reason="Oracle column order is not changed automatically.",
                )
            )
        if "comment" in properties and capabilities.column_comments and status != DiffStatus.MISSING_IN_DB2:
            _add_comment(
                plan,
                "COLUMN",
                f"{qualified_table}.{quote_identifier(column.column_name)}",
                column.comment,
                f"Set the comment on {db2_owner}.{target}.",
            )


def column_definition_sql(
    column: ColumnDefinition,
    *,
    for_modify: bool = False,
    include_default: bool = True,
    include_nullability: bool = True,
    include_visibility: bool = False,
) -> str | None:
    """Return the parenthesized column clause, without the surrounding DDL verb.

    A MODIFY statement must omit NULL when the column is already nullable.
    Oracle raises ORA-01451 if that clause is repeated.
    """

    name = quote_identifier(column.column_name)
    if column.virtual:
        expression = normalize_default(column.data_default)
        if not expression or ";" in expression:
            return None
        parts = [name, format_data_type(column), f"GENERATED ALWAYS AS ({expression}) VIRTUAL"]
    elif column.identity:
        generation = (column.identity_generation or "ALWAYS").strip().upper()
        if column.default_on_null and generation == "BY DEFAULT":
            identity = "GENERATED BY DEFAULT ON NULL AS IDENTITY"
        elif generation == "BY DEFAULT":
            identity = "GENERATED BY DEFAULT AS IDENTITY"
        else:
            identity = "GENERATED ALWAYS AS IDENTITY"
        parts = [name, format_data_type(column), identity]
    else:
        parts = [name, format_data_type(column)]
        if include_default:
            default_sql = _default_sql(column)
            if default_sql is not None:
                parts.append(default_sql)
            elif for_modify:
                parts.append("DEFAULT NULL")
    if include_nullability:
        parts.append("NULL" if column.nullable else "NOT NULL")
    if column.hidden:
        parts.append("INVISIBLE")
    elif for_modify and include_visibility:
        parts.append("VISIBLE")
    return " ".join(parts)


def _default_sql(column: ColumnDefinition) -> str | None:
    expression = normalize_default(column.data_default)
    if expression is None or ";" in expression:
        return None
    if column.default_on_null:
        return f"DEFAULT ON NULL {expression}"
    return f"DEFAULT {expression}"


def _storage_clause(table: TableDefinition) -> str | None:
    if table.iot_type or table.partitioned or table.nested or table.cluster_name:
        return None
    if not table.temporary:
        return ""
    duration = (table.duration or "").strip().upper()
    if duration in {"", "SYS$TRANSACTION"}:
        return "ON COMMIT DELETE ROWS"
    if duration == "SYS$SESSION":
        return "ON COMMIT PRESERVE ROWS"
    return None


def _shrinking_length(
    column: ColumnDefinition,
    current: ColumnDefinition | None,
    properties: set[str],
    db2_owner: str,
    table_name: str,
) -> tuple[str, str, str, int, str] | None:
    """Return a length check when DB2 must store a shorter character value."""

    if current is None or not properties & {"character_length", "data_length"}:
        return None
    new_length = character_length(column)
    old_length = character_length(current)
    if new_length is None or old_length is None or new_length >= old_length:
        return None
    semantics = "CHAR" if column.char_used == "C" else "BYTE"
    return (db2_owner, table_name, column.column_name, new_length, semantics)


def _add_comment(
    plan: ApplyPlan,
    object_kind: str,
    qualified_name: str,
    comment: str | None,
    description: str,
) -> None:
    literal = quote_string_literal(normalize_comment(comment) or "")
    _add_sql(plan, f"COMMENT ON {object_kind} {qualified_name} IS {literal}", description)


def _add_sql(
    plan: ApplyPlan,
    sql: str,
    description: str,
    *,
    require_no_nulls: tuple[str, str, str] | None = None,
    max_stored_length: tuple[str, str, str, int, str] | None = None,
) -> None:
    try:
        checked = assert_applicable_ddl(sql)
    except DatabaseConnectionError as exc:
        plan.manual_reviews.append(ManualReview(description=description, reason=str(exc)))
        return
    plan.statements.append(
        SqlStatement(
            sql=checked,
            description=description,
            require_no_nulls=require_no_nulls,
            max_stored_length=max_stored_length,
        )
    )


def _index_tables(tables: list[TableDefinition]) -> dict[str, TableDefinition]:
    indexed: dict[str, TableDefinition] = {}
    for table in sorted(tables, key=lambda item: (item.table_name.upper(), item.table_name)):
        indexed.setdefault(table.table_name.upper(), table)
    return indexed


def _index_columns(table: TableDefinition) -> dict[str, ColumnDefinition]:
    indexed: dict[str, ColumnDefinition] = {}
    for column in sorted(table.columns, key=lambda item: (item.column_name.upper(), item.column_name)):
        indexed.setdefault(column.column_name.upper(), column)
    return indexed
