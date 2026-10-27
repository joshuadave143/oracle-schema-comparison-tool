"""Command implementations for connection tests and schema comparison."""

import argparse
import sys
from collections.abc import Callable

from rich.console import Console
from rich.markup import escape

from comparison.comparison_service import compare_schemas
from config.settings import AppSettings, load_settings
from database.identifiers import default_owner_for_user, normalize_owner, parse_prefix, quote_identifier
from database.metadata_repository import MetadataRepository
from database.oracle_connection import ConnectionTestResult, OracleConnection, prepare_oracle_driver
from errors import ConfigurationError, DatabaseConnectionError, MetadataAccessError
from migration.sql_generator import ApplyPlan, SqlStatement, plan_db2_changes
from reporting.console_report import render_comparison

console = Console()


def test_connections_command(args: argparse.Namespace) -> int:
    """Test DB1 and DB2 independently."""

    try:
        settings = load_settings(dotenv_path=args.env_file)
    except ConfigurationError as exc:
        console.print(f"[red]Configuration error:[/red] {escape(str(exc))}")
        return 2
    try:
        results = run_connection_tests(settings, console=console)
    except DatabaseConnectionError as exc:
        console.print(f"[red]Database error:[/red] {escape(str(exc))}")
        return 1
    if all(result.success for result in results):
        console.print("[bold]Both database connections succeeded.[/bold]")
        return 0
    console.print("[bold]One or both database connections failed.[/bold]")
    return 1


def compare_command(args: argparse.Namespace) -> int:
    """Compare tables and columns for a prefix."""

    try:
        settings = load_settings(dotenv_path=args.env_file)
        return run_compare(
            settings,
            prefix=args.prefix,
            db1_owner=args.db1_owner,
            db2_owner=args.db2_owner,
            console=console,
        )
    except ConfigurationError as exc:
        console.print(f"[red]Configuration error:[/red] {escape(str(exc))}")
        return 2
    except (DatabaseConnectionError, MetadataAccessError) as exc:
        console.print(f"[red]Database error:[/red] {escape(str(exc))}")
        return 1


def run_connection_tests(
    settings: AppSettings,
    *,
    console: Console,
    connection_factory: type[OracleConnection] = OracleConnection,
) -> list[ConnectionTestResult]:
    """Open each database separately. A failure in DB1 does not skip DB2."""

    prepare_oracle_driver(settings)
    results: list[ConnectionTestResult] = []
    for database in (settings.db1, settings.db2):
        console.print(f"Testing {database.label} ({database.endpoint})...")
        connection = connection_factory(database)
        result = connection.test()
        results.append(result)
        if result.success:
            version = result.oracle_version or "unknown version"
            schema = result.current_schema or "unknown schema"
            mode = result.driver_mode or "thin"
            console.print(
                f"[green]{result.label} OK[/green] {escape(result.endpoint)} "
                f"schema {escape(schema)} Oracle {escape(version)} ({escape(mode)} mode)"
            )
        else:
            console.print(
                f"[red]{result.label} FAILED[/red] {escape(result.endpoint)}: "
                f"{escape(result.error or 'Unknown connection error')}"
            )
    return results


def run_compare(
    settings: AppSettings,
    *,
    prefix: str | None,
    db1_owner: str | None,
    db2_owner: str | None,
    console: Console,
    connection_factory: type[OracleConnection] | None = None,
    repository_factory: type[MetadataRepository] | None = None,
    confirm: Callable[[str], str] | None = None,
) -> int:
    """Compare DB1 with DB2 and print the result. Connections are always closed."""

    if connection_factory is None:
        connection_factory = OracleConnection
    if repository_factory is None:
        repository_factory = MetadataRepository
    if connection_factory is OracleConnection:
        prepare_oracle_driver(settings)

    validated_prefix = _prefix_or_error(prefix, settings.table_prefix)
    owner1 = _owner_or_error(db1_owner, settings.db1.user, "DB1")
    owner2 = _owner_or_error(db2_owner, settings.db2.user, "DB2")

    connection1: OracleConnection | None = None
    connection2: OracleConnection | None = None
    try:
        console.print(f"Connecting to DB1 ({settings.db1.endpoint})...")
        connection1 = connection_factory(settings.db1)
        connection1.connect()
        console.print(f"Connecting to DB2 ({settings.db2.endpoint})...")
        connection2 = connection_factory(settings.db2)
        connection2.connect()

        console.print(f"Reading tables and columns from DB1 schema {owner1}...")
        snapshot1 = repository_factory(
            connection1,
            label="DB1",
            secrets=(settings.db1.reveal_password(),),
        ).fetch_snapshot(owner=owner1, prefix=validated_prefix, endpoint=settings.db1.endpoint)
        console.print(f"Reading tables and columns from DB2 schema {owner2}...")
        snapshot2 = repository_factory(
            connection2,
            label="DB2",
            secrets=(settings.db2.reveal_password(),),
        ).fetch_snapshot(owner=owner2, prefix=validated_prefix, endpoint=settings.db2.endpoint)
        console.print("Comparing table and column metadata...")
        result = compare_schemas(snapshot1, snapshot2)
        render_comparison(result, console)
        plan = plan_db2_changes(snapshot1, snapshot2)
        if not _apply_confirmed(plan, owner2, settings.db2.endpoint, console, confirm):
            return 0
        console.print(f"Applying {len(plan.statements)} statement(s) to DB2 schema {owner2}...")
        failures = _apply_plan(connection2, plan, console)
        return 1 if failures else 0
    finally:
        if connection1 is not None:
            connection1.close()
        if connection2 is not None:
            connection2.close()


