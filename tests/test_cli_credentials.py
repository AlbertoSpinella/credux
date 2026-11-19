import json
import sys

import click
import pytest
from click.testing import CliRunner

from credux import cli as cli_module
from credux.cli import cli
from credux.config import Config
from tests.test_store import memory_keyring  # noqa: F401


@pytest.fixture
def runner(credux_home, memory_keyring, tmp_path, monkeypatch):
    monkeypatch.setenv("CREDUX_SHELL", "posix")
    config = Config.load()
    config.settings["aws_credentials_file"] = str(tmp_path / "credentials")
    config.settings["aws_config_file"] = str(tmp_path / "config")
    config.save()
    return CliRunner()


def add_user(runner, profile_user="ada", extra=()):
    return runner.invoke(
        cli,
        [
            "add", "iam-user",
            "--org", "acme",
            "--account-name", "billing",
            "--account-id", "111111111111",
            "--user", profile_user,
            "--region", "eu-west-1",
            "--access-key-id", "AKIAEXAMPLE",
            "--secret-access-key", "secret",
            "--no-management",
            *extra,
        ],
    )


def test_add_iam_user_names_the_profile_from_the_template(runner):
    result = add_user(runner)

    assert result.exit_code == 0, result.output
    assert "acme-billing-User-ada" in result.output
    assert Config.load().credentials[0]["profile"] == "acme-billing-User-ada"


def test_add_iam_user_stores_the_keys_in_the_keychain(runner, memory_keyring):
    add_user(runner)
    cred_id = Config.load().credentials[0]["id"]

    assert memory_keyring.data[("credux", f"{cred_id}:aws_access_key_id")] == "AKIAEXAMPLE"


def test_add_iam_user_never_echoes_the_secret(runner):
    result = add_user(runner)

    assert "secret" not in result.output


def test_management_flag_applies_the_prefix(runner):
    result = runner.invoke(
        cli,
        [
            "add", "iam-user",
            "--org", "acme",
            "--account-name", "platform",
            "--account-id", "111111111111",
            "--user", "ada",
            "--access-key-id", "AKIA",
            "--secret-access-key", "s",
            "--management",
        ],
    )

    assert "acme-management_platform-User-ada" in result.output


def test_add_iam_role_requires_a_known_source(runner):
    result = runner.invoke(
        cli,
        [
            "add", "iam-role",
            "--org", "acme",
            "--account-name", "prod",
            "--account-id", "222222222222",
            "--role", "Admin",
            "--role-arn", "arn:aws:iam::222222222222:role/Admin",
            "--source", "nothing-here",
            "--no-management",
        ],
    )

    assert result.exit_code == 2
    assert "nothing-here" in result.output


def test_add_iam_role_chains_onto_an_existing_credential(runner):
    add_user(runner)

    result = runner.invoke(
        cli,
        [
            "add", "iam-role",
            "--org", "acme",
            "--account-name", "prod",
            "--account-id", "222222222222",
            "--role", "Admin",
            "--role-arn", "arn:aws:iam::222222222222:role/Admin",
            "--source", "acme-billing-User-ada",
            "--no-management",
        ],
    )

    assert result.exit_code == 0, result.output
    role = [c for c in Config.load().credentials if c["kind"] == "iam-role"][0]
    user = [c for c in Config.load().credentials if c["kind"] == "iam-user"][0]
    assert role["profile"] == "acme-prod-Role-Admin"
    assert role["params"]["source"] == user["id"]


def test_add_sso_session_records_the_start_url(runner):
    result = runner.invoke(
        cli,
        ["add", "sso-session", "acme", "--start-url", "https://acme.awsapps.com/start",
         "--sso-region", "eu-west-1"],
    )

    assert result.exit_code == 0, result.output
    assert Config.load().sso_sessions["acme"]["sso_region"] == "eu-west-1"


