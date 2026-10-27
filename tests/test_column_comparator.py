from models.schema_models import DiffStatus, SchemaCapabilities
from comparison.column_comparator import compare_columns
from comparison.normalization import normalize_default
from tests.factories import make_column, make_table, modern_capabilities


def _properties(differences: list) -> dict[str, object]:
    return {
        difference.property_name: (difference.db1_value, difference.db2_value, difference.status)
        for difference in differences
    }


def _compare(left_columns: list, right_columns: list, capabilities: SchemaCapabilities | None = None):
    return compare_columns(
        make_table(columns=left_columns),
        make_table(owner="ROWIS_NEW", columns=right_columns),
        capabilities or modern_capabilities(),
        db2_owner="ROWIS_NEW",
    )


def test_column_missing_from_db2() -> None:
    differences = _compare(
        [make_column(column_name="CLAIM_ID"), make_column(column_name="STATUS", column_id=2)],
        [make_column(column_name="CLAIM_ID")],
    )

    missing = [item for item in differences if item.status == DiffStatus.MISSING_IN_DB2]
    assert [item.object_name for item in missing] == ["STATUS"]
    assert "ROWIS_NEW.RCL_CLAIM" in missing[0].suggested_action
    assert missing[0].db1_value is not None


def test_column_only_in_db2() -> None:
    differences = _compare(
        [make_column()],
        [make_column(), make_column(column_name="EXTRA", column_id=2, data_type="DATE", data_precision=None, data_scale=None)],
    )

    extra = [item for item in differences if item.status == DiffStatus.ONLY_IN_DB2]
    assert [item.object_name for item in extra] == ["EXTRA"]
    assert extra[0].severity.value == "INFO"
    assert "not a missing change" in extra[0].suggested_action


def test_different_data_types_do_not_also_report_length() -> None:
    differences = _compare(
        [make_column(
            column_name="AMOUNT",
            data_type="VARCHAR2",
            data_length=100,
            data_precision=None,
            data_scale=None,
            char_length=100,
            char_used="B",
        )],
        [make_column(column_name="AMOUNT", data_type="NUMBER", data_precision=10, data_scale=2)],
    )

    assert _properties(differences)["data_type"][0] == "VARCHAR2(100 BYTE)"
    assert _properties(differences)["data_type"][1] == "NUMBER(10,2)"
    assert "character_length" not in _properties(differences)
    assert "data_precision" not in _properties(differences)


def test_character_length_and_semantics_are_distinct() -> None:
    byte_column = make_column(
        column_name="STATUS",
        data_type="VARCHAR2",
        data_length=100,
        data_precision=None,
        data_scale=None,
        char_length=100,
        char_used="B",
        nullable=True,
    )
    longer = byte_column.model_copy(update={"char_length": 200, "data_length": 200})
    semantics = byte_column.model_copy(update={"char_used": "C", "data_length": 400})

    length_diff = _properties(_compare([byte_column], [longer]))
    assert length_diff["character_length"] == ("100", "200", DiffStatus.DIFFERENT)
    assert "character_semantics" not in length_diff
    assert length_diff["character_length"][2] == DiffStatus.DIFFERENT

    semantic_diff = _properties(_compare([byte_column], [semantics]))
    assert semantic_diff["character_semantics"] == ("BYTE", "CHAR", DiffStatus.DIFFERENT)
    assert "character_length" not in semantic_diff
    assert "data_length" not in semantic_diff


def test_shorter_db2_character_length_is_high_severity() -> None:
    left = make_column(
        column_name="STATUS",
        data_type="VARCHAR2",
        data_precision=None,
        data_scale=None,
        char_length=200,
        char_used="B",
        nullable=True,
    )
    right = left.model_copy(update={"char_length": 50})
    differences = _compare([left], [right])

    assert differences[0].property_name == "character_length"
    assert differences[0].severity.value == "HIGH"


def test_numeric_precision_and_scale() -> None:
    left = make_column(data_precision=10, data_scale=2)
    right = make_column(data_precision=12, data_scale=4)
    properties = _properties(_compare([left], [right]))

    assert properties["data_precision"] == ("10", "12", DiffStatus.DIFFERENT)
    assert properties["data_scale"] == ("2", "4", DiffStatus.DIFFERENT)
    assert "data_type" not in properties


def test_nullable_default_position_and_comment() -> None:
    left = make_column(
        nullable=False,
        data_default=" sysdate ",
        column_id=1,
        comment="Identifier",
    )
    right = make_column(
        nullable=True,
        data_default="SYSDATE",
        column_id=3,
        comment="identifier",
    )
    properties = _properties(_compare([left], [right]))

    assert properties["nullable"] == ("NO", "YES", DiffStatus.DIFFERENT)
    assert "data_default" not in properties
    assert properties["column_position"] == ("1", "3", DiffStatus.DIFFERENT)
    assert properties["comment"] == ("Identifier", "identifier", DiffStatus.DIFFERENT)


def test_quoted_default_is_case_sensitive() -> None:
    left = make_column(data_default="'Ab'")
    right = make_column(data_default="'ab'")
    properties = _properties(_compare([left], [right]))

    assert properties["data_default"] == ("'Ab'", "'ab'", DiffStatus.DIFFERENT)


def test_identity_virtual_and_hidden_differences() -> None:
    left = make_column(
        identity=True,
        identity_generation="ALWAYS",
        identity_options="START WITH 1 INCREMENT BY 1",
        data_default="ISEQ$$_1.NEXTVAL",
        virtual=False,
        hidden=False,
    )
    right = make_column(
        identity=False,
        virtual=True,
        hidden=True,
        data_default="CLAIM_ID + 1",
    )
    properties = _properties(_compare([left], [right]))

    assert properties["identity"] == ("YES", "NO", DiffStatus.DIFFERENT)
    assert properties["virtual_column"] == ("NO", "YES", DiffStatus.DIFFERENT)
    assert properties["hidden_column"] == ("NO", "YES", DiffStatus.DIFFERENT)
    assert "data_default" in properties


def test_identity_sequence_defaults_are_not_compared_when_both_are_identity() -> None:
    left = make_column(
        identity=True,
        identity_generation="BY DEFAULT",
        identity_options="START WITH: 1, INCREMENT BY: 1",
        data_default="ISEQ$$_100.NEXTVAL",
    )
    right = make_column(
        identity=True,
        identity_generation="by default",
        identity_options="start with: 1, increment by: 1",
        data_default="ISEQ$$_200.NEXTVAL",
    )

    assert _compare([left], [right]) == []


def test_identical_columns_have_no_differences() -> None:
    column = make_column(
        data_type="VARCHAR2",
        data_length=400,
        data_precision=None,
        data_scale=None,
        char_length=100,
        char_used="C",
        nullable=True,
        data_default=" 'Y' ",
    )
    other = column.model_copy(update={"data_length": 100, "data_default": "'Y'"})

    assert _compare([column], [other]) == []


def test_missing_character_semantics_metadata_does_not_invent_a_difference() -> None:
    capabilities = modern_capabilities(char_used=False)
    left = make_column(
        data_type="VARCHAR2",
        data_precision=None,
        data_scale=None,
        char_length=100,
        char_used="B",
        nullable=True,
    )
    right = left.model_copy(update={"char_used": "C"})

    assert _compare([left], [right], capabilities) == []


def test_default_whitespace_and_unquoted_case_are_normalized() -> None:
    assert normalize_default("  sysdate  ") == "SYSDATE"
    assert normalize_default("'AbC'") == "'AbC'"
    assert normalize_default("NULL") is None
    assert normalize_default("a  ||  'x y'") == "A || 'x y'"
