import pytest

from database.ddl_guard import assert_applicable_ddl
from errors import DatabaseConnectionError
from migration.sql_generator import plan_db2_changes
from tests.factories import make_column, make_snapshot, make_table


def _plan(left_table, right_tables, db2_owner: str = "ROWIS_NEW"):
    return plan_db2_changes(
        make_snapshot(owner="ROWIS", tables=[left_table]),
        make_snapshot(label="DB2", owner=db2_owner, tables=right_tables),
    )


def test_missing_table_becomes_create_table() -> None:
    plan = _plan(make_table(comment="O'Brien"), [])

    sql = "\n".join(statement.sql for statement in plan.statements)
    assert "CREATE TABLE ROWIS_NEW.RCL_CLAIM (" in sql
    assert "CLAIM_ID NUMBER(10,0) NOT NULL" in sql
    assert "COMMENT ON TABLE ROWIS_NEW.RCL_CLAIM IS 'O''Brien'" in sql
    assert "DROP" not in sql.upper()
    assert plan.manual_reviews == []


def test_mixed_case_table_name_is_quoted() -> None:
    plan = _plan(make_table(table_name="Rcl_Claim"), [])

    assert any('"Rcl_Claim"' in statement.sql for statement in plan.statements)


def test_missing_column_becomes_alter_add() -> None:
    left = make_table(
        columns=[
            make_column(),
            make_column(
                column_name="STATUS",
                column_id=2,
                data_type="VARCHAR2",
                data_length=20,
                data_precision=None,
                data_scale=None,
                char_length=20,
                char_used="B",
                nullable=True,
                data_default="'OPEN'",
            ),
        ]
    )
    right = make_table(owner="ROWIS_NEW", columns=[make_column()])
    plan = _plan(left, [right])

    add = [statement.sql for statement in plan.statements if statement.sql.startswith("ALTER")]
    assert add == [
        "ALTER TABLE ROWIS_NEW.RCL_CLAIM ADD "
        "(STATUS VARCHAR2(20 BYTE) DEFAULT 'OPEN' NULL)"
    ]


def test_different_character_semantics_become_modify() -> None:
    left_column = make_column(
        column_name="STATUS",
        data_type="VARCHAR2",
        data_precision=None,
        data_scale=None,
        char_length=100,
        char_used="C",
        nullable=True,
    )
    right_column = left_column.model_copy(update={"char_used": "B"})
    plan = _plan(
        make_table(columns=[left_column]),
        [make_table(owner="ROWIS_NEW", columns=[right_column])],
    )

    assert plan.statements[0].sql == (
        "ALTER TABLE ROWIS_NEW.RCL_CLAIM MODIFY (STATUS VARCHAR2(100 CHAR))"
    )


def test_length_change_does_not_restate_existing_nullability() -> None:
    left_column = make_column(
        column_name="TES",
        data_type="VARCHAR2",
        data_precision=None,
        data_scale=None,
        char_length=500,
        char_used="B",
        nullable=True,
    )
    right_column = left_column.model_copy(update={"char_length": 20})
    plan = _plan(
        make_table(table_name="ROWIS_CL_DOC_REQ_D", columns=[left_column]),
        [
            make_table(
                owner="ROWIS_NEW",
                table_name="ROWIS_CL_DOC_REQ_D",
                columns=[right_column],
            )
        ],
    )

    assert plan.statements[0].sql == (
        "ALTER TABLE ROWIS_NEW.ROWIS_CL_DOC_REQ_D MODIFY (TES VARCHAR2(500 BYTE))"
    )


def test_nullability_change_is_included_in_modify() -> None:
    left_column = make_column(
        column_name="STATUS",
        data_type="VARCHAR2",
        data_precision=None,
        data_scale=None,
        char_length=20,
        char_used="B",
        nullable=False,
    )
    right_column = left_column.model_copy(update={"nullable": True})
    plan = _plan(
        make_table(columns=[left_column]),
        [make_table(owner="ROWIS_NEW", columns=[right_column])],
    )

    assert plan.statements[0].sql == (
        "ALTER TABLE ROWIS_NEW.RCL_CLAIM MODIFY (STATUS NOT NULL)"
    )
    assert plan.statements[0].require_no_nulls == ("ROWIS_NEW", "RCL_CLAIM", "STATUS")


def test_length_and_not_null_are_separate_statements() -> None:
    left_column = make_column(
        column_name="TES",
        data_type="VARCHAR2",
        data_precision=None,
        data_scale=None,
        char_length=56,
        char_used="B",
        nullable=False,
    )
    right_column = left_column.model_copy(update={"char_length": 500, "nullable": True})
    plan = _plan(
        make_table(table_name="ROWIS_CL_DOC_REQ_D", columns=[left_column]),
        [
            make_table(
                owner="ROWIS_NEW",
                table_name="ROWIS_CL_DOC_REQ_D",
                columns=[right_column],
            )
        ],
    )

    assert [statement.sql for statement in plan.statements] == [
        "ALTER TABLE ROWIS_NEW.ROWIS_CL_DOC_REQ_D MODIFY (TES VARCHAR2(56 BYTE))",
        "ALTER TABLE ROWIS_NEW.ROWIS_CL_DOC_REQ_D MODIFY (TES NOT NULL)",
    ]
    assert plan.statements[0].max_stored_length == (
        "ROWIS_NEW",
        "ROWIS_CL_DOC_REQ_D",
        "TES",
        56,
        "BYTE",
    )
    assert plan.statements[1].require_no_nulls == ("ROWIS_NEW", "ROWIS_CL_DOC_REQ_D", "TES")


def test_objects_only_in_db2_are_not_dropped() -> None:
    extra = make_column(
        column_name="EXTRA",
        column_id=2,
        data_type="DATE",
        data_precision=None,
        data_scale=None,
        nullable=True,
    )
    plan = _plan(
        make_table(),
        [make_table(owner="ROWIS_NEW", columns=[make_column(), extra])],
    )

    assert plan.statements == []
    assert all("DROP" not in item.reason.upper() for item in plan.manual_reviews)


def test_index_organized_table_is_not_created_automatically() -> None:
    plan = _plan(make_table(iot_type="IOT"), [])

    assert plan.statements == []
    assert any("not created automatically" in item.reason for item in plan.manual_reviews)


def test_ddl_guard_allows_generated_statements_and_rejects_destructive_sql() -> None:
    assert assert_applicable_ddl("CREATE TABLE T (A NUMBER)") == "CREATE TABLE T (A NUMBER)"
    assert "drop" in assert_applicable_ddl("COMMENT ON TABLE T IS 'do not drop'").lower()

    with pytest.raises(DatabaseConnectionError):
        assert_applicable_ddl("DROP TABLE RCL_CLAIM")
    with pytest.raises(DatabaseConnectionError):
        assert_applicable_ddl("ALTER TABLE RCL_CLAIM DROP (STATUS)")
    with pytest.raises(DatabaseConnectionError):
        assert_applicable_ddl("CREATE TABLE T (A NUMBER); DROP TABLE T")
