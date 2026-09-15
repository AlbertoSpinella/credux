import sys
from pathlib import Path

import pytest

from credux import upgrade
from credux.errors import UpgradeError, UsageError

# Spelled the way the running OS spells a path: `_installer` reports the venv
# as `str(Path(sys.prefix))`, which on Windows turns every forward slash into a
# backslash, so a hardcoded POSIX prefix could never match what it returns.
PIPX_PREFIX = str(Path("/home/someone/.local/share/pipx/venvs/credux"))
VENV_PREFIX = str(Path("/home/someone/src/credux/.venv"))

def test_the_install_spec_never_points_at_a_private_source(pipx_env, monkeypatch) -> None:
    """The published project is the only thing a release may install from.

    This replaces the guard that used to sit on the repository URL. The
    constant is gone, but the property it protected is not: whatever
    `upgrade` hands the installer must name the public release and nothing
    else.
    """
    monkeypatch.setattr(upgrade, "_which", lambda name: "/usr/bin/pipx")

    plan = upgrade.plan(version="0.2.0")

    assert plan.command[-1] == "credux==0.2.0"
    assert "private" not in plan.command[-1]
    assert "git+" not in plan.command[-1]


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    """Stands in for subprocess.run and records what it was asked to do."""

    def __init__(self, stdout="", returncode=0, stderr=""):
        self.calls = []
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        return FakeCompleted(self.returncode, self.stdout, self.stderr)


class FakeFetch:
    """Stands in for _fetch_json and records the URLs it was given."""

    def __init__(self, payload=None, error=None):
        self.calls = []
        self.payload = {"info": {"version": "0.10.0"}} if payload is None else payload
        self.error = error

    def __call__(self, url):
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return self.payload


@pytest.fixture
def pipx_env(monkeypatch):
    """A pipx-managed, non-editable installation with pipx on PATH."""
    monkeypatch.setattr(upgrade, "_is_editable", lambda: False)
    monkeypatch.setattr(sys, "prefix", PIPX_PREFIX)
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    return None


def test_a_version_is_normalised_with_or_without_its_tag_prefix():
    assert upgrade.normalize_version("0.2.0") == "0.2.0"
    assert upgrade.normalize_version("v0.2.0") == "0.2.0"


def test_a_branch_name_is_not_a_release():
    with pytest.raises(UsageError) as excinfo:
        upgrade.normalize_version("main")

    assert "main" in str(excinfo.value)


def test_a_pipx_installation_is_upgraded_with_pipx(pipx_env, monkeypatch):
    monkeypatch.setattr(upgrade, "_which", lambda name: "/usr/bin/pipx")

    plan = upgrade.plan(version="0.2.0")

    assert plan.manager == "pipx"
    assert plan.target == "0.2.0"
    assert plan.command == [
        "/usr/bin/pipx",
        "install",
        "--force",
        "--pip-args=--no-cache-dir",
        "credux==0.2.0",
    ]


def test_a_pipx_layout_without_pipx_on_path_reports_the_manual_command(pipx_env, monkeypatch):
    monkeypatch.setattr(upgrade, "_which", lambda name: None)

    with pytest.raises(UpgradeError) as excinfo:
        upgrade.plan(version="0.2.0")

    message = str(excinfo.value)
    assert "pipx" in message
    assert "credux==0.2.0" in message


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
        "--no-cache-dir",
        "credux==0.2.0",
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

    assert plan.command == [
        "/usr/bin/pipx",
        "install",
        "--force",
        "--pip-args=--no-cache-dir",
        str(tmp_path),
    ]
    assert str(tmp_path) in plan.target


def test_a_local_clone_that_does_not_exist_is_a_usage_error(pipx_env, tmp_path):
    with pytest.raises(UsageError):
        upgrade.plan(source=str(tmp_path / "nowhere"))


def test_a_version_together_with_a_clone_is_a_usage_error(pipx_env, tmp_path):
    # A path install takes whatever is checked out there; honouring --version
    # too would mean silently ignoring one of the two arguments.
    with pytest.raises(UsageError):
        upgrade.plan(version="0.2.0", source=str(tmp_path))


def test_with_no_version_the_newest_release_is_resolved(pipx_env, monkeypatch):
    monkeypatch.setattr(upgrade, "_which", lambda name: "/usr/bin/pipx")
    fetch = FakeFetch()

    plan = upgrade.plan(fetch=fetch)

    assert plan.target == "0.10.0"
    assert plan.command[-1] == "credux==0.10.0"


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


def test_the_index_url_names_the_published_project() -> None:
    """The lookup must never be pointed at anything but the published project."""
    assert upgrade.PYPI_JSON_URL.startswith("https://pypi.org/pypi/")
    assert upgrade.PYPI_JSON_URL.endswith("/credux/json")
    assert "private" not in upgrade.PYPI_JSON_URL


def test_the_newest_version_comes_from_the_index() -> None:
    fetch = FakeFetch()

    assert upgrade.latest_version(fetch=fetch) == "0.10.0"
    assert fetch.calls == [upgrade.PYPI_JSON_URL]


