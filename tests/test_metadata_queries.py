from database.metadata_queries import (
    COLUMN_COLUMNS,
    TABLE_COLUMNS,
    build_column_query,
    build_default_query,
    build_identity_query,
    build_table_comment_query,
    build_table_query,
)
from database.sql_guard import assert_read_only_sql


def test_table_query_uses_binds_and_literal_underscore_escape() -> None:
    available = set(TABLE_COLUMNS) | {"SECONDARY"}
    sql = build_table_query(available)

    assert_read_only_sql(sql)
    assert "FROM all_tables" in sql
    assert "owner = :owner" in sql
    assert "ESCAPE '!'" in sql
    assert "NVL(dropped, 'NO') = 'NO'" in sql
    assert "IOT_OVERFLOW" in sql
    assert "NVL(secondary, 'N') = 'N'" in sql
    assert "ROWIS" not in sql
    assert "RCL_" not in sql


def test_column_query_filters_generated_columns_and_omits_the_long_default() -> None:
    available = set(COLUMN_COLUMNS) | {"DATA_DEFAULT"}
    structure = build_column_query(available)
    defaults = build_default_query(available)

    assert_read_only_sql(structure)
    assert "data_default" not in structure.lower()
    assert "user_generated = 'YES'" in structure
    assert defaults is not None
    assert_read_only_sql(defaults)
    assert defaults.lower().strip().endswith("data_default") or "data_default" in defaults.lower()
    assert "user_generated = 'YES'" in defaults


def test_optional_queries_are_read_only() -> None:
    for sql in (
        build_table_comment_query(),
        build_identity_query(),
        build_table_query(set(TABLE_COLUMNS)),
    ):
        assert_read_only_sql(sql)


def test_default_query_is_skipped_when_the_column_is_absent() -> None:
    assert build_default_query({"OWNER", "TABLE_NAME", "COLUMN_NAME"}) is None
