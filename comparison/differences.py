"""Shared difference constructors and deterministic ordering."""

from models.schema_models import DiffStatus, Difference, Severity

_STATUS_ORDER = {
    DiffStatus.MISSING_IN_DB2: 0,
    DiffStatus.DIFFERENT: 1,
    DiffStatus.ONLY_IN_DB2: 2,
    DiffStatus.WARNING: 3,
    DiffStatus.MATCH: 4,
}

_TYPE_ORDER = {"TABLE": 0, "COLUMN": 1, "METADATA": 2}


def make_difference(
    *,
    status: DiffStatus,
    severity: Severity,
    object_type: str,
    table_name: str | None,
    object_name: str | None,
    property_name: str | None,
    db1_value: str | None,
    db2_value: str | None,
    suggested_action: str,
    detail: str | None = None,
) -> Difference:
    return Difference(
        status=status,
        severity=severity,
        object_type=object_type,
        table_name=table_name,
        object_name=object_name,
        property_name=property_name,
        db1_value=db1_value,
        db2_value=db2_value,
        suggested_action=suggested_action,
        detail=detail,
    )


def warning_difference(
    message: str,
    *,
    table_name: str | None = None,
    object_name: str | None = None,
) -> Difference:
    return make_difference(
        status=DiffStatus.WARNING,
        severity=Severity.INFO,
        object_type="METADATA",
        table_name=table_name,
        object_name=object_name,
        property_name=None,
        db1_value=None,
        db2_value=None,
        suggested_action=(
            "Review this metadata warning. The comparison skipped the affected "
            "check instead of reporting an unreliable difference."
        ),
        detail=message,
    )


def sort_differences(differences: list[Difference]) -> list[Difference]:
    """Return a stable order for reports and tests."""

    return sorted(differences, key=_sort_key)


def _sort_key(difference: Difference) -> tuple[object, ...]:
    return (
        _STATUS_ORDER.get(difference.status, 9),
        _TYPE_ORDER.get(difference.object_type, 9),
        difference.table_name or "",
        difference.object_name or "",
        difference.property_name or "",
        difference.detail or "",
    )
