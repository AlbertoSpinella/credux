"""How a command-line profile argument is turned into one credential.

Every command that takes a profile name searches for it rather than demanding
it verbatim: a single match is used, several matches are offered as a numbered
list. The prompt and the list belong on stderr, because 'credux env' is meant to
be run as eval "$(credux env NAME)" and anything on stdout would be evaluated
by the shell.
"""

from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from credux import browser
from credux.cli import cli
from credux.config import Config
from credux.model import Credential
from credux.providers import base
from tests.test_store import memory_keyring  # noqa: F401

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

PROFILES = ("acme-svinet-Role-Admin", "beta-svinet-Role-ReadOnly", "acme-dev-Role-Admin")


class FakeProvider(base.Provider):
    name = "fake"
    kinds = ("fake-kind",)

    def resolve(self, cred, ctx):
        return base.ResolvedCredentials("AKIA", "secret", "token", NOW + timedelta(hours=1))

    def console_url(self, cred, ctx, resolved):
        return "https://signin.example/?token=abc"


def make_runner():
    """Keep stdout and stderr separate; click >= 8.2 does that unconditionally."""
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


@pytest.fixture
def runner(credux_home, memory_keyring, tmp_path, monkeypatch):
    monkeypatch.setattr(base, "_PROVIDERS", {})
    base.register(FakeProvider())
    monkeypatch.setenv("CREDUX_SHELL", "posix")

    config = Config.load()
    config.settings["aws_credentials_file"] = str(tmp_path / "credentials")
    config.settings["aws_config_file"] = str(tmp_path / "config")
    for index, profile in enumerate(PROFILES):
        config.credentials.append(
            Credential(
                id=f"c{index}", kind="fake-kind", profile=profile,
                provider="fake", region="eu-west-1",
            ).to_dict()
        )
    config.save()
    return make_runner()


def test_a_unique_substring_needs_no_prompt(runner):
    result = runner.invoke(cli, ["env", "beta-svi"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == 'export AWS_PROFILE="beta-svinet-Role-ReadOnly"'


def test_several_matches_are_offered_as_a_numbered_list(runner):
    result = runner.invoke(cli, ["env", "svi"], input="2\n")

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == 'export AWS_PROFILE="beta-svinet-Role-ReadOnly"'
    assert "1) acme-svinet-Role-Admin" in result.stderr
    assert "2) beta-svinet-Role-ReadOnly" in result.stderr


def test_the_prompt_never_reaches_stdout(runner):
    """stdout is evaluated by the shell, so the picker must not write to it."""
    result = runner.invoke(cli, ["env", "svi"], input="1\n")

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == 'export AWS_PROFILE="acme-svinet-Role-Admin"'


def test_a_rejected_choice_is_asked_again(runner):
    result = runner.invoke(cli, ["env", "svi"], input="9\n1\n")

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == 'export AWS_PROFILE="acme-svinet-Role-Admin"'


def test_several_matches_without_an_answer_report_the_ambiguity(runner):
    """No stdin to read (a script, a pipe): fail with the list rather than
    picking one, and use the usage exit code rather than click's abort."""
    result = runner.invoke(cli, ["env", "svi"], input="")

    assert result.exit_code == 2
    assert "acme-svinet-Role-Admin" in result.stderr
    assert "beta-svinet-Role-ReadOnly" in result.stderr


def test_no_match_reports_the_unknown_name(runner):
    result = runner.invoke(cli, ["env", "nothing-like-this"])

    assert result.exit_code == 2
    assert "nothing-like-this" in result.stderr


def test_start_searches_each_of_its_arguments(runner, tmp_path):
    result = runner.invoke(cli, ["start", "acme-dev", "beta-svi"])

    assert result.exit_code == 0, result.output
    text = (tmp_path / "credentials").read_text(encoding="utf-8")
    assert "[acme-dev-Role-Admin]" in text
    assert "[beta-svinet-Role-ReadOnly]" in text


def test_stop_searches_its_argument(runner, tmp_path):
    runner.invoke(cli, ["start", "beta-svi"])

    result = runner.invoke(cli, ["stop", "beta-svi"])

    assert result.exit_code == 0, result.output
    assert "[beta-svinet-Role-ReadOnly]" not in (tmp_path / "credentials").read_text(
        encoding="utf-8"
    )


def test_console_searches_its_argument(runner, monkeypatch):
    launched = []
    monkeypatch.setattr(browser.subprocess, "Popen", lambda argv: launched.append(argv))

    result = runner.invoke(cli, ["console", "beta-svi"])

    assert result.exit_code == 0, result.output
    assert "ext+container:name=beta-svinet-Role-ReadOnly" in launched[0][1]


def test_remove_searches_its_argument(runner):
    result = runner.invoke(cli, ["remove", "beta-svi", "--yes"])

    assert result.exit_code == 0, result.output
    profiles = [entry["profile"] for entry in Config.load().credentials]
    assert "beta-svinet-Role-ReadOnly" not in profiles
    assert "acme-svinet-Role-Admin" in profiles
