"""Read table and column metadata for one Oracle schema.

The repository issues SELECT statements only. It does not read application
table rows. Optional dictionary features degrade to warnings when a database
version or privilege set does not provide them.
"""

from collections.abc import Collection, Iterable
from typing import Any, Protocol

from database.identifiers import matches_prefix, parse_prefix, prefix_to_like_pattern
from database.metadata_queries import (
    FALLBACK_COLUMNS,
    PROBE_VIEWS,
    build_column_comment_query,
    build_column_query,
    build_default_query,
    build_dictionary_probe_query,
    build_identity_query,
    build_table_comment_query,
    build_table_query,
)
from database.sql_guard import assert_read_only_sql
from errors import DatabaseConnectionError, MetadataAccessError
from models.schema_models import (
    ColumnDefinition,
    DatabaseContext,
    SchemaCapabilities,
    SchemaSnapshot,
    TableDefinition,
)
from security import redact


class SupportsFetch(Protocol):
    """Connection surface used by the repository. Tests supply a fake."""

    driver_mode: str

    def fetchall(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[list[str], list[tuple[Any, ...]]]:
        """Return column names and rows for one SELECT."""

    @property
    def version(self) -> str | None:
        """Oracle version string, when the driver knows it."""


class MetadataRepository:
    """Load a prefix-filtered schema snapshot from one connection."""

    def __init__(
        self,
        connection: SupportsFetch,
        *,
        label: str,
        secrets: Iterable[str] = (),
        column_catalog: dict[str, Collection[str]] | None = None,
    ) -> None:
        self._connection = connection
        self._label = label
        self._secrets = tuple(secret for secret in secrets if secret)
        self._column_catalog = column_catalog
        self._cache: dict[str, set[str]] = {}

    def fetch_snapshot(
        self,
        *,
        owner: str,
        prefix: str,
        endpoint: str,
    ) -> SchemaSnapshot:
        """Return tables and columns visible for ``owner`` and ``prefix``."""

        validated_prefix = parse_prefix(prefix)
        warnings: list[str] = []
        params = {
            "owner": owner,
            "prefix_pattern": prefix_to_like_pattern(validated_prefix),
        }
        table_fields = self._columns_for("ALL_TABLES", warnings)
        column_fields = self._columns_for("ALL_TAB_COLUMNS", warnings)

        table_rows = self._kept_rows(
            self._select(build_table_query(table_fields), params, view="ALL_TABLES"),
            owner=owner,
            prefix=validated_prefix,
            warnings=warnings,
            object_label="table",
        )
        comment_rows = self._optional_select(
            build_table_comment_query(),
            params,
            view="ALL_TAB_COMMENTS",
            warnings=warnings,
        )
        column_rows = self._kept_rows(
            self._select(build_column_query(column_fields), params, view="ALL_TAB_COLUMNS"),
            owner=owner,
            prefix=validated_prefix,
            warnings=warnings,
            object_label="column",
        )
        column_comment_rows = self._optional_select(
            build_column_comment_query(),
            params,
            view="ALL_COL_COMMENTS",
            warnings=warnings,
        )
        default_sql = build_default_query(column_fields)
        if default_sql is None:
            default_rows: list[dict[str, Any]] | None = None
        else:
            default_rows = self._optional_select(
                default_sql,
                params,
                view="ALL_TAB_COLUMNS",
                warnings=warnings,
            )
        if "IDENTITY_COLUMN" in column_fields:
            identity_rows = self._optional_select(
                build_identity_query(),
                params,
                view="ALL_TAB_IDENTITY_COLS",
                warnings=warnings,
            )
        else:
            identity_rows = None

        capabilities = SchemaCapabilities(
            table_comments=comment_rows is not None,
            column_comments=column_comment_rows is not None,
            char_length="CHAR_LENGTH" in column_fields,
            char_used="CHAR_USED" in column_fields,
            hidden_column="HIDDEN_COLUMN" in column_fields,
            virtual_column="VIRTUAL_COLUMN" in column_fields,
            identity_column="IDENTITY_COLUMN" in column_fields,
            identity_details=identity_rows is not None and "IDENTITY_COLUMN" in column_fields,
            default_on_null="DEFAULT_ON_NULL" in column_fields,
            data_default=default_rows is not None,
        )
        tables = _assemble_tables(
            owner=owner,
            table_rows=table_rows,
            column_rows=column_rows,
            column_fields=column_fields,
            table_comments=comment_rows or [],
            column_comments=column_comment_rows or [],
            defaults=default_rows or [],
            identities=identity_rows or [],
            warnings=warnings,
            label=self._label,
        )
        return SchemaSnapshot(
            database=DatabaseContext(
                label=self._label,
                owner=owner,
                endpoint=endpoint,
                oracle_version=self._connection.version,
                driver_mode=getattr(self._connection, "driver_mode", None),
            ),
            prefix=validated_prefix,
            tables=tables,
            capabilities=capabilities,
            warnings=warnings,
        )

    def _columns_for(self, view_name: str, warnings: list[str]) -> set[str]:
        cached = self._cache.get(view_name)
        if cached is not None:
            return cached
        if view_name not in PROBE_VIEWS:
            raise MetadataAccessError(
                f"Unexpected dictionary view {view_name}.",
                view=view_name,
            )
        if self._column_catalog is not None:
            columns = {column.upper() for column in self._column_catalog.get(view_name, ())}
        else:
            columns = self._probe_columns(view_name)
        if not columns:
            warnings.append(
                f"{self._label}: could not list columns for {view_name}. "
                "Using a conservative column list. Some newer properties will be omitted."
            )
            columns = set(FALLBACK_COLUMNS.get(view_name, set()))
        self._cache[view_name] = columns
        return columns

    def _probe_columns(self, view_name: str) -> set[str]:
        for any_owner in (False, True):
            try:
                rows = self._select(
                    build_dictionary_probe_query(any_owner=any_owner),
                    {"view_name": view_name},
                    view="ALL_TAB_COLUMNS",
                )
            except MetadataAccessError:
                continue
            names = {
                str(row["column_name"]).upper()
                for row in rows
                if row.get("column_name")
            }
            if names:
                return names
        return set()

    def _select(
        self,
        sql: str,
        params: dict[str, Any],
        *,
        view: str,
    ) -> list[dict[str, Any]]:
        assert_read_only_sql(sql)
        try:
            columns, rows = self._connection.fetchall(sql, params)
        except MetadataAccessError:
            raise
        except DatabaseConnectionError as exc:
            raise self._wrap(view, exc) from exc
        except Exception as exc:
            safe = redact(str(exc), self._secrets)
            raise MetadataAccessError(
                f"{self._label}: failed to read {view} ({safe}).",
                view=view,
            ) from exc
        names = [column.lower() for column in columns]
        return [{name: value for name, value in zip(names, row, strict=True)} for row in rows]

    def _optional_select(
        self,
        sql: str,
        params: dict[str, Any],
        *,
        view: str,
        warnings: list[str],
    ) -> list[dict[str, Any]] | None:
        try:
            return self._select(sql, params, view=view)
        except MetadataAccessError as exc:
            warnings.append(str(exc))
            return None

    def _kept_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        owner: str,
        prefix: str,
        warnings: list[str],
        object_label: str,
    ) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        ignored = 0
        for row in rows:
            table_name = str(row.get("table_name", ""))
            row_owner = str(row.get("owner", ""))
            if row_owner != owner or not matches_prefix(table_name, prefix):
                ignored += 1
                continue
            kept.append(row)
        if ignored:
            warnings.append(
                f"{self._label}: ignored {ignored} {object_label} row(s) outside "
                f"schema {owner} or prefix {prefix!r}."
            )
        return kept

    def _wrap(self, view: str, exc: DatabaseConnectionError) -> MetadataAccessError:
        if exc.code == 942:
            message = (
                f"{self._label}: cannot read {view} ({exc}). "
                "ALL_* views only list objects visible to this account. "
                "DBA_* views would require catalog privileges and are not used."
            )
        elif exc.code == 904:
            message = (
                f"{self._label}: {view} does not have an expected column ({exc}). "
                "The database version may be older than the dictionary query."
            )
        else:
            message = f"{self._label}: failed to read {view} ({exc})."
        return MetadataAccessError(message, view=view, code=exc.code)

    def __repr__(self) -> str:
        return f"MetadataRepository(label={self._label!r})"