def test_add_sso_session_stores_an_optional_container(runner):
    result = runner.invoke(
        cli,
        ["add", "sso-session", "acme", "--start-url", "https://acme.awsapps.com/start",
         "--sso-region", "eu-west-1", "--container", "Acme Work"],
    )

    assert result.exit_code == 0, result.output
    assert Config.load().sso_sessions["acme"]["container"] == "Acme Work"


def test_add_sso_session_omits_container_when_not_given(runner):
    result = runner.invoke(
        cli,
        ["add", "sso-session", "acme", "--start-url", "https://acme.awsapps.com/start",
         "--sso-region", "eu-west-1"],
    )

    assert result.exit_code == 0, result.output
    assert "container" not in Config.load().sso_sessions["acme"]


def test_add_sso_session_points_at_login_before_sync(runner):
    # 'sync' calls sso_login.token_or_raise first, so a freshly registered
    # session cannot be synced until the device flow has run: the hint has to
    # name 'login' first or it sends the user straight into an SsoTokenError.
    result = runner.invoke(
        cli,
        ["add", "sso-session", "acme", "--start-url", "https://acme.awsapps.com/start",
         "--sso-region", "eu-west-1"],
    )

    assert result.exit_code == 0, result.output
    assert "credux login acme" in result.output
    assert result.output.index("credux login acme") < result.output.index("credux sync acme")


def test_list_shows_profiles_and_kinds(runner):
    add_user(runner)

    result = runner.invoke(cli, ["list"])

    assert "acme-billing-User-ada" in result.output
    assert "iam-user" in result.output


def test_list_json_is_machine_readable(runner):
    add_user(runner)

    result = runner.invoke(cli, ["list", "--json"])

    payload = json.loads(result.output)
    assert payload[0]["profile"] == "acme-billing-User-ada"
    assert "secret" not in result.output


def test_remove_deletes_the_credential_and_its_secrets(runner, memory_keyring):
    add_user(runner)
    cred_id = Config.load().credentials[0]["id"]

    result = runner.invoke(cli, ["remove", "acme-billing-User-ada", "--yes"])

    assert result.exit_code == 0, result.output
    assert Config.load().credentials == []
    assert ("credux", f"{cred_id}:aws_access_key_id") not in memory_keyring.data


def test_remove_accepts_a_unique_prefix(runner):
    add_user(runner)

    result = runner.invoke(cli, ["remove", "acme-billing", "--yes"])

    assert result.exit_code == 0, result.output


def add_sso_session(runner, name="acme"):
    return runner.invoke(
        cli,
        [
            "add", "sso-session", name,
            "--start-url", "https://example.awsapps.com/start",
            "--sso-region", "eu-west-1",
        ],
    )


def test_remove_all_deletes_every_credential_its_secrets_and_the_sso_sessions(
    runner, memory_keyring
):
    add_user(runner)
    add_user(runner, profile_user="grace")
    add_sso_session(runner)
    cred_ids = [cred["id"] for cred in Config.load().credentials]

    result = runner.invoke(cli, ["remove", "--all"], input="permanently delete\n")

    assert result.exit_code == 0, result.output
    config = Config.load()
    assert config.credentials == []
    assert config.sso_sessions == {}
    for cred_id in cred_ids:
        assert ("credux", f"{cred_id}:aws_access_key_id") not in memory_keyring.data


def test_remove_all_keeps_everything_when_the_phrase_does_not_match(runner, memory_keyring):
    add_user(runner)
    add_sso_session(runner)
    cred_id = Config.load().credentials[0]["id"]

    result = runner.invoke(cli, ["remove", "--all"], input="yes\n")

    assert result.exit_code == 1, result.output
    config = Config.load()
    assert len(config.credentials) == 1
    assert "acme" in config.sso_sessions
    assert ("credux", f"{cred_id}:aws_access_key_id") in memory_keyring.data


def test_remove_all_refuses_to_be_confirmed_by_yes(runner):
    add_user(runner)

    result = runner.invoke(cli, ["remove", "--all", "--yes"])

    assert result.exit_code == 2, result.output
    assert "--yes" in result.output and "--all" in result.output
    assert Config.load().credentials != []


