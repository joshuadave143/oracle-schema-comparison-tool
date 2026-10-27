"""Factories for schema objects used by the unit tests."""

from config.settings import AppSettings, DatabaseSettings
from models.schema_models import (
    ColumnDefinition,
    DatabaseContext,
    SchemaCapabilities,
    SchemaSnapshot,
    TableDefinition,
)


def modern_capabilities(**overrides: object) -> SchemaCapabilities:
    values: dict[str, object] = {name: True for name in SchemaCapabilities.model_fields}
    values.update(overrides)
    return SchemaCapabilities(**values)  # type: ignore[arg-type]


def make_column(**overrides: object) -> ColumnDefinition:
    values: dict[str, object] = {
        "owner": "ROWIS",
        "table_name": "RCL_CLAIM",
        "column_name": "CLAIM_ID",
        "column_id": 1,
        "data_type": "NUMBER",
        "data_precision": 10,
        "data_scale": 0,
        "nullable": False,
    }
    values.update(overrides)
    return ColumnDefinition(**values)  # type: ignore[arg-type]


def make_table(**overrides: object) -> TableDefinition:
    values: dict[str, object] = {
        "owner": "ROWIS",
        "table_name": "RCL_CLAIM",
        "columns": [make_column()],
    }
    values.update(overrides)
    return TableDefinition(**values)  # type: ignore[arg-type]


def make_database(label: str = "DB1", owner: str = "ROWIS") -> DatabaseContext:
    if label == "DB1":
        endpoint = "source_user@192.168.1.100:1521/ORCL"
    else:
        endpoint = "target_user@192.168.1.101:1521/ORCL"
    return DatabaseContext(
        label=label,
        owner=owner,
        endpoint=endpoint,
        oracle_version="19.21.0.0.0",
        driver_mode="thin",
    )


def make_snapshot(
    label: str = "DB1",
    owner: str = "ROWIS",
    tables: list[TableDefinition] | None = None,
    **overrides: object,
) -> SchemaSnapshot:
    if tables is None:
        table_owner = owner
        tables = [make_table(owner=table_owner)]
    values: dict[str, object] = {
        "database": make_database(label, owner),
        "prefix": "RCL_",
        "tables": tables,
        "capabilities": modern_capabilities(),
        "warnings": [],
    }
    values.update(overrides)
    return SchemaSnapshot(**values)  # type: ignore[arg-type]


def make_settings() -> AppSettings:
    return AppSettings(
        db1=DatabaseSettings(
            label="DB1",
            user="source_user",
            password="source-secret",
            host="192.168.1.100",
            port=1521,
            service_name="ORCL",
        ),
        db2=DatabaseSettings(
            label="DB2",
            user="target_user",
            password="target-secret",
            host="192.168.1.101",
            port=1521,
            service_name="ORCL",
        ),
        table_prefix="RCL_",
    )


def sample_environ(**overrides: str) -> dict[str, str]:
    values = {
        "ORACLE_DB1_USER": "source_user",
        "ORACLE_DB1_PASSWORD": "source-secret",
        "ORACLE_DB1_HOST": "192.168.1.100",
        "ORACLE_DB1_PORT": "1521",
        "ORACLE_DB1_SERVICE_NAME": "ORCL",
        "ORACLE_DB2_USER": "target_user",
        "ORACLE_DB2_PASSWORD": "target-secret",
        "ORACLE_DB2_HOST": "192.168.1.101",
        "ORACLE_DB2_PORT": "1521",
        "ORACLE_DB2_SERVICE_NAME": "ORCL",
        "TABLE_PREFIX": "RCL_",
    }
    values.update(overrides)
    return values
