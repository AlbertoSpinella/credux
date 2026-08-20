import sys

import pytest

from credux import upgrade
from credux.errors import UpgradeError, UsageError

PIPX_PREFIX = "/home/someone/.local/share/pipx/venvs/credux"
VENV_PREFIX = "/home/someone/src/credux/.venv"

LS_REMOTE = (
    "aaaa\trefs/tags/v0.1.0\n"
    "bbbb\trefs/tags/v0.9.0\n"
    "cccc\trefs/tags/v0.10.0\n"
    "dddd\trefs/tags/nightly\n"
)


def test_repo_url_needs_no_credentials() -> None:
    """A released credux upgrades itself without an ssh key on file.

    `git ls-remote` and the `git+` install spec both run as whatever user
    installed credux, so an ssh URL would make `credux upgrade` fail for
    everyone who is not a collaborator. Anonymous https works against the
    published repository, which is the mirror and never the private tree.
    """
    assert upgrade.REPO_URL.startswith("https://")
    assert upgrade.REPO_URL.endswith("/credux.git")
    assert "private" not in upgrade.REPO_URL


class FakeCompleted:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


class FakeRunner:
    """Stands in for subprocess.run and records what it was asked to do."""

    def __init__(self, stdout="", returncode=0):
        self.calls = []
        self.stdout = stdout
        self.returncode = returncode

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        return FakeCompleted(self.returncode, self.stdout)


@pytest.fixture
def pipx_env(monkeypatch):
    """A pipx-managed, non-editable installation with pipx on PATH."""
    monkeypatch.setattr(upgrade, "_is_editable", lambda: False)
    monkeypatch.setattr(sys, "prefix", PIPX_PREFIX)
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    return None


def test_a_bare_number_and_a_v_prefixed_tag_mean_the_same_tag():
    assert upgrade.normalize_ref("0.2.0") == "v0.2.0"
    assert upgrade.normalize_ref("v0.2.0") == "v0.2.0"


def test_a_ref_that_is_not_a_version_is_refused():
    with pytest.raises(UsageError) as excinfo:
        upgrade.normalize_ref("main")

    assert "main" in str(excinfo.value)


def test_the_newest_tag_is_the_highest_number_not_the_last_string():
    # Lexically 'v0.9.0' sorts after 'v0.10.0'; numerically it is older.
    runner = FakeRunner(stdout=LS_REMOTE)

    assert upgrade.latest_tag(runner=runner) == "v0.10.0"
    assert runner.calls == [["git", "ls-remote", "--tags", "--refs", upgrade.REPO_URL]]


def test_a_repository_with_no_version_tags_is_an_upgrade_error():
    runner = FakeRunner(stdout="dddd\trefs/tags/nightly\n")

    with pytest.raises(UpgradeError):
        upgrade.latest_tag(runner=runner)


def test_a_failing_git_call_is_an_upgrade_error_naming_the_repository():
    runner = FakeRunner(returncode=128)

    with pytest.raises(UpgradeError) as excinfo:
        upgrade.latest_tag(runner=runner)

    assert upgrade.REPO_URL in str(excinfo.value)


def test_a_pipx_installation_is_upgraded_with_pipx(pipx_env, monkeypatch):
    monkeypatch.setattr(upgrade, "_which", lambda name: "/usr/bin/pipx")

    plan = upgrade.plan(version="0.2.0")

    assert plan.manager == "pipx"
    assert plan.target == "v0.2.0"
    assert plan.command == [
        "/usr/bin/pipx",
        "install",
        "--force",
        f"git+{upgrade.REPO_URL}@v0.2.0",
    ]


def test_a_pipx_layout_without_pipx_on_path_reports_the_manual_command(pipx_env, monkeypatch):
    monkeypatch.setattr(upgrade, "_which", lambda name: None)

    with pytest.raises(UpgradeError) as excinfo:
        upgrade.plan(version="0.2.0")

    message = str(excinfo.value)
    assert "pipx" in message
    assert f"git+{upgrade.REPO_URL}@v0.2.0" in message


def test_a_plain_virtualenv_is_upgraded_with_its_own_pip(monkeypatch):
    monkeypatch.setattr(upgrade, "_is_editable", lambda: False)
    monkeypatch.setattr(sys, "prefix", VENV_PREFIX)
    monkeypatch.setattr(sys, "base_prefix", "/usr")

    plan = upgrade.plan(version="0.2.0")

    assert plan.manager == "pip"
    assert plan.command == [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        f"git+{upgrade.REPO_URL}@v0.2.0",
    ]


def test_an_editable_checkout_is_refused_rather_than_overwritten(monkeypatch):
    # Installing a release over `pip install -e .` would silently replace a
    # developer's working tree install with a fixed copy.
    monkeypatch.setattr(upgrade, "_is_editable", lambda: True)
    monkeypatch.setattr(sys, "prefix", VENV_PREFIX)
    monkeypatch.setattr(sys, "base_prefix", "/usr")

    with pytest.raises(UpgradeError) as excinfo:
        upgrade.plan(version="0.2.0")

    assert "editable" in str(excinfo.value)


