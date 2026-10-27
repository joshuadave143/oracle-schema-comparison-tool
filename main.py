"""Command-line entry point for the Oracle schema comparison tool.

Working commands:

- ``python main.py test-connections``
- ``python main.py compare --prefix ROWIS_``
- ``python main.py compare --prefix CLAIM_ --db1-owner ROWIS --db2-owner ROWIS_NEW``

After a comparison, the tool asks before applying table and column changes to DB2.
"""

import argparse

from cli import compare_command, test_connections_command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=(
            "Compare Oracle table and column metadata between an original "
            "database (DB1) and a new database (DB2)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  python main.py test-connections
  python main.py compare --prefix ROWIS_
  python main.py compare --prefix CLAIM_ --db1-owner ROWIS --db2-owner ROWIS_NEW

After the report, type yes to apply table and column changes to DB2.
Constraints, indexes, and file reports are not available yet.
""",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to the environment file. Defaults to .env in the current directory.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    test_parser = subparsers.add_parser(
        "test-connections",
        help="Test the DB1 and DB2 connections independently.",
    )
    test_parser.set_defaults(func=test_connections_command)

    compare_parser = subparsers.add_parser(
        "compare",
        help="Compare tables and columns for a table-name prefix.",
    )
    compare_parser.add_argument(
        "--prefix",
        help="Table-name prefix to compare. Overrides TABLE_PREFIX from the environment.",
    )
    compare_parser.add_argument(
        "--db1-owner",
        help="Schema owner on DB1. Defaults to the DB1 login user, uppercased.",
    )
    compare_parser.add_argument(
        "--db2-owner",
        help="Schema owner on DB2. Defaults to the DB2 login user, uppercased.",
    )
    compare_parser.set_defaults(func=compare_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