def test_an_unreachable_index_is_reported_rather_than_guessed_at() -> None:
    fetch = FakeFetch(error=OSError("no route to host"))

    with pytest.raises(UpgradeError) as excinfo:
        upgrade.latest_version(fetch=fetch)

    message = str(excinfo.value)
    assert upgrade.PYPI_JSON_URL in message
    assert "--version" in message


def test_a_payload_naming_no_version_is_an_error() -> None:
    """A 200 with nothing usable in it must not read as 'no upgrade available'."""
    fetch = FakeFetch(payload={"info": {}})

    with pytest.raises(UpgradeError):
        upgrade.latest_version(fetch=fetch)


# --- the installer must not be answered out of a stale index cache ----------
#
# `latest_version` reads PyPI's JSON document over urllib; the installer reads
# the file listing through pip, which keeps its own copy for as long as PyPI's
# ten-minute `cache-control` allows. Minutes after a release the two disagree,
# and credux then asks pip for a version pip is convinced does not exist.


def test_the_pipx_command_bypasses_pips_index_cache(pipx_env, monkeypatch):
    monkeypatch.setattr(upgrade, "_which", lambda name: "/usr/bin/pipx")

    plan = upgrade.plan(version="0.2.0")

    assert "--pip-args=--no-cache-dir" in plan.command


def test_the_pip_command_bypasses_pips_index_cache(monkeypatch):
    monkeypatch.setattr(upgrade, "_is_editable", lambda: False)
    monkeypatch.setattr(sys, "prefix", VENV_PREFIX)
    monkeypatch.setattr(sys, "base_prefix", "/usr")

    plan = upgrade.plan(version="0.2.0")

    assert "--no-cache-dir" in plan.command


def test_a_version_taken_from_the_index_is_recorded_as_such(pipx_env, monkeypatch):
    monkeypatch.setattr(upgrade, "_which", lambda name: "/usr/bin/pipx")

    plan = upgrade.plan(fetch=FakeFetch())

    assert plan.resolved_from_index is True


def test_a_version_the_user_named_is_not_recorded_as_the_index_answer(pipx_env, monkeypatch):
    monkeypatch.setattr(upgrade, "_which", lambda name: "/usr/bin/pipx")

    assert upgrade.plan(version="0.2.0").resolved_from_index is False


def test_a_local_clone_is_not_recorded_as_the_index_answer(pipx_env, monkeypatch, tmp_path):
    monkeypatch.setattr(upgrade, "_which", lambda name: "/usr/bin/pipx")

    assert upgrade.plan(source=str(tmp_path)).resolved_from_index is False


def test_the_installers_stderr_is_both_shown_and_kept(capfd):
    """Teed, not captured.

    `apply` reads stderr to tell one failure from another, but pipx writes its
    whole commentary there: held back until the process exits, a minute-long
    install would look hung. This is the one test that runs a real
    subprocess - the buffering is the behaviour under test, and a fake runner
    cannot exhibit it.
    """
    result = upgrade._run(
        [sys.executable, "-c", "import sys; sys.stderr.write('installing\\n'); raise SystemExit(3)"]
    )

    assert result.returncode == 3
    assert "installing" in result.stderr, "apply cannot read what was not kept"
    assert "installing" in capfd.readouterr().err, "the user saw nothing"


def test_a_version_the_index_named_but_the_installer_cannot_find_is_explained():
    """The two sources disagreeing is a cache, not a missing release.

    Raw, this reads as "0.10.0 does not exist" one line under credux saying it
    does, and the user concludes the release is broken.
    """
    runner = FakeRunner(
        returncode=1,
        stderr="ERROR: No matching distribution found for credux==0.10.0\n",
    )
    plan = upgrade.UpgradePlan(
        manager="pipx",
        target="0.10.0",
        command=["pipx", "install", "--force", "credux==0.10.0"],
        resolved_from_index=True,
    )

    with pytest.raises(UpgradeError) as excinfo:
        upgrade.apply(plan, runner=runner)

    message = str(excinfo.value)
    assert "0.10.0" in message
    assert "cache" in message.lower()
    assert "again" in message.lower()


def test_a_version_the_user_named_is_not_blamed_on_the_index():
    """`--version 9.9.9` for a release that never existed is a typo, not a lag."""
    runner = FakeRunner(
        returncode=1,
        stderr="ERROR: No matching distribution found for credux==9.9.9\n",
    )
    plan = upgrade.UpgradePlan(
        manager="pipx",
        target="9.9.9",
        command=["pipx", "install", "--force", "credux==9.9.9"],
        resolved_from_index=False,
    )

    with pytest.raises(UpgradeError) as excinfo:
        upgrade.apply(plan, runner=runner)

    assert "cache" not in str(excinfo.value).lower()


def test_a_failure_that_is_not_about_a_missing_version_is_left_alone():
    """A read-only venv, a locked exe: the installer's own error is the answer."""
    runner = FakeRunner(returncode=1, stderr="ERROR: Permission denied\n")
    plan = upgrade.UpgradePlan(
        manager="pipx",
        target="0.10.0",
        command=["pipx", "install", "--force", "credux==0.10.0"],
        resolved_from_index=True,
    )

    with pytest.raises(UpgradeError) as excinfo:
        upgrade.apply(plan, runner=runner)

    assert "cache" not in str(excinfo.value).lower()
