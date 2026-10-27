"""Load and validate the two Oracle connection settings.

Credentials are read from the process environment or a ``.env`` file. They are
never hardcoded and password values stay in ``SecretStr`` so model reprs do
not reveal them.
"""

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, field_validator

from database.identifiers import parse_prefix
from errors import ConfigurationError

ENV_KEYS = (
    "ORACLE_DB1_USER",
    "ORACLE_DB1_PASSWORD",
    "ORACLE_DB1_HOST",
    "ORACLE_DB1_PORT",
    "ORACLE_DB1_SERVICE_NAME",
    "ORACLE_DB2_USER",
    "ORACLE_DB2_PASSWORD",
    "ORACLE_DB2_HOST",
    "ORACLE_DB2_PORT",
    "ORACLE_DB2_SERVICE_NAME",
    "ORACLE_DB1_MODE",
    "ORACLE_DB2_MODE",
    "ORACLE_CLIENT_LIB_DIR",
    "TABLE_PREFIX",
)

_REQUIRED = {
    "DB1": {
        "user": "ORACLE_DB1_USER",
        "password": "ORACLE_DB1_PASSWORD",
        "host": "ORACLE_DB1_HOST",
        "service_name": "ORACLE_DB1_SERVICE_NAME",
    },
    "DB2": {
        "user": "ORACLE_DB2_USER",
        "password": "ORACLE_DB2_PASSWORD",
        "host": "ORACLE_DB2_HOST",
        "service_name": "ORACLE_DB2_SERVICE_NAME",
    },
}

_PORTS = {"DB1": "ORACLE_DB1_PORT", "DB2": "ORACLE_DB2_PORT"}


class DatabaseSettings(BaseModel):
    """One Oracle connection. The password is excluded from repr and logs."""

    model_config = ConfigDict(extra="forbid")

    label: str
    user: str
    password: SecretStr
    host: str
    port: int = 1521
    service_name: str
    driver_mode: str = "thin"

    @field_validator("user", "host", "service_name")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be blank")
        return text

    @field_validator("user")
    @classmethod
    def _user_has_no_whitespace(cls, value: str) -> str:
        if any(character.isspace() for character in value):
            raise ValueError("must not contain whitespace")
        return value

    @field_validator("host")
    @classmethod
    def _host_without_credentials(cls, value: str) -> str:
        if any(character.isspace() for character in value) or "://" in value or "@" in value:
            raise ValueError(
                "must be a hostname or IP address, without credentials or spaces"
            )
        return value

    @field_validator("service_name")
    @classmethod
    def _service_name_without_whitespace(cls, value: str) -> str:
        if any(character.isspace() for character in value):
            raise ValueError("must not contain whitespace")
        return value

    @field_validator("driver_mode", mode="before")
    @classmethod
    def _driver_mode(cls, value: str) -> str:
        text = str(value).strip().lower()
        if text not in {"thin", "thick"}:
            raise ValueError("must be thin or thick")
        return text

    @field_validator("port")
    @classmethod
    def _valid_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("must be between 1 and 65535")
        return value

    @property
    def endpoint(self) -> str:
        """Connection target safe to print. The password is not included."""

        return f"{self.user}@{self.host}:{self.port}/{self.service_name}"

    def reveal_password(self) -> str:
        return self.password.get_secret_value()


class AppSettings(BaseModel):
    """Both database connections and the optional default table prefix."""

    model_config = ConfigDict(extra="forbid")

    db1: DatabaseSettings
    db2: DatabaseSettings
    table_prefix: str | None = None
    oracle_client_lib_dir: str | None = None


def load_settings(
    dotenv_path: str | Path | None = ".env",
    *,
    use_dotenv: bool = True,
    environ: Mapping[str, str] | None = None,
) -> AppSettings:
    """Load settings.

    Process environment variables win over the dotenv file. Pass ``environ``
    in tests to avoid reading the developer's real environment.
    """

    if environ is None and use_dotenv and dotenv_path is not None:
        path = Path(dotenv_path)
        if path.exists():
            load_dotenv(path, override=False)
    source = os.environ if environ is None else environ
    return settings_from_environ(source)


def settings_from_environ(source: Mapping[str, str]) -> AppSettings:
    """Validate a mapping of environment values."""

    missing: list[str] = []
    databases: dict[str, DatabaseSettings] = {}
    for label, keys in _REQUIRED.items():
        values: dict[str, object] = {"label": label}
        for field, env_name in keys.items():
            raw = source.get(env_name)
            if raw is None or (field != "password" and raw.strip() == "") or raw == "":
                missing.append(env_name)
                continue
            values[field] = raw if field == "password" else raw.strip()
        mode_raw = source.get(f"ORACLE_{label}_MODE")
        if mode_raw is not None and mode_raw.strip() != "":
            values["driver_mode"] = mode_raw.strip()
        port_name = _PORTS[label]
        port_raw = source.get(port_name)
        if port_raw is not None and port_raw.strip() != "":
            try:
                values["port"] = int(port_raw.strip())
            except ValueError as exc:
                raise ConfigurationError(f"{port_name} must be an integer.") from exc
        if missing:
            continue
        try:
            databases[label] = DatabaseSettings(**values)  # type: ignore[arg-type]
        except ValidationError as exc:
            raise ConfigurationError(_format_validation(label, exc)) from exc

    if missing:
        names = ", ".join(missing)
        raise ConfigurationError(
            "Missing required configuration: "
            f"{names}. Copy .env.example to .env and set both Oracle connections."
        )

    prefix = _optional_prefix(source.get("TABLE_PREFIX"))
    lib_dir_raw = source.get("ORACLE_CLIENT_LIB_DIR")
    lib_dir = lib_dir_raw.strip() if lib_dir_raw and lib_dir_raw.strip() else None
    return AppSettings(
        db1=databases["DB1"],
        db2=databases["DB2"],
        table_prefix=prefix,
        oracle_client_lib_dir=lib_dir,
    )


def _optional_prefix(raw: str | None) -> str | None:
    if raw is None or raw.strip() == "":
        return None
    try:
        return parse_prefix(raw)
    except ValueError as exc:
        raise ConfigurationError(f"TABLE_PREFIX is invalid: {exc}") from exc


def _format_validation(label: str, exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        field = ".".join(str(item) for item in error["loc"])
        if "password" in field:
            parts.append(f"{label} password is invalid")
            continue
        parts.append(f"{label} {field}: {error['msg']}")
    return "; ".join(parts) if parts else f"{label} configuration is invalid."
