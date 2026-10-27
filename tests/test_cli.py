import os
from io import StringIO

from rich.console import Console

from cli import run_compare, run_connection_tests
from config.settings import ENV_KEYS
from database.oracle_connection import ConnectionTestResult
from errors import ConfigurationError, DatabaseConnectionError
from main import main
from tests.factories import make_settings, make_snapshot, make_table, sample_environ

import pytest


class _Connection:
    instances: list["_Connection"] = []

    def __init__(self, settings: object) -> None:
        self.settings = settings
        self.closed = False
        self.driver_mode = "thin"
        self.version = "19.0.0.0.0"
        self.ddl: list[str] = []
        _Connection.instances.append(self)

    def connect(self) -> None:
        if getattr(self.settings, "label", "") == "DB1" and _Connection.fail_db1:
            raise DatabaseConnectionError(
                "DB1 (source_user@192.168.1.100:1521/ORCL): ORA-01017: invalid username/password"
            )

    def close(self) -> None:
        self.closed = True

    def execute_ddl(self, sql: str) -> None:
        if getattr(self.settings, "label", "") != "DB2":
            raise AssertionError("DDL was sent to a database other than DB2")
        self.ddl.append(sql)

    def test(self) -> ConnectionTestResult:
        label = self.settings.label
        self.close()
        if label == "DB1" and _Connection.fail_db1:
            return ConnectionTestResult(
                label=label,
                success=False,
                endpoint=self.settings.endpoint,
                error="ORA-01017: invalid username/password",
            )
        return ConnectionTestResult(
            label=label,
            success=True,
            endpoint=self.settings.endpoint,
            oracle_version="19.0.0.0.0",
            current_schema=self.settings.user.upper(),
            driver_mode="thin",
        )

    fail_db1 = False


class _Repository:
    def __init__(self, _connection: object, *, label: str, secrets: tuple[str, ...]) -> None:
        self.label = label
        self._secrets = secrets
        assert "source-secret" not in repr(self)

    def fetch_snapshot(self, *, owner: str, prefix: str, endpoint: str):
        if self.label == "DB1":
            return make_snapshot(owner=owner, tables=[make_table(owner=owner)])
        return make_snapshot(label="DB2", owner=owner, tables=[])


def _console() -> tuple[Console, StringIO]:
    buffer = StringIO()
    return Console(file=buffer, force_terminal=False, color_system=None, width=120), buffer


def test_compare_command_reports_a_missing_table_and_closes_connections() -> None:
    _Connection.instances = []
    _Connection.fail_db1 = False
    output, buffer = _console()

    code = run_compare(
        make_settings(),
        prefix="rcl_",
        db1_owner="rowis",
        db2_owner="rowis_new",
        console=output,
        connection_factory=_Connection,
        repository_factory=_Repository,
        confirm=lambda _prompt: "no",
    )

    text = buffer.getvalue()
    assert code == 0
    assert "MISSING_IN_DB2" in text
    assert "RCL_CLAIM" in text
    assert "ROWIS_NEW" in text
    assert "source-secret" not in text
    assert "target-secret" not in text
    assert "not compared yet" in text
    assert all(connection.closed for connection in _Connection.instances)
    assert len(_Connection.instances) == 2
    assert all(connection.ddl == [] for connection in _Connection.instances)


def test_compare_applies_ddl_to_db2_only_when_confirmed() -> None:
    _Connection.instances = []
    _Connection.fail_db1 = False
    output, buffer = _console()

    code = run_compare(
        make_settings(),
        prefix="RCL_",
        db1_owner="ROWIS",
        db2_owner="ROWIS_NEW",
        console=output,
        connection_factory=_Connection,
        repository_factory=_Repository,
        confirm=lambda _prompt: "yes",
    )

    db1, db2 = _Connection.instances
    assert code == 0
    assert db1.ddl == []
    assert any(sql.startswith("CREATE TABLE ROWIS_NEW.RCL_CLAIM") for sql in db2.ddl)
    assert "DROP" not in " ".join(db2.ddl).upper()
    assert "source-secret" not in buffer.getvalue()
    assert all(connection.closed for connection in _Connection.instances)


