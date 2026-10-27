import pytest

from database.identifiers import (
    default_owner_for_user,
    matches_prefix,
    normalize_owner,
    parse_prefix,
    prefix_to_like_pattern,
)
from database.sql_guard import assert_read_only_sql
from errors import MetadataAccessError


@pytest.mark.parametrize(
    "prefix",
    ["RCL_", "CLAIM_", "DOC_", "rcl_", "A1", "RCL$", "RCL#"],
)
def test_valid_prefixes(prefix: str) -> None:
    assert parse_prefix(f"  {prefix}  ") == prefix


@pytest.mark.parametrize(
    "prefix",
    ["", "   ", None, "RCL_%", "RC L_", "RCL_'", 'RCL_"', "SCHEMA.RCL_", "RCL_;DROP"],
)
def test_invalid_prefixes(prefix: str | None) -> None:
    with pytest.raises(ValueError):
        parse_prefix(prefix)


def test_empty_prefix_explains_why_it_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        parse_prefix("")


def test_like_pattern_escapes_underscore() -> None:
    assert prefix_to_like_pattern("RCL_") == "RCL!_%"
    assert prefix_to_like_pattern("doc_") == "DOC!_%"
    assert matches_prefix("RCL_CLAIM", "rcl_")
    assert matches_prefix("rcl_claim", "RCL_")
    assert not matches_prefix("RCLX", "RCL_")
    assert not matches_prefix("OTHER", "RCL_")


def test_owner_normalization() -> None:
    assert normalize_owner("rowis") == "ROWIS"
    assert normalize_owner("  Rowis_New ") == "ROWIS_NEW"
    assert normalize_owner('"Rowis"') == "Rowis"
    assert default_owner_for_user("source_user") == "SOURCE_USER"

    with pytest.raises(ValueError):
        normalize_owner("ROW IS")
    with pytest.raises(ValueError):
        normalize_owner("1ROWIS")
    with pytest.raises(ValueError):
        normalize_owner('ROWIS"; DROP')


def test_sql_guard_allows_metadata_selects_and_blocks_changes() -> None:
    assert_read_only_sql("SELECT table_name, comments FROM all_tab_comments")
    assert_read_only_sql(
        "SELECT table_name FROM all_tables WHERE NVL(dropped, 'NO') = 'NO'"
    )

    with pytest.raises(MetadataAccessError):
        assert_read_only_sql("DROP TABLE RCL_CLAIM")
    with pytest.raises(MetadataAccessError):
        assert_read_only_sql("UPDATE RCL_CLAIM SET claim_id = 1")
    with pytest.raises(MetadataAccessError):
        assert_read_only_sql("SELECT * FROM all_tables; DROP TABLE RCL_CLAIM")
