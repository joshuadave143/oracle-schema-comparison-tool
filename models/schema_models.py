"""Models shared by metadata retrieval, comparison, and reports.

Storage attributes such as tablespace are kept on the table model so a later
migration stage can see them. They are not schema-drift differences: tablespace
names often differ between environments on purpose.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class DiffStatus(str, Enum):
    """Classification for every compared object or property."""

    MISSING_IN_DB2 = "MISSING_IN_DB2"
    DIFFERENT = "DIFFERENT"
    ONLY_IN_DB2 = "ONLY_IN_DB2"
    MATCH = "MATCH"
    WARNING = "WARNING"


class Severity(str, Enum):
    """How urgent a difference is for a DB1-to-DB2 schema update."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class SchemaCapabilities(BaseModel):
    """Which optional dictionary properties were readable on one database.

    A property is compared only when both databases expose it. Otherwise the
    comparison records a warning instead of a false difference.
    """

    model_config = ConfigDict(extra="forbid")

    table_comments: bool = False
    column_comments: bool = False
    char_length: bool = False
    char_used: bool = False
    hidden_column: bool = False
    virtual_column: bool = False
    identity_column: bool = False
    identity_details: bool = False
    default_on_null: bool = False
    data_default: bool = False


class ColumnDefinition(BaseModel):
    """One column from ``ALL_TAB_COLUMNS`` plus comments and identity details."""

    model_config = ConfigDict(extra="forbid")

    owner: str
    table_name: str
    column_name: str
    column_id: int | None = None
    data_type: str
    data_type_owner: str | None = None
    data_length: int | None = None
    data_precision: int | None = None
    data_scale: int | None = None
    char_length: int | None = None
    char_used: str | None = None
    nullable: bool = True
    data_default: str | None = None
    hidden: bool = False
    virtual: bool = False
    identity: bool = False
    identity_generation: str | None = None
    identity_options: str | None = None
    default_on_null: bool = False
    comment: str | None = None


class TableDefinition(BaseModel):
    """One table from ``ALL_TABLES`` and the columns gathered for it."""

    model_config = ConfigDict(extra="forbid")

    owner: str
    table_name: str
    tablespace_name: str | None = None
    temporary: bool = False
    duration: str | None = None
    iot_type: str | None = None
    partitioned: bool = False
    nested: bool = False
    status: str | None = None
    cluster_name: str | None = None
    comment: str | None = None
    columns: list[ColumnDefinition] = Field(default_factory=list)


class DatabaseContext(BaseModel):
    """Connection facts that are safe to put in a report."""

    model_config = ConfigDict(extra="forbid")

    label: str
    owner: str
    endpoint: str
    oracle_version: str | None = None
    driver_mode: str | None = None


class SchemaSnapshot(BaseModel):
    """Metadata retrieved for one database and one table prefix."""

    model_config = ConfigDict(extra="forbid")

    database: DatabaseContext
    prefix: str
    tables: list[TableDefinition]
    capabilities: SchemaCapabilities
    warnings: list[str] = Field(default_factory=list)


class Difference(BaseModel):
    """One compared fact. Reports and a later JSON export share this model."""

    model_config = ConfigDict(extra="forbid")

    status: DiffStatus
    severity: Severity
    object_type: str
    table_name: str | None = None
    object_name: str | None = None
    property_name: str | None = None
    db1_value: str | None = None
    db2_value: str | None = None
    suggested_action: str
    detail: str | None = None


class ComparisonResult(BaseModel):
    """Full result of one table and column comparison."""

    model_config = ConfigDict(extra="forbid")

    compared_at: datetime
    prefix: str
    db1: DatabaseContext
    db2: DatabaseContext
    db1_table_count: int
    db2_table_count: int
    matching_table_count: int
    missing_table_count: int
    only_db2_table_count: int
    differences: list[Difference]
    scope: str

    @property
    def db1_change_count(self) -> int:
        """Changes present in DB1 that are missing or different in DB2."""

        return sum(
            1
            for difference in self.differences
            if difference.status in {DiffStatus.MISSING_IN_DB2, DiffStatus.DIFFERENT}
        )

    @property
    def only_db2_count(self) -> int:
        return sum(
            1 for difference in self.differences if difference.status == DiffStatus.ONLY_IN_DB2
        )

    @property
    def total_differences(self) -> int:
        return self.db1_change_count + self.only_db2_count

    @property
    def total_warnings(self) -> int:
        return sum(1 for difference in self.differences if difference.status == DiffStatus.WARNING)


def intersect_capabilities(
    left: SchemaCapabilities,
    right: SchemaCapabilities,
) -> SchemaCapabilities:
    """Keep only properties that both databases were able to supply."""

    values = {
        name: bool(getattr(left, name) and getattr(right, name))
        for name in SchemaCapabilities.model_fields
    }
    return SchemaCapabilities(**values)
