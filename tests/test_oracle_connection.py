from config.settings import DatabaseSettings
from database.oracle_connection import OracleConnection
from database.sql_guard import assert_read_only_sql
from errors import DatabaseConnectionError, MetadataAccessError

import pytest


def _settings() -> DatabaseSettings:
    return DatabaseSettings(
        label="DB1",
        user="source_user",
        password="source-secret",
        host="192.168.1.100",
        port=1521,
        service_name="ORCL",
    )


class _Cursor:
    def __init__(self, driver: "_Driver") -> None:
        self.driver = driver
        self.closed = False
        self.description = [("CURRENT_SCHEMA",)]
        self.arraysize = 0

    def execute(self, sql: str, params: dict | None = None) -> None:
        assert_read_only_sql(sql)
        self.driver.executed.append((sql, params))
        if self.driver.execute_error is not None:
            raise self.driver.execute_error

    def fetchall(self) -> list[tuple[str, ...]]:
        return [("ROWIS",)]

    def close(self) -> None:
        self.closed = True


class _Driver:
    def __init__(self, execute_error: Exception | None = None) -> None:
        self.version = "19.3.0.0.0"
        self.closed = False
        self.execute_error = execute_error
        self.cursors: list[_Cursor] = []
        self.executed: list[tuple[str, dict | None]] = []

    def cursor(self) -> _Cursor:
        cursor = _Cursor(self)
        self.cursors.append(cursor)
        return cursor

    def close(self) -> None:
        self.closed = True


def test_connection_success_closes_cursor_and_connection() -> None:
    driver = _Driver()
    captured: dict[str, object] = {}

    def connect(**kwargs: object) -> _Driver:
        captured.update(kwargs)
        return driver

    result = OracleConnection(_settings()).test(connect_func=connect)

    assert result.success
    assert result.endpoint == "source_user@192.168.1.100:1521/ORCL"
    assert result.current_schema == "ROWIS"
    assert result.oracle_version == "19.3.0.0.0"
    assert result.driver_mode == "thin"
    assert captured["password"] == "source-secret"
    assert "password" not in result.endpoint
    assert driver.closed
    assert driver.cursors[0].closed
    assert "source-secret" not in repr(result)


def test_connection_error_redacts_password_and_still_closes() -> None:
    def connect(**kwargs: object) -> _Driver:
        raise RuntimeError(f"ORA-01017 login failed for {kwargs['password']} at {kwargs['host']}")

    result = OracleConnection(_settings()).test(connect_func=connect)

    assert not result.success
    assert result.error is not None
    assert "ORA-01017" in result.error
    assert "source-secret" not in result.error
    assert "****" in result.error
    assert "192.168.1.100" in result.error


def test_query_error_closes_the_cursor() -> None:
    driver = _Driver(execute_error=RuntimeError("ORA-12541 network failure"))
    connection = OracleConnection(_settings())
    connection.connect(connect_func=lambda **_kwargs: driver)

    with pytest.raises(DatabaseConnectionError) as captured:
        connection.fetchall("SELECT 1 FROM DUAL")

    assert "source-secret" not in str(captured.value)
    assert driver.cursors[0].closed
    connection.close()
    assert driver.closed


def test_fetchall_rejects_statements_that_change_data() -> None:
    connection = OracleConnection(_settings())

    with pytest.raises(MetadataAccessError):
        connection.fetchall("DELETE FROM RCL_CLAIM")


def test_context_manager_closes_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    driver = _Driver()
    connection = OracleConnection(_settings())

    def fake_connect(*_args: object, **_kwargs: object) -> None:
        connection._connection = driver

    monkeypatch.setattr(connection, "connect", fake_connect)
    with connection:
        assert connection.version == "19.3.0.0.0"

    assert driver.closed


def test_importing_the_module_leaves_thin_mode_enabled() -> None:
    import oracledb

    import database.oracle_connection  # noqa: F401

    assert oracledb.is_thin_mode()


def test_thick_mode_is_enabled_only_when_requested(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import oracledb

    from config.settings import AppSettings
    from database.oracle_connection import prepare_oracle_driver
    from tests.factories import make_settings

    calls: list[dict[str, str]] = []
    monkeypatch.setattr(oracledb, "is_thin_mode", lambda: True)
    monkeypatch.setattr(
        oracledb,
        "init_oracle_client",
        lambda **kwargs: calls.append(kwargs),
    )
    prepare_oracle_driver(make_settings())
    assert calls == []

    client_dir = tmp_path / "instantclient"
    client_dir.mkdir()
    settings = make_settings()
    thick_settings = AppSettings(
        db1=settings.db1.model_copy(update={"driver_mode": "thick"}),
        db2=settings.db2,
        table_prefix=settings.table_prefix,
        oracle_client_lib_dir=str(client_dir),
    )
    prepare_oracle_driver(thick_settings)
    assert calls == [{"lib_dir": str(client_dir)}]