def _assemble_tables(
    *,
    owner: str,
    table_rows: list[dict[str, Any]],
    column_rows: list[dict[str, Any]],
    column_fields: set[str],
    table_comments: list[dict[str, Any]],
    column_comments: list[dict[str, Any]],
    defaults: list[dict[str, Any]],
    identities: list[dict[str, Any]],
    warnings: list[str],
    label: str,
) -> list[TableDefinition]:
    comments = {
        str(row["table_name"]): _as_text(row.get("comments"))
        for row in table_comments
        if row.get("table_name")
    }
    column_comment_map = {
        (str(row["table_name"]), str(row["column_name"])): _as_text(row.get("comments"))
        for row in column_comments
        if row.get("table_name") and row.get("column_name")
    }
    default_map = {
        (str(row["table_name"]), str(row["column_name"])): _as_text(row.get("data_default"))
        for row in defaults
        if row.get("table_name") and row.get("column_name")
    }
    identity_map = {
        (str(row["table_name"]), str(row["column_name"])): row
        for row in identities
        if row.get("table_name") and row.get("column_name")
    }

    tables: dict[str, TableDefinition] = {}
    for row in table_rows:
        if _is_excluded_table(row):
            continue
        table_name = str(row["table_name"])
        if table_name in tables:
            warnings.append(
                f"{label}: duplicate table {owner}.{table_name} was returned. "
                "The first definition was kept."
            )
            continue
        tables[table_name] = TableDefinition(
            owner=str(row.get("owner", owner)),
            table_name=table_name,
            tablespace_name=_as_optional_str(row.get("tablespace_name")),
            temporary=_as_yes(row.get("temporary")),
            duration=_as_optional_str(row.get("duration")),
            iot_type=_as_optional_str(row.get("iot_type")),
            partitioned=_as_yes(row.get("partitioned")),
            nested=_as_yes(row.get("nested")),
            status=_as_optional_str(row.get("status")),
            cluster_name=_as_optional_str(row.get("cluster_name")),
            comment=comments.get(table_name),
        )

    columns_for_table: dict[str, list[ColumnDefinition]] = {name: [] for name in tables}
    seen_columns: set[tuple[str, str]] = set()
    orphans = 0
    for row in column_rows:
        table_name = str(row["table_name"])
        column_name = str(row["column_name"])
        if table_name not in tables:
            orphans += 1
            continue
        key = (table_name, column_name)
        if key in seen_columns:
            warnings.append(
                f"{label}: duplicate column {owner}.{table_name}.{column_name} "
                "was returned. The first definition was kept."
            )
            continue
        seen_columns.add(key)
        identity_row = identity_map.get(key)
        column = _map_column(row, column_fields)
        if identity_row is not None:
            column = column.model_copy(
                update={
                    "identity": True,
                    "identity_generation": _as_optional_str(identity_row.get("generation_type")),
                    "identity_options": _as_text(identity_row.get("identity_options")),
                }
            )
        column = column.model_copy(
            update={
                "data_default": default_map.get(key),
                "comment": column_comment_map.get(key),
            }
        )
        columns_for_table[table_name].append(column)

    if orphans:
        warnings.append(
            f"{label}: ignored {orphans} column row(s) whose table was not in the "
            f"compared {owner} table list."
        )

    assembled: list[TableDefinition] = []
    for table_name in sorted(tables):
        columns = sorted(
            columns_for_table[table_name],
            key=lambda column: (
                column.column_id is None,
                column.column_id if column.column_id is not None else 0,
                column.column_name,
            ),
        )
        assembled.append(tables[table_name].model_copy(update={"columns": columns}))
    return assembled