def _prefix_or_error(prefix: str | None, configured: str | None) -> str:
    try:
        return parse_prefix(prefix if prefix is not None else configured)
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc


def _owner_or_error(owner: str | None, user: str, label: str) -> str:
    if owner is None:
        try:
            return default_owner_for_user(user)
        except ValueError as exc:
            raise ConfigurationError(f"{label} user is not a valid schema owner: {exc}") from exc
    if not owner.strip():
        raise ConfigurationError(f"{label} schema owner must not be empty.")
    try:
        return normalize_owner(owner)
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc


def _apply_confirmed(
    plan: ApplyPlan,
    db2_owner: str,
    db2_endpoint: str,
    console: Console,
    confirm: Callable[[str], str] | None,
) -> bool:
    """Ask before changing DB2. Return true only when the user answers yes."""

    if not plan.statements and not plan.manual_reviews:
        return False
    console.print()
    console.rule(f"Apply changes to DB2 schema {db2_owner}")
    console.print(
        f"Target: {db2_endpoint}, schema {db2_owner}. "
        "DB1 is not changed. Objects that exist only in DB2 are not dropped."
    )
    if plan.statements:
        console.print()
        console.print("[bold]Statements that will run on DB2[/bold]")
        for statement in plan.statements:
            console.print(f"-- {escape(statement.description)}")
            console.print(escape(statement.sql))
            console.print()
    if plan.manual_reviews:
        console.print("[bold]Left for manual review[/bold]")
        for item in plan.manual_reviews:
            console.print(f"- {escape(item.description)} {escape(item.reason)}")
        console.print()
    if not plan.statements:
        console.print("Nothing can be applied automatically.")
        return False
    console.print(
        "Oracle commits each successful statement. "
        "A later failure does not undo statements that already succeeded."
    )
    prompt = "Apply these changes to DB2? Type yes to continue: "
    if confirm is None:
        if not sys.stdin.isatty():
            console.print("No interactive terminal was detected. Changes were not applied.")
            return False
        answer = input(prompt)
    else:
        answer = confirm(prompt)
    accepted = answer.strip().lower() in {"y", "yes"}
    if not accepted:
        console.print("No changes were applied.")
    return accepted


def _apply_plan(connection: OracleConnection, plan: ApplyPlan, console: Console) -> int:
    """Execute the planned statements on the already open DB2 connection."""

    failures = 0
    skipped = 0
    for statement in plan.statements:
        reason = _data_blocks_statement(connection, statement)
        if reason is not None:
            skipped += 1
            console.print(f"[yellow]Skipped[/yellow] {escape(statement.description)}")
            console.print(escape(reason))
            continue
        try:
            connection.execute_ddl(statement.sql)
        except Exception as exc:
            failures += 1
            console.print(f"[red]Failed[/red] {escape(statement.description)}")
            console.print(escape(str(exc)))
        else:
            console.print(f"[green]Applied[/green] {escape(statement.description)}")
    if failures:
        console.print(f"{failures} statement(s) failed. Earlier successful statements remain applied.")
    if skipped:
        console.print(
            f"{skipped} statement(s) were skipped because existing DB2 data does not allow them."
        )
    if not failures and not skipped:
        console.print("All statements were applied to DB2.")
    return failures + skipped


def _data_blocks_statement(connection: OracleConnection, statement: SqlStatement) -> str | None:
    """Return why a statement must not run, when current DB2 rows would reject it."""

    if statement.require_no_nulls is not None:
        owner, table_name, column_name = statement.require_no_nulls
        if _rows_exist(
            connection,
            (
                f"SELECT COUNT(*) FROM {quote_identifier(owner)}.{quote_identifier(table_name)} "
                f"WHERE {quote_identifier(column_name)} IS NULL AND ROWNUM = 1"
            ),
        ):
            return (
                f"DB2 has null values in {table_name}.{column_name}. "
                "The column was not changed to NOT NULL. "
                "Fill those values, then run the compare again."
            )
    if statement.max_stored_length is not None:
        owner, table_name, column_name, limit, semantics = statement.max_stored_length
        length_function = "LENGTH" if semantics == "CHAR" else "LENGTHB"
        unit = "characters" if semantics == "CHAR" else "bytes"
        if _rows_exist(
            connection,
            (
                f"SELECT COUNT(*) FROM {quote_identifier(owner)}.{quote_identifier(table_name)} "
                f"WHERE {length_function}({quote_identifier(column_name)}) > :limit AND ROWNUM = 1"
            ),
            {"limit": limit},
        ):
            return (
                f"DB2 has a value in {table_name}.{column_name} longer than {limit} {unit}. "
                "The column length was not reduced."
            )
    return None


def _rows_exist(
    connection: OracleConnection,
    sql: str,
    params: dict[str, object] | None = None,
) -> bool:
    _columns, rows = connection.fetchall(sql, params)
    return bool(rows and rows[0] and rows[0][0])
