from models.schema_models import DiffStatus
from comparison.table_comparator import compare_tables
from tests.factories import make_column, make_table


def _names(status: DiffStatus, differences: list) -> list[str]:
    return [
        difference.table_name
        for difference in differences
        if difference.status == status and difference.property_name == "existence"
    ]


def test_table_missing_from_db2() -> None:
    differences, matched = compare_tables(
        [make_table(table_name="RCL_CLAIM", columns=[make_column(column_name="CLAIM_ID")])],
        [],
        db1_owner="ROWIS",
        db2_owner="ROWIS_NEW",
        compare_comments=True,
    )

    assert matched == []
    assert _names(DiffStatus.MISSING_IN_DB2, differences) == ["RCL_CLAIM"]
    missing = differences[0]
    assert missing.object_type == "TABLE"
    assert "ROWIS_NEW" in missing.suggested_action
    assert "ROWIS" in missing.suggested_action
    assert not any(difference.object_type == "COLUMN" for difference in differences)


def test_table_only_in_db2() -> None:
    differences, matched = compare_tables(
        [],
        [make_table(table_name="RCL_EXTRA", owner="ROWIS_NEW")],
        db1_owner="ROWIS",
        db2_owner="ROWIS_NEW",
        compare_comments=True,
    )

    assert matched == []
    assert _names(DiffStatus.ONLY_IN_DB2, differences) == ["RCL_EXTRA"]
    assert "not a missing change" in differences[0].suggested_action


def test_identical_tables_have_no_differences() -> None:
    table = make_table(comment="Claim header")
    differences, matched = compare_tables(
        [table],
        [table.model_copy(deep=True)],
        db1_owner="ROWIS",
        db2_owner="ROWIS",
        compare_comments=True,
    )

    assert len(matched) == 1
    assert differences == []


def test_table_comment_and_temporary_differences() -> None:
    left = make_table(comment="Original", temporary=False)
    right = make_table(comment="  Original  ", temporary=True, duration="SYS$TRANSACTION")
    differences, _matched = compare_tables(
        [left],
        [right],
        db1_owner="ROWIS",
        db2_owner="ROWIS",
        compare_comments=True,
    )

    properties = {difference.property_name for difference in differences}
    assert "comment" not in properties
    assert "temporary" in properties
    assert "duration" in properties


def test_tablespace_difference_is_not_schema_drift() -> None:
    left = make_table(tablespace_name="USERS")
    right = make_table(tablespace_name="DATA_TS")
    differences, matched = compare_tables(
        [left],
        [right],
        db1_owner="ROWIS",
        db2_owner="ROWIS_NEW",
        compare_comments=True,
    )

    assert len(matched) == 1
    assert differences == []


def test_case_insensitive_match_preserves_actual_names() -> None:
    differences, matched = compare_tables(
        [make_table(table_name="RCL_Claim")],
        [make_table(table_name="rcl_claim", owner="ROWIS_NEW")],
        db1_owner="ROWIS",
        db2_owner="ROWIS_NEW",
        compare_comments=True,
    )

    assert len(matched) == 1
    assert matched[0][0].table_name == "RCL_Claim"
    assert matched[0][1].table_name == "rcl_claim"
    assert any(difference.property_name == "table_name" for difference in differences)


def test_table_differences_are_sorted() -> None:
    differences, _matched = compare_tables(
        [make_table(table_name="RCL_B"), make_table(table_name="RCL_A")],
        [],
        db1_owner="ROWIS",
        db2_owner="ROWIS",
        compare_comments=True,
    )

    assert _names(DiffStatus.MISSING_IN_DB2, differences) == ["RCL_A", "RCL_B"]
