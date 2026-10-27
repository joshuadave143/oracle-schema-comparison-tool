from config.settings import ENV_KEYS, settings_from_environ
from errors import ConfigurationError
from tests.factories import sample_environ

import pytest


def test_settings_load_and_hide_password() -> None:
    settings = settings_from_environ(sample_environ())

    assert settings.db1.endpoint == "source_user@192.168.1.100:1521/ORCL"
    assert settings.db2.port == 1521
    assert settings.table_prefix == "RCL_"
    assert settings.db1.reveal_password() == "source-secret"
    rendered = repr(settings) + str(settings)
    assert "source-secret" not in rendered
    assert "target-secret" not in rendered


def test_port_defaults_to_1521() -> None:
    environ = sample_environ()
    del environ["ORACLE_DB1_PORT"]
    del environ["ORACLE_DB2_PORT"]

    settings = settings_from_environ(environ)

    assert settings.db1.port == 1521
    assert settings.db2.port == 1521


def test_blank_prefix_is_unset() -> None:
    settings = settings_from_environ(sample_environ(TABLE_PREFIX="   "))

    assert settings.table_prefix is None


def test_missing_configuration_lists_keys_without_values() -> None:
    with pytest.raises(ConfigurationError) as captured:
        settings_from_environ({"ORACLE_DB1_USER": "source_user"})

    message = str(captured.value)
    assert "ORACLE_DB1_PASSWORD" in message
    assert "ORACLE_DB2_HOST" in message
    assert "source_user" not in message
    assert set(ENV_KEYS).issuperset(
        {"ORACLE_DB1_USER", "ORACLE_DB2_SERVICE_NAME", "TABLE_PREFIX"}
    )


def test_invalid_port_and_host_do_not_echo_the_password() -> None:
    with pytest.raises(ConfigurationError) as captured:
        settings_from_environ(sample_environ(ORACLE_DB1_PORT="not-a-port"))
    assert "source-secret" not in str(captured.value)

    with pytest.raises(ConfigurationError) as captured_host:
        settings_from_environ(
            sample_environ(ORACLE_DB1_HOST="source-secret@192.168.1.100")
        )
    message = str(captured_host.value)
    assert "source-secret" not in message
    assert "without credentials" in message


def test_driver_mode_and_client_directory() -> None:
    settings = settings_from_environ(
        sample_environ(
            ORACLE_DB1_MODE="thick",
            ORACLE_CLIENT_LIB_DIR=r"C:\oracle\instantclient_19_3",
        )
    )

    assert settings.db1.driver_mode == "thick"
    assert settings.db2.driver_mode == "thin"
    assert settings.oracle_client_lib_dir == r"C:\oracle\instantclient_19_3"
    assert "source-secret" not in repr(settings)


def test_invalid_driver_mode_does_not_echo_the_password() -> None:
    with pytest.raises(ConfigurationError) as captured:
        settings_from_environ(sample_environ(ORACLE_DB1_MODE="native"))

    message = str(captured.value)
    assert "thin or thick" in message
    assert "source-secret" not in message


def test_invalid_prefix_in_environment() -> None:
    with pytest.raises(ConfigurationError) as captured:
        settings_from_environ(sample_environ(TABLE_PREFIX="RCL_%"))

    assert "TABLE_PREFIX is invalid" in str(captured.value)
    assert "source-secret" not in str(captured.value)
