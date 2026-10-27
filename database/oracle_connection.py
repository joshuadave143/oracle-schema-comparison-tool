"""Oracle connections.

Thin mode is the default and needs no Oracle Client. Oracle Database 11g is
not supported in Thin mode (DPY-3010). Set that database's mode to ``thick``
and ``ORACLE_CLIENT_LIB_DIR`` to an Instant Client directory. Thick mode
applies to every connection in the process, so it is enabled before the first
connection when either database requests it.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.settings import AppSettings, DatabaseSettings
from database.sql_guard import assert_read_only_sql
from errors import DatabaseConnectionError
from security import redact

CONNECTION_TIMEOUT_SECONDS = 15


def prepare_oracle_driver(settings: AppSettings) -> None:
    """Enable Thick mode before connecting when either database requires it."""

    if settings.db1.driver_mode != "thick" and settings.db2.driver_mode != "thick":
        return
    try:
        import oracledb
    except ImportError as exc:
        raise DatabaseConnectionError(
            "The oracledb package is not installed. Run: pip install -r requirements.txt"
        ) from exc
    if not oracledb.is_thin_mode():
        return
    lib_dir = settings.oracle_client_lib_dir
    if lib_dir and not Path(lib_dir).is_dir():
        raise DatabaseConnectionError(
            f"ORACLE_CLIENT_LIB_DIR does not exist: {lib_dir}. "
            "Oracle 11g requires Oracle Instant Client for Thick mode."
        )
    try:
        if lib_dir:
            oracledb.init_oracle_client(lib_dir=lib_dir)
        else:
            oracledb.init_oracle_client()
    except Exception as exc:
        raise DatabaseConnectionError(
            "Could not enable Oracle Thick mode. Oracle Database 11g cannot use "
            "python-oracledb Thin mode (DPY-3010). Install Oracle Instant Client 19c, "
            "which can also connect to newer databases, and set ORACLE_CLIENT_LIB_DIR "
            f"to its directory. {redact(str(exc), [])}"
        ) from exc


@dataclass(frozen=True)
class ConnectionTestResult:
    """Outcome of one independent connection test."""

    label: str
    success: bool
    endpoint: str
    oracle_version: str | None = None
    current_schema: str | None = None
    driver_mode: str | None = None
    error: str | None = None


class OracleConnection:
    """One read-only Oracle connection."""

    def __init__(self, settings: DatabaseSettings) -> None:
        self.settings = settings
        self.driver_mode = "thin"
        self._connection: Any = None

    def connect(self, *, connect_func: Any = None) -> None:
        """Open a Thin-mode connection. Pass ``connect_func`` only from tests."""

        if self._connection is not None:
            return
        if connect_func is None:
            try:
                import oracledb
            except ImportError as exc:
                raise DatabaseConnectionError(
                    "The oracledb package is not installed. "
                    "Run: pip install -r requirements.txt"
                ) from exc
            connect_func = oracledb.connect
            self.driver_mode = "thin" if oracledb.is_thin_mode() else "thick"
        password = self.settings.reveal_password()
        kwargs = {
            "user": self.settings.user,
            "password": password,
            "host": self.settings.host,
            "port": self.settings.port,
            "service_name": self.settings.service_name,
            "tcp_connect_timeout": CONNECTION_TIMEOUT_SECONDS,
        }
        try:
            try:
                self._connection = connect_func(**kwargs)
            except TypeError as exc:
                if "tcp_connect_timeout" not in str(exc):
                    raise
                kwargs.pop("tcp_connect_timeout")
                self._connection = connect_func(**kwargs)
        except DatabaseConnectionError:
            raise
        except Exception as exc:
            self._connection = None
            raise _describe_driver_error(
                exc,
                secrets=[password, self.settings.user],
                label=self.settings.label,
                endpoint=self.settings.endpoint,
            ) from exc

    def fetchall(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[list[str], list[tuple[Any, ...]]]:
        """Run one SELECT and close the cursor before returning."""

        assert_read_only_sql(sql)
        if self._connection is None:
            raise DatabaseConnectionError(f"{self.settings.label} is not connected.")
        cursor = self._connection.cursor()
        cursor.arraysize = 1000
        try:
            cursor.execute(sql, params or {})
            description = list(cursor.description or [])
            columns = [str(item[0]) for item in description]
            rows = [tuple(row) for row in cursor.fetchall()]
            return columns, rows
        except Exception as exc:
            raise _describe_driver_error(
                exc,
                secrets=[self.settings.reveal_password(), self.settings.user],
                label=self.settings.label,
                endpoint=self.settings.endpoint,
            ) from exc
        finally:
            cursor.close()

    def test(self, *, connect_func: Any = None) -> ConnectionTestResult:
        """Connect, run a harmless query, and always close the connection."""

        try:
            self.connect(connect_func=connect_func)
            _columns, rows = self.fetchall(
                "SELECT SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') AS current_schema "
                "FROM DUAL"
            )
            current_schema = str(rows[0][0]) if rows and rows[0][0] is not None else None
            return ConnectionTestResult(
                label=self.settings.label,
                success=True,
                endpoint=self.settings.endpoint,
                oracle_version=self.version,
                current_schema=current_schema,
                driver_mode=self.driver_mode,
            )
        except DatabaseConnectionError as exc:
            return ConnectionTestResult(
                label=self.settings.label,
                success=False,
                endpoint=self.settings.endpoint,
                driver_mode=self.driver_mode,
                error=str(exc),
            )
        except Exception as exc:
            return ConnectionTestResult(
                label=self.settings.label,
                success=False,
                endpoint=self.settings.endpoint,
                driver_mode=self.driver_mode,
                error=redact(
                    str(exc),
                    [self.settings.reveal_password(), self.settings.user],
                ),
            )
        finally:
            self.close()

    def close(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.close()

    def execute_ddl(self, sql: str) -> None:
        """Run one generated DB2 DDL statement. Metadata reads never call this."""

        from database.ddl_guard import assert_applicable_ddl

        checked = assert_applicable_ddl(sql)
        if self._connection is None:
            raise DatabaseConnectionError(f"{self.settings.label} is not connected.")
        cursor = self._connection.cursor()
        try:
            cursor.execute(checked)
        except Exception as exc:
            raise _describe_driver_error(
                exc,
                secrets=[self.settings.reveal_password(), self.settings.user],
                label=self.settings.label,
                endpoint=self.settings.endpoint,
            ) from exc
        finally:
            cursor.close()

    @property
    def version(self) -> str | None:
        if self._connection is None:
            return None
        value = getattr(self._connection, "version", None)
        if value is None:
            return None
        return str(value)

    def __enter__(self) -> "OracleConnection":
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"OracleConnection(label={self.settings.label!r}, endpoint={self.settings.endpoint!r})"


def _describe_driver_error(
    exc: Exception,
    *,
    secrets: list[str],
    label: str,
    endpoint: str,
) -> DatabaseConnectionError:
    code = _error_code(exc)
    message = _error_message(exc)
    safe = redact(message, secrets)
    if code is not None and f"ORA-{code:05d}" not in safe:
        safe = f"ORA-{code:05d}: {safe}"
    return DatabaseConnectionError(f"{label} ({endpoint}): {safe}", code=code)


def _error_code(exc: Exception) -> int | None:
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    if exc.args:
        nested = getattr(exc.args[0], "code", None)
        if isinstance(nested, int):
            return nested
    return None


def _error_message(exc: Exception) -> str:
    if exc.args:
        nested = exc.args[0]
        message = getattr(nested, "message", None)
        if message:
            return str(message)
    return str(exc)