def test_remove_all_refuses_a_profile_argument(runner):
    add_user(runner)

    result = runner.invoke(cli, ["remove", "acme-billing-User-ada", "--all"])

    assert result.exit_code == 2, result.output
    assert "Pass --all or a profile name, not both" in result.output
    assert Config.load().credentials != []


def test_remove_without_a_profile_or_all_is_a_usage_error(runner):
    result = runner.invoke(cli, ["remove"])

    assert result.exit_code == 2, result.output


def test_remove_all_with_nothing_configured_never_asks_for_the_phrase(runner):
    """No input is supplied: a prompt here would abort with exit code 1."""
    result = runner.invoke(cli, ["remove", "--all"])

    assert result.exit_code == 0, result.output


def test_config_set_writes_a_setting(runner):
    result = runner.invoke(cli, ["config", "set", "refresh_threshold_minutes", "30"])

    assert result.exit_code == 0, result.output
    assert Config.load().settings["refresh_threshold_minutes"] == 30


def test_config_set_creates_a_missing_section(runner):
    result = runner.invoke(cli, ["config", "set", "brand_new.nested", "1"])

    assert result.exit_code == 0, result.output
    assert Config.load().settings["brand_new"] == {"nested": 1}


def test_config_set_through_a_scalar_is_a_usage_error(runner):
    """Walking with node.setdefault(part, {}) raised AttributeError on a
    non-dict node and exited with a traceback."""
    result = runner.invoke(
        cli, ["config", "set", "refresh_threshold_minutes.deep", "1"]
    )

    assert result.exit_code == 2
    assert "refresh_threshold_minutes" in result.output
    # The setting it walked into is untouched.
    assert Config.load().settings["refresh_threshold_minutes"] == 10


@pytest.mark.parametrize("key", ["integrations", "post_sync", "naming"])
def test_config_set_refuses_a_top_level_config_key(runner, key):
    """These live at the top level of config.json, but this command only
    writes under `settings`, so it used to report success while writing
    somewhere nothing reads."""
    result = runner.invoke(cli, ["config", "set", key, "[]"])

    assert result.exit_code == 2
    assert "config edit" in result.output
    assert key.split(".")[0] not in Config.load().settings


def test_unknown_credential_exits_with_code_two(runner):
    result = runner.invoke(cli, ["remove", "ghost", "--yes"])

    assert result.exit_code == 2


def test_main_exits_with_the_credux_error_code_outside_the_test_runner(runner, monkeypatch):
    """CliRunner.invoke bypasses main(); this exercises the real entry point,
    where CreduxGroup maps errors via ctx.exit() instead of a raised exception."""
    monkeypatch.setattr(sys, "argv", ["credux", "remove", "ghost", "--yes"])

    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()

    assert excinfo.value.code == 2


def test_declined_removal_exits_with_the_same_code_via_the_runner_and_main(runner, monkeypatch):
    """A declined confirmation raises click.Abort. Under CliRunner (standalone
    mode) click hardcodes exit 1 for it; main() must agree, or the same user
    action reports two different codes depending on how credux is invoked."""
    add_user(runner)

    cli_result = runner.invoke(cli, ["remove", "acme-billing-User-ada"], input="n\n")
    assert cli_result.exit_code == 1, cli_result.output
    assert Config.load().credentials != []  # declining must not remove anything

    monkeypatch.setattr(sys, "argv", ["credux", "remove", "acme-billing-User-ada"])
    monkeypatch.setattr(click.termui, "visible_prompt_func", lambda prompt: "n")

    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()

    assert excinfo.value.code == cli_result.exit_code == 1


def test_duplicate_add_iam_user_does_not_leak_keys_into_the_keychain(runner, memory_keyring):
    add_user(runner)
    before = dict(memory_keyring.data)

    result = add_user(runner)

    assert result.exit_code == 2, result.output
    assert len(Config.load().credentials) == 1
    assert memory_keyring.data == before


