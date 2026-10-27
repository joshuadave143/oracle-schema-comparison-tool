"""Optional checks against real Oracle databases.

Set ``ORACLE_SCHEMA_DIFF_INTEGRATION=1`` and provide both connections in
``.env`` before running these tests. They are skipped during normal unit
test runs. The tests only read dictionary metadata.
"""

import os

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("ORACLE_SCHEMA_DIFF_INTEGRATION") != "1",
    reason="Set ORACLE_SCHEMA_DIFF_INTEGRATION=1 to run live Oracle tests.",
)
def test_live_connections_and_prefixed_compare() -> None:
    from cli import run_compare, run_connection_tests
    from config.settings import load_settings
    from rich.console import Console

    settings = load_settings()
    console = Console()
    results = run_connection_tests(settings, console=console)
    assert all(result.success for result in results)
    assert settings.table_prefix
    code = run_compare(
        settings,
        prefix=settings.table_prefix,
        db1_owner=None,
        db2_owner=None,
        console=console,
    )
    assert code == 0
