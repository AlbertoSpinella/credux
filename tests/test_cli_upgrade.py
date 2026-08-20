import subprocess
import sys

import pytest
from click.testing import CliRunner

from credux import upgrade
from credux.cli import cli
from credux.errors import UpgradeError, UsageError

PIPX_PREFIX = "/home/someone/.local/share/pipx/venvs/credux"


class FakeCompleted:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def make_runner():
    """Keep stdout and stderr separate; click >= 8.2 does that unconditionally."""
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


@pytest.fixture
def calls(credux_home, monkeypatch):
    """A pipx installation whose subprocess calls are recorded, never run."""
    monkeypatch.setattr(upgrade, "_is_editable", lambda: False)
    monkeypatch.setattr(upgrade, "_which", lambda name: "/usr/bin/pipx")
    monkeypatch.setattr(upgrade, "current_version", lambda: "0.1.0")
    monkeypatch.setattr(sys, "prefix", PIPX_PREFIX)
    monkeypatch.setattr(sys, "base_prefix", "/usr")

    recorded = []

    def fake_run(command, **kwargs):
        recorded.append(command)
        return FakeCompleted(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    # Resolution is a request to the index now, not a subprocess, so it is
    # stubbed separately and never shows up in `recorded`.
    monkeypatch.setattr(upgrade, "_fetch_json", lambda url: {"info": {"version": "0.2.0"}})
    return recorded


def test_upgrade_installs_the_newest_release(calls):
    result = make_runner().invoke(cli, ["upgrade"])

    assert result.exit_code == 0, result.output
    assert calls[-1] == [
        "/usr/bin/pipx",
        "install",
        "--force",
        "credux==0.2.0",
    ]
    assert "0.2.0" in result.stdout


def test_upgrade_does_nothing_when_the_newest_release_is_already_installed(calls, monkeypatch):
    monkeypatch.setattr(upgrade, "current_version", lambda: "0.2.0")

    result = make_runner().invoke(cli, ["upgrade"])

    assert result.exit_code == 0, result.output
    # The index was consulted; no installer was run.
    assert calls == []
    assert "0.2.0" in result.stdout


def test_an_explicit_version_is_installed_even_if_it_is_the_current_one(calls, monkeypatch):
    monkeypatch.setattr(upgrade, "current_version", lambda: "0.2.0")

    result = make_runner().invoke(cli, ["upgrade", "--version", "0.2.0"])

    assert result.exit_code == 0, result.output
    # No index lookup is needed when the version is spelled out.
    assert calls == [["/usr/bin/pipx", "install", "--force", "credux==0.2.0"]]


def test_a_failing_installer_exits_with_the_upgrade_error_code(calls, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda command, **kw: FakeCompleted(returncode=1))

    result = make_runner().invoke(cli, ["upgrade", "--version", "0.2.0"])

    assert result.exit_code == UpgradeError.exit_code, result.output


def test_a_version_together_with_a_clone_is_a_usage_error(calls, tmp_path):
    result = make_runner().invoke(
        cli, ["upgrade", "--from", str(tmp_path), "--version", "0.2.0"]
    )

    assert result.exit_code == UsageError.exit_code, result.output
    assert calls == []
