from database.metadata_queries import COLUMN_COLUMNS, TABLE_COLUMNS
from database.metadata_repository import MetadataRepository
from errors import DatabaseConnectionError, MetadataAccessError
from tests.fakes import FakeConnection, ScriptStep, contains, driver_error

import pytest


def _catalog() -> dict[str, set[str]]:
    return {
        "ALL_TABLES": set(TABLE_COLUMNS) | {"SECONDARY"},
        "ALL_TAB_COLUMNS": set(COLUMN_COLUMNS) | {"DATA_DEFAULT"},
    }


def _table_row(name: str = "RCL_CLAIM", **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "owner": "ROWIS",
        "table_name": name,
        "tablespace_name": "USERS",
        "temporary": "N",
        "duration": None,
        "iot_type": None,
        "partitioned": "NO",
        "nested": "NO",
        "status": "VALID",
        "cluster_name": None,
        "dropped": "NO",
    }
    row.update(overrides)
    return row


def _column_row(
    name: str = "CLAIM_ID",
    table: str = "RCL_CLAIM",
    **overrides: object,
) -> dict[str, object]:
    row: dict[str, object] = {
        "owner": "ROWIS",
        "table_name": table,
        "column_name": name,
        "column_id": 1,
        "data_type": "NUMBER",
        "data_type_owner": None,
        "data_length": 22,
        "data_precision": 10,
        "data_scale": 0,
        "char_length": None,
        "char_used": None,
        "nullable": "N",
        "hidden_column": "NO",
        "virtual_column": "NO",
        "identity_column": "NO",
        "default_on_null": "NO",
        "user_generated": "YES",
    }
    row.update(overrides)
    return row


def _plan(identity_error: Exception | None = None) -> list[ScriptStep]:
    return [
        ScriptStep("tables", contains("from all_tables"), list(_table_row()), [
            _table_row(),
            _table_row("OTHER_TABLE"),
            _table_row("RCL_OVERFLOW", iot_type="IOT_OVERFLOW"),
        ]),
        ScriptStep("table comments", contains("from all_tab_comments"), ["table_name", "comments"], [
            {"table_name": "RCL_CLAIM", "comments": "Claim header"},
        ]),
        ScriptStep(
            "columns",
            lambda sql: "from all_tab_columns" in sql.lower() and "data_default" not in sql.lower(),
            list(_column_row()),
            [
                _column_row(),
                _column_row(
                    "STATUS",
                    column_id=2,
                    data_type="VARCHAR2",
                    data_length=100,
                    data_precision=None,
                    data_scale=None,
                    char_length=100,
                    char_used="B",
                    nullable="Y",
                ),
            ],
        ),
        ScriptStep(
            "column comments",
            contains("from all_col_comments"),
            ["table_name", "column_name", "comments"],
            [{"table_name": "RCL_CLAIM", "column_name": "STATUS", "comments": "Claim status"}],
        ),
        ScriptStep(
            "defaults",
            contains("data_default"),
            ["table_name", "column_name", "data_default"],
            [{"table_name": "RCL_CLAIM", "column_name": "STATUS", "data_default": " 'OPEN' "}],
        ),
        ScriptStep(
            "identity",
            contains("from all_tab_identity_cols"),
            ["table_name", "column_name", "generation_type", "identity_options"],
            [],
            error=identity_error,
        ),
    ]


def _repository(steps: list[ScriptStep]) -> tuple[MetadataRepository, FakeConnection]:
    connection = FakeConnection(steps)
    repository = MetadataRepository(
        connection,
        label="DB1",
        secrets=("source-secret",),
        column_catalog=_catalog(),
    )
    return repository, connection


def test_snapshot_preserves_names_and_binds_the_prefix() -> None:
    repository, connection = _repository(_plan())

    snapshot = repository.fetch_snapshot(
        owner="ROWIS",
        prefix="rcl_",
        endpoint="source_user@192.168.1.100:1521/ORCL",
    )

    assert snapshot.prefix == "rcl_"
    assert snapshot.database.oracle_version == "19.21.0.0.0"
    assert [table.table_name for table in snapshot.tables] == ["RCL_CLAIM"]
    table = snapshot.tables[0]
    assert table.comment == "Claim header"
    assert [column.column_name for column in table.columns] == ["CLAIM_ID", "STATUS"]
    status = table.columns[1]
    assert status.char_length == 100
    assert status.char_used == "B"
    assert status.nullable is True
    assert status.data_default == " 'OPEN' "
    assert status.comment == "Claim status"
    assert snapshot.capabilities.char_used is True
    assert snapshot.capabilities.identity_details is True
    assert "RCL_OVERFLOW" not in [table.table_name for table in snapshot.tables]
    assert any("outside" in warning for warning in snapshot.warnings)
    assert connection.steps == []


def test_prefix_and_owner_are_bound_parameters() -> None:
    steps = _plan()
    repository, _connection = _repository(steps)

    repository.fetch_snapshot(owner="ROWIS", prefix="RCL_", endpoint="source_user@host:1521/ORCL")

    sql, params = steps[0].captured or ("", None)
    assert params is not None
    assert params["owner"] == "ROWIS"
    assert params["prefix_pattern"] == "RCL!_%"
    assert "ROWIS" not in sql
    assert "RCL_" not in sql
    assert "ESCAPE '!'" in sql
    assert "source-secret" not in sql


def test_metadata_access_error_for_required_view() -> None:
    steps = _plan()
    steps[0].error = driver_error("table or view does not exist", 942)
    repository, _connection = _repository(steps)

    with pytest.raises(MetadataAccessError) as captured:
        repository.fetch_snapshot(owner="ROWIS", prefix="RCL_", endpoint="db1")

    message = str(captured.value)
    assert captured.value.view == "ALL_TABLES"
    assert "ALL_*" in message
    assert "source-secret" not in message


def test_optional_identity_failure_keeps_tables_and_columns() -> None:
    steps = _plan(identity_error=driver_error("table or view does not exist", 942))
    repository, _connection = _repository(steps)

    snapshot = repository.fetch_snapshot(owner="ROWIS", prefix="RCL_", endpoint="db1")

    assert snapshot.tables[0].columns[0].column_name == "CLAIM_ID"
    assert snapshot.capabilities.identity_details is False
    assert snapshot.capabilities.identity_column is True
    assert any("ALL_TAB_IDENTITY_COLS" in warning for warning in snapshot.warnings)


def test_generic_driver_error_redacts_secrets() -> None:
    steps = _plan()
    steps[2].error = RuntimeError("network down for source-secret")
    repository, _connection = _repository(steps)

    with pytest.raises(MetadataAccessError) as captured:
        repository.fetch_snapshot(owner="ROWIS", prefix="RCL_", endpoint="db1")

    assert "source-secret" not in str(captured.value)
    assert "****" in str(captured.value)


def test_duplicate_column_keeps_the_first_definition() -> None:
    steps = _plan()
    column_step = steps[2]
    column_step.rows.append(_column_row(data_precision=99))
    repository, _connection = _repository(steps)

    snapshot = repository.fetch_snapshot(owner="ROWIS", prefix="RCL_", endpoint="db1")

    claim_id = snapshot.tables[0].columns[0]
    assert claim_id.data_precision == 10
    assert any("duplicate column" in warning for warning in snapshot.warnings)


def test_quoted_owner_is_matched_exactly() -> None:
    steps = _plan()
    repository, _connection = _repository(steps)

    snapshot = repository.fetch_snapshot(owner="Rowis", prefix="RCL_", endpoint="db1")

    assert snapshot.tables == []
    assert any("outside" in warning for warning in snapshot.warnings)
