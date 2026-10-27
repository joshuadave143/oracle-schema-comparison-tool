"""User-facing errors for the schema comparison tool.

These messages are shown on the command line. They must never include
passwords or other secrets.
"""


class SchemaDiffError(Exception):
    """Base class for errors the command-line interface can report cleanly."""


class ConfigurationError(SchemaDiffError):
    """Raised when environment values or command arguments are invalid."""


class DatabaseConnectionError(SchemaDiffError):
    """Raised when a database connection or a read-only query fails."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class MetadataAccessError(SchemaDiffError):
    """Raised when an Oracle dictionary view cannot be read."""

    def __init__(
        self,
        message: str,
        *,
        view: str | None = None,
        code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.view = view
        self.code = code
