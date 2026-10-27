from models.schema_models import DiffStatus
from comparison.comparison_service import compare_schemas
from tests.factories import make_column, make_snapshot, make_table, modern_capabilities


def test_identical_schemas_have_zero_differences() -> None:
    table = make_table(
        comment="Claim",
        columns=[
            make_column(),
            make_column(
                column_name="STATUS",
                column_id=2,
                data_type="VARCHAR2",
                data_length=100,
                data_precision=None,
                data_scale=None,
                char_length=100,
                char_used="B",
                nullable=True,
            ),
        ],
    )
    result = compare_schemas(
        make_snapshot(tables=[table]),
        make_snapshot(label="DB2", tables=[table.model_copy(deep=True)]),
    )

    assert result.differences == []
    assert result.total_differences == 0
    assert result.total_warnings == 0
    assert result.matching_table_count == 1
    assert result.db1_change_count == 0


def test_missing_table_does_not_repeat_its_columns() -> None:
    table = make_table(columns=[make_column(), make_column(column_name="STATUS", column_id=2)])
    result = compare_schemas(
        make_snapshot(tables=[table]),
        make_snapshot(label="DB2", tables=[]),
    )

    column_diffs = [item for item in result.differences if item.object_type == "COLUMN"]
    table_diffs = [
        item
        for item in result.differences
        if item.object_type == "TABLE" and item.status == DiffStatus.MISSING_IN_DB2
    ]
    assert column_diffs == []
    assert [item.table_name for item in table_diffs] == ["RCL_CLAIM"]
    assert result.missing_table_count == 1
    assert result.total_warnings == 1
    assert result.db1_table_count == 1
    assert result.db2_table_count == 0


def test_different_schema_owner_names_are_not_column_differences() -> None:
    left_table = make_table(owner="ROWIS")
    right_table = make_table(owner="ROWIS_NEW")
    result = compare_schemas(
        make_snapshot(owner="ROWIS", tables=[left_table]),
        make_snapshot(label="DB2", owner="ROWIS_NEW", tables=[right_table]),
    )

    assert result.db1.owner == "ROWIS"
    assert result.db2.owner == "ROWIS_NEW"
    assert result.differences == []
    assert result.matching_table_count == 1


def test_both_empty_schemas_warn_and_do_not_invent_differences() -> None:
    result = compare_schemas(
        make_snapshot(tables=[]),
        make_snapshot(label="DB2", tables=[]),
    )

    assert result.total_differences == 0
    assert result.total_warnings == 2
    assert all(item.status == DiffStatus.WARNING for item in result.differences)


def test_unavailable_metadata_is_a_warning_not_a_false_difference() -> None:
    column = make_column(
        data_type="VARCHAR2",
        data_precision=None,
        data_scale=None,
        char_length=100,
        char_used="B",
        nullable=True,
        comment="Status",
    )
    other = column.model_copy(update={"char_used": "C", "comment": "Different"})
    capabilities = modern_capabilities(char_used=False, column_comments=False)
    result = compare_schemas(
        make_snapshot(tables=[make_table(columns=[column])], capabilities=capabilities),
        make_snapshot(
            label="DB2",
            tables=[make_table(columns=[other])],
            capabilities=capabilities,
        ),
    )

    properties = {item.property_name for item in result.differences}
    assert "character_semantics" not in properties
    assert "comment" not in properties
    assert result.total_warnings >= 2
    assert any("Character semantics" in (item.detail or "") for item in result.differences)


def test_metadata_warning_from_a_snapshot_is_kept() -> None:
    result = compare_schemas(
        make_snapshot(warnings=["DB1: could not read ALL_COL_COMMENTS."]),
        make_snapshot(label="DB2"),
    )

    assert any(
        item.status == DiffStatus.WARNING and "ALL_COL_COMMENTS" in (item.detail or "")
        for item in result.differences
    )


def test_prefixes_must_match() -> None:
    with __import__("pytest").raises(ValueError):
        compare_schemas(
            make_snapshot(),
            make_snapshot(label="DB2", prefix="DOC_"),
        )