def test_not_null_is_skipped_when_existing_rows_contain_nulls() -> None:
    from cli import _apply_plan
    from migration.sql_generator import ApplyPlan, SqlStatement

    class _Db2:
        def __init__(self) -> None:
            self.ddl: list[str] = []

        def fetchall(self, sql: str, params: dict[str, object] | None = None):
            assert "IS NULL" in sql
            return ["NULL_COUNT"], [(1,)]

        def execute_ddl(self, sql: str) -> None:
            self.ddl.append(sql)

    connection = _Db2()
    output, buffer = _console()
    plan = ApplyPlan(
        statements=[
            SqlStatement(
                sql="ALTER TABLE ROWIS.ROWIS_CL_DOC_REQ_D MODIFY (TES VARCHAR2(56 BYTE))",
                description="Modify column ROWIS_CL_DOC_REQ_D.TES on DB2 to match DB1.",
            ),
            SqlStatement(
                sql="ALTER TABLE ROWIS.ROWIS_CL_DOC_REQ_D MODIFY (TES NOT NULL)",
                description="Make ROWIS_CL_DOC_REQ_D.TES NOT NULL on DB2.",
                require_no_nulls=("ROWIS", "ROWIS_CL_DOC_REQ_D", "TES"),
            ),
        ]
    )

    code = _apply_plan(connection, plan, output)

    text = buffer.getvalue()
    assert code == 1
    assert connection.ddl == [
        "ALTER TABLE ROWIS.ROWIS_CL_DOC_REQ_D MODIFY (TES VARCHAR2(56 BYTE))"
    ]
    assert "Skipped" in text
    assert "null values" in text


def test_compare_closes_db1_when_connection_fails() -> None:
    _Connection.instances = []
    _Connection.fail_db1 = True
    output, buffer = _console()

    with pytest.raises(DatabaseConnectionError) as captured:
        run_compare(
            make_settings(),
            prefix="RCL_",
            db1_owner=None,
            db2_owner=None,
            console=output,
            connection_factory=_Connection,
            repository_factory=_Repository,
        )

    assert "source-secret" not in str(captured.value)
    assert all(connection.closed for connection in _Connection.instances)


def test_invalid_prefix_and_owner_fail_before_connecting() -> None:
    _Connection.instances = []
    output, _buffer = _console()

    with pytest.raises(ConfigurationError, match="must not be empty"):
        run_compare(
            make_settings(),
            prefix=" ",
            db1_owner=None,
            db2_owner=None,
            console=output,
            connection_factory=_Connection,
            repository_factory=_Repository,
        )
    with pytest.raises(ConfigurationError):
        run_compare(
            make_settings(),
            prefix="RCL_%",
            db1_owner="ROW IS",
            db2_owner=None,
            console=output,
            connection_factory=_Connection,
            repository_factory=_Repository,
        )

    assert _Connection.instances == []


def test_connection_tests_continue_after_db1_fails() -> None:
    _Connection.instances = []
    _Connection.fail_db1 = True
    output, buffer = _console()

    results = run_connection_tests(make_settings(), console=output, connection_factory=_Connection)

    assert [result.success for result in results] == [False, True]
    assert "DB2 OK" in buffer.getvalue()
    assert "source-secret" not in buffer.getvalue()
    assert all(connection.closed for connection in _Connection.instances)


def test_main_rejects_unimplemented_report_flags() -> None:
    with pytest.raises(SystemExit) as captured:
        main(["compare", "--prefix", "RCL_", "--format", "excel"])

    assert captured.value.code == 2


def test_main_reports_missing_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    missing = tmp_path / "missing.env"

    code = main(["--env-file", str(missing), "test-connections"])

    assert code == 2


def test_main_compare_uses_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    for key in list(os.environ):
        if key.startswith("ORACLE_") or key == "TABLE_PREFIX":
            monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    lines = [f"{key}={value}" for key, value in sample_environ().items()]
    env_file.write_text("\n".join(lines), encoding="utf-8")
    _Connection.instances = []
    _Connection.fail_db1 = False
    monkeypatch.setattr("cli.console", Console(file=StringIO(), force_terminal=False, color_system=None))
    monkeypatch.setattr("cli.OracleConnection", _Connection)
    monkeypatch.setattr("cli.MetadataRepository", _Repository)
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")

    code = main(["--env-file", str(env_file), "compare", "--prefix", "RCL_"])

    assert code == 0
    assert len(_Connection.instances) == 2
    assert all(connection.closed for connection in _Connection.instances)