def test_inconclusive_management_detection_does_not_block_the_add(runner, monkeypatch):
    """When org.detect_management cannot tell (returns None), the add must
    still succeed, default to no management prefix, and say so - without
    ever printing the secret."""
    monkeypatch.setattr(cli_module.org_detection, "detect_management", lambda *a, **k: None)

    result = runner.invoke(
        cli,
        [
            "add", "iam-user",
            "--org", "acme",
            "--account-name", "platform",
            "--account-id", "111111111111",
            "--user", "ada",
            "--access-key-id", "AKIA",
            "--secret-access-key", "totally-secret-value",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "acme-platform-User-ada" in result.output
    # Named in full rather than as the bare prefix: "management_" alone would
    # also be a substring of an unrelated mention like management_account_id.
    assert "acme-management_platform-User-ada" not in result.output
    assert "--management" in result.output
    assert "totally-secret-value" not in result.output


def test_config_set_writes_a_naming_template(runner):
    """The naming templates are the one part of config.json a user is most
    likely to want to change, and 'config edit' was the only way to do it."""
    result = runner.invoke(
        cli, ["config", "set", "naming.iam_sso", "{org}_{account_name}_{role}"]
    )

    assert result.exit_code == 0, result.output
    assert Config.load().naming["iam_sso"] == "{org}_{account_name}_{role}"


def test_config_set_writes_the_management_account_prefix(runner):
    result = runner.invoke(cli, ["config", "set", "naming.management_account_prefix", "mgmt-"])

    assert result.exit_code == 0, result.output
    assert Config.load().naming["management_account_prefix"] == "mgmt-"


def test_config_set_rejects_a_naming_template_with_an_unknown_variable(runner):
    result = runner.invoke(cli, ["config", "set", "naming.iam_sso", "{org}-{nonsense}"])

    assert result.exit_code == 2
    assert "nonsense" in result.output
    assert Config.load().naming["iam_sso"] == "{org}-{account_name}-SSO-{role}"


def test_config_set_rejects_a_malformed_naming_template(runner):
    result = runner.invoke(cli, ["config", "set", "naming.iam_role", "{org}-{}"])

    assert result.exit_code == 2
    assert Config.load().naming["iam_role"] == "{org}-{account_name}-Role-{role}"


def test_config_set_refuses_an_unknown_naming_key(runner):
    result = runner.invoke(cli, ["config", "set", "naming.iam_saml", "{org}"])

    assert result.exit_code == 2
    assert "iam_saml" in result.output
    assert "iam_saml" not in Config.load().naming


def test_config_set_refuses_a_naming_key_nested_deeper(runner):
    result = runner.invoke(cli, ["config", "set", "naming.iam_sso.extra", "x"])

    assert result.exit_code == 2
    assert "iam_sso" not in Config.load().naming.get("iam_sso", {})


def test_add_iam_user_says_the_profile_name_came_from_a_default_template(runner):
    result = add_user(runner)

    assert result.exit_code == 0, result.output
    assert "naming.iam_user" in result.stderr
    assert "credux config set naming.iam_user" in result.stderr


def test_the_naming_notice_stays_off_stdout(runner):
    """'credux list --json' and 'credux env' are parsed by other programs."""
    result = add_user(runner)

    assert "naming.iam_user" not in result.stdout


def test_no_naming_notice_once_the_template_is_the_user_s_own(runner):
    runner.invoke(cli, ["config", "set", "naming.iam_user", "{account_name}_{user}"])

    result = add_user(runner)

    assert result.exit_code == 0, result.output
    assert "naming.iam_user" not in result.stderr


def test_add_sso_session_names_the_template_its_profiles_will_use(runner):
    result = runner.invoke(
        cli,
        ["add", "sso-session", "acme", "--start-url", "https://acme.awsapps.com/start",
         "--sso-region", "eu-west-1"],
    )

    assert result.exit_code == 0, result.output
    assert "naming.iam_sso" in result.stderr
