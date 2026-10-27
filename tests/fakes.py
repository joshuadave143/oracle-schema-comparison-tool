"""Scripted Oracle connections for repository tests."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from errors import DatabaseConnectionError


@dataclass
class ScriptStep:
    name: str
    predicate: Callable[[str], bool]
    columns: list[str]
    rows: list[dict[str, Any]] = field(default_factory=list)
    error: Exception | None = None
    captured: tuple[str, dict[str, Any] | None] | None = None


class FakeConnection:
    """Return scripted query results in call order."""

    def __init__(self, steps: list[ScriptStep]) -> None:
        self.steps = list(steps)
        self.driver_mode = "thin"
        self.closed = False
        self.version_value = "19.21.0.0.0"

    @property
    def version(self) -> str:
        return self.version_value

    def fetchall(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[list[str], list[tuple[Any, ...]]]:
        if not self.steps:
            raise AssertionError(f"Unexpected SQL:\n{sql}")
        step = self.steps[0]
        if not step.predicate(sql):
            raise AssertionError(f"Step {step.name!r} did not match SQL:\n{sql}")
        self.steps.pop(0)
        step.captured = (sql, params)
        if step.error is not None:
            raise step.error
        rows = [tuple(row[column.lower()] for column in step.columns) for row in step.rows]
        return [column.upper() for column in step.columns], rows

    def close(self) -> None:
        self.closed = True


def contains(*needles: str) -> Callable[[str], bool]:
    def predicate(sql: str) -> bool:
        lowered = sql.lower()
        return all(needle.lower() in lowered for needle in needles)

    return predicate


def driver_error(message: str, code: int) -> DatabaseConnectionError:
    return DatabaseConnectionError(message, code=code)
