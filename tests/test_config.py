import json
from pathlib import Path

import pytest

from credux.config import CONFIG_VERSION, Config
from credux.config import credux_home as credux_home_path
from credux.errors import ConfigError


def test_credux_home_follows_environment(credux_home, monkeypatch):
    assert Config.load().path == credux_home / "config.json"

    monkeypatch.delenv("CREDUX_HOME")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/somewhere")))
    assert credux_home_path() == Path("/somewhere/.credux")


def test_load_creates_defaults_when_absent(credux_home):
    config = Config.load()

    assert config.version == CONFIG_VERSION
    assert config.naming["iam_user"] == "{org}-{account_name}-User-{user}"
    assert config.naming["iam_sso"] == "{org}-{account_name}-SSO-{role}"
    assert config.naming["management_account_prefix"] == "management_"
    assert config.settings["default_profile"] == {
        "enabled": True,
        "access_key_id": "test",
        "secret_access_key": "test",
    }
    assert config.credentials == []


def test_save_then_load_round_trips(credux_home):
    config = Config.load()
    config.credentials.append({"id": "abc", "kind": "iam-user", "profile": "p"})
    config.save()

    on_disk = json.loads((credux_home / "config.json").read_text(encoding="utf-8"))
    assert on_disk["credentials"][0]["id"] == "abc"
    assert Config.load().credentials[0]["profile"] == "p"


def test_user_values_survive_and_missing_defaults_are_filled(credux_home):
    (credux_home / "config.json").write_text(
        json.dumps({"version": 1, "naming": {"iam_user": "{user}"}}), encoding="utf-8"
    )

    config = Config.load()

    assert config.naming["iam_user"] == "{user}"
    assert config.naming["iam_role"] == "{org}-{account_name}-Role-{role}"
    assert config.settings["aws_credentials_file"] == "~/.aws/credentials"


def test_a_newer_version_is_refused(credux_home):
    (credux_home / "config.json").write_text(
        json.dumps({"version": CONFIG_VERSION + 1}), encoding="utf-8"
    )

    with pytest.raises(ConfigError) as excinfo:
        Config.load()

    assert "upgrade credux" in str(excinfo.value)


@pytest.mark.parametrize("version", ["one", None, [1], {}, True])
def test_a_non_numeric_version_is_a_config_error(credux_home, version):
    """config.json is hand-editable, so `version` is whatever the file says.
    Comparing a non-number against an int raised TypeError, which is not a
    CreduxError and escaped as a traceback. `True` is included because bool is
    a subclass of int and would otherwise slip through the isinstance check.
    """
    (credux_home / "config.json").write_text(
        json.dumps({"version": version}), encoding="utf-8"
    )

    with pytest.raises(ConfigError) as excinfo:
        Config.load()

    assert excinfo.value.exit_code == 3
    assert "version" in str(excinfo.value)


def test_setting_reads_dotted_paths(credux_home):
    config = Config.load()

    assert config.setting("default_profile.access_key_id") == "test"
    assert config.setting("nope.missing", "fallback") == "fallback"


def test_a_stock_config_reports_default_naming(credux_home):
    assert Config.load().uses_default_naming is True


def test_a_changed_template_is_no_longer_default_naming(credux_home):
    config = Config.load()
    config.naming["iam_sso"] = "{account_name}_{role}"

    assert config.uses_default_naming is False


def test_a_changed_management_prefix_is_no_longer_default_naming(credux_home):
    config = Config.load()
    config.naming["management_account_prefix"] = "mgmt-"

    assert config.uses_default_naming is False