def _map_column(row: dict[str, Any], fields: set[str]) -> ColumnDefinition:
    lower_fields = {field.lower() for field in fields}
    return ColumnDefinition(
        owner=str(row["owner"]),
        table_name=str(row["table_name"]),
        column_name=str(row["column_name"]),
        column_id=_as_int(row.get("column_id")),
        data_type=str(row.get("data_type") or ""),
        data_type_owner=_as_optional_str(row.get("data_type_owner")),
        data_length=_as_int(row.get("data_length")),
        data_precision=_as_int(row.get("data_precision")),
        data_scale=_as_int(row.get("data_scale")),
        char_length=_as_int(row.get("char_length")) if "char_length" in lower_fields else None,
        char_used=_char_used(row.get("char_used")) if "char_used" in lower_fields else None,
        nullable=_as_yes(row.get("nullable")),
        hidden=_as_yes(row.get("hidden_column")) if "hidden_column" in lower_fields else False,
        virtual=_as_yes(row.get("virtual_column")) if "virtual_column" in lower_fields else False,
        identity=_as_yes(row.get("identity_column")) if "identity_column" in lower_fields else False,
        default_on_null=_as_yes(row.get("default_on_null")) if "default_on_null" in lower_fields else False,
    )


def _is_excluded_table(row: dict[str, Any]) -> bool:
    iot_type = str(row.get("iot_type") or "").upper()
    dropped = str(row.get("dropped") or "NO").upper()
    return iot_type == "IOT_OVERFLOW" or dropped == "YES"


def _as_yes(value: object) -> bool:
    if value is None:
        return False
    return str(value).strip().upper() in {"Y", "YES", "TRUE", "1"}


def _as_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)  # type: ignore[arg-type]


def _as_optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_text(value: object) -> str | None:
    if value is None:
        return None
    if hasattr(value, "read"):
        value = value.read()
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value)


def _char_used(value: object) -> str | None:
    if value is None:
        return None
    token = str(value).strip().upper()
    if not token:
        return None
    if token.startswith("C"):
        return "C"
    if token.startswith("B"):
        return "B"
    return None
