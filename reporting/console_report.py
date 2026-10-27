"""Console report for a table and column comparison."""

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from comparison.comparison_service import PRIVILEGE_NOTE
from models.schema_models import ComparisonResult, DatabaseContext, Difference, DiffStatus


def render_comparison(result: ComparisonResult, console: Console) -> None:
    """Print the shared comparison result. This does not write a file."""

    console.print()
    console.rule("Oracle schema comparison")
    console.print(
        f"Compared at [bold]{result.compared_at.strftime('%Y-%m-%d %H:%M:%S UTC')}[/bold]"
    )
    console.print(f"Prefix: [bold]{escape(result.prefix)}[/bold]")
    _print_database(console, result.db1)
    _print_database(console, result.db2)
    console.print()
    console.print(f"Tables in DB1: {result.db1_table_count}")
    console.print(f"Tables in DB2: {result.db2_table_count}")
    console.print(f"Matching tables: {result.matching_table_count}")
    console.print(f"Tables missing in DB2: {result.missing_table_count}")
    console.print(f"Tables only in DB2: {result.only_db2_table_count}")
    console.print(f"DB1 changes not in DB2: {result.db1_change_count}")
    console.print(f"Extra DB2 objects: {result.only_db2_count}")
    console.print(f"Total differences: {result.total_differences}")
    console.print(f"Total warnings: {result.total_warnings}")
    console.print()
    console.print(escape(result.scope))
    console.print(escape(PRIVILEGE_NOTE))
    console.print(
        "File reports are not generated in this version. "
        "You can apply the table and column changes to DB2 after this report."
    )

    changes = [
        difference
        for difference in result.differences
        if difference.status in {DiffStatus.MISSING_IN_DB2, DiffStatus.DIFFERENT}
    ]
    extras = [
        difference
        for difference in result.differences
        if difference.status == DiffStatus.ONLY_IN_DB2
    ]
    warnings = [
        difference
        for difference in result.differences
        if difference.status == DiffStatus.WARNING
    ]

    console.print()
    if changes:
        console.print("[bold]Not yet implemented in DB2[/bold]")
        console.print(_difference_table(changes))
    else:
        console.print("[bold]No table or column changes from DB1 are missing in DB2.[/bold]")

    console.print()
    if extras:
        console.print("[bold]Present only in DB2[/bold]")
        console.print(_difference_table(extras))
    else:
        console.print("No extra tables or columns were found only in DB2.")

    console.print()
    if warnings:
        console.print("[bold]Warnings[/bold]")
        console.print(_warning_table(warnings))
    else:
        console.print("No metadata warnings.")
    console.print()


def _print_database(console: Console, database: DatabaseContext) -> None:
    version = database.oracle_version or "unknown version"
    mode = database.driver_mode or "unknown mode"
    console.print(
        f"{escape(database.label)}: {escape(database.endpoint)} "
        f"owner [bold]{escape(database.owner)}[/bold] "
        f"({escape(version)}, {escape(mode)})"
    )


def _difference_table(differences: list[Difference]) -> Table:
    table = Table(show_lines=False, expand=True, header_style="bold")
    table.add_column("Status", no_wrap=True)
    table.add_column("Severity", no_wrap=True)
    table.add_column("Object")
    table.add_column("Property")
    table.add_column("DB1")
    table.add_column("DB2")
    table.add_column("Suggested action")
    for difference in differences:
        table.add_row(
            difference.status.value,
            difference.severity.value,
            escape(_object_label(difference)),
            escape(difference.property_name or ""),
            escape(_present(difference.db1_value)),
            escape(_present(difference.db2_value)),
            escape(difference.suggested_action),
        )
    return table


def _warning_table(differences: list[Difference]) -> Table:
    table = Table(show_lines=False, expand=True, header_style="bold")
    table.add_column("Warning")
    table.add_column("Suggested action")
    for difference in differences:
        table.add_row(
            escape(difference.detail or difference.suggested_action),
            escape(difference.suggested_action),
        )
    return table


def _object_label(difference: Difference) -> str:
    if difference.object_type == "COLUMN" and difference.table_name and difference.object_name:
        return f"{difference.table_name}.{difference.object_name}"
    if difference.table_name:
        return difference.table_name
    return difference.object_type


def _present(value: str | None) -> str:
    if value is None:
        return "(not present)"
    return value