def test_an_installation_outside_any_virtualenv_is_refused(monkeypatch):
    monkeypatch.setattr(upgrade, "_is_editable", lambda: False)
    monkeypatch.setattr(sys, "prefix", "/usr")
    monkeypatch.setattr(sys, "base_prefix", "/usr")

    with pytest.raises(UpgradeError) as excinfo:
        upgrade.plan(version="0.2.0")

    assert "pipx" in str(excinfo.value)


def test_a_local_clone_is_installed_from_its_path(pipx_env, monkeypatch, tmp_path):
    monkeypatch.setattr(upgrade, "_which", lambda name: "/usr/bin/pipx")

    plan = upgrade.plan(source=str(tmp_path))

    assert plan.command == ["/usr/bin/pipx", "install", "--force", str(tmp_path)]
    assert str(tmp_path) in plan.target


def test_a_local_clone_that_does_not_exist_is_a_usage_error(pipx_env, tmp_path):
    with pytest.raises(UsageError):
        upgrade.plan(source=str(tmp_path / "nowhere"))


def test_a_version_together_with_a_clone_is_a_usage_error(pipx_env, tmp_path):
    # A path install takes whatever is checked out there; honouring --version
    # too would mean silently ignoring one of the two arguments.
    with pytest.raises(UsageError):
        upgrade.plan(version="0.2.0", source=str(tmp_path))


def test_with_no_version_the_newest_tag_is_resolved(pipx_env, monkeypatch):
    monkeypatch.setattr(upgrade, "_which", lambda name: "/usr/bin/pipx")
    runner = FakeRunner(stdout=LS_REMOTE)

    plan = upgrade.plan(runner=runner)

    assert plan.target == "v0.10.0"
    assert plan.command[-1] == f"git+{upgrade.REPO_URL}@v0.10.0"


def test_the_real_runner_never_lets_git_prompt_for_credentials(monkeypatch):
    # The repository is private. Without this, a machine with no ssh key
    # blocks on git's username prompt behind a captured stdout, which looks
    # like a hang rather than a failure.
    import subprocess

    seen = {}
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: seen.update(kwargs) or FakeCompleted(),
    )

    upgrade._run(["git", "ls-remote"], capture=True)

    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert seen["capture_output"] is True


def test_a_pipx_plan_carries_the_venv_it_will_overwrite(pipx_env, monkeypatch):
    monkeypatch.setattr(upgrade, "_which", lambda name: "/usr/bin/pipx")

    plan = upgrade.plan(version="0.2.0")

    assert plan.venv == PIPX_PREFIX


def test_a_pip_plan_carries_no_venv_to_unlock(monkeypatch):
    # pip installs into the venv it is already running in, without recreating
    # it, so there is nothing to make writable.
    monkeypatch.setattr(upgrade, "_is_editable", lambda: False)
    monkeypatch.setattr(sys, "prefix", VENV_PREFIX)
    monkeypatch.setattr(sys, "base_prefix", "/usr")

    assert upgrade.plan(version="0.2.0").venv is None


def test_apply_makes_read_only_venv_scaffolding_writable_first(tmp_path):
    # A Homebrew Python copies its own read-only activate scripts into every
    # venv it creates, and `python -m venv` cannot overwrite a read-only
    # file - so `pipx install --force` fails on exactly the machines where
    # credux was installed with a brew pipx. Unlock before installing.
    scripts = tmp_path / "bin"
    scripts.mkdir()
    activate = scripts / "activate.fish"
    activate.write_text("# activate\n", encoding="utf-8")
    activate.chmod(0o444)
    runner = FakeRunner()
    plan = upgrade.UpgradePlan(
        manager="pipx", target="v0.2.0", command=["pipx", "install"], venv=str(tmp_path)
    )

    upgrade.apply(plan, runner=runner)

    assert activate.stat().st_mode & 0o200, "activate.fish is still read-only"
    assert runner.calls == [["pipx", "install"]]


def test_apply_returns_the_installer_exit_code_on_success():
    runner = FakeRunner()
    plan = upgrade.UpgradePlan(manager="pipx", target="v0.2.0", command=["pipx", "install"])

    assert upgrade.apply(plan, runner=runner) == 0
    assert runner.calls == [["pipx", "install"]]


def test_a_failing_installer_is_an_upgrade_error():
    runner = FakeRunner(returncode=1)
    plan = upgrade.UpgradePlan(manager="pipx", target="v0.2.0", command=["pipx", "install"])

    with pytest.raises(UpgradeError) as excinfo:
        upgrade.apply(plan, runner=runner)

    assert "pipx install" in str(excinfo.value)
