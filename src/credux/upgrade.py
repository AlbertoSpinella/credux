"""Replacing credux's own installation with another release of it."""

from __future__ import annotations

import dataclasses
import importlib.metadata
import json
import re
import sys
from pathlib import Path

from .errors import UpgradeError, UsageError

# Credux is not published to PyPI, so an upgrade always installs from the
# repository itself rather than from an index. This is the https URL, and it
# must stay anonymous-clonable: `credux upgrade` runs as whoever installed
# credux, so an ssh URL would work only for a collaborator and fail for every
# other user. One constant serves both `git ls-remote` and the `git+` install
# spec, so the scheme has to satisfy pip as well as git.
REPO_URL = "https://github.com/AlbertoSpinella/credux.git"

_VERSION_TAG = re.compile(r"^v(\d+)(?:\.(\d+))*$")


@dataclasses.dataclass(frozen=True)
class UpgradePlan:
    """What an upgrade would do, before anything is run."""

    manager: str  # "pipx" or "pip"
    target: str  # human-readable: a tag, or a path
    command: list[str]
    # The venv the installer will recreate, if it recreates one at all. Only
    # pipx does; see `_unlock` for why apply() needs to know.
    venv: str | None = None


def _run(command: list[str], capture: bool = False):
    """Run a command through subprocess, resolved at call time.

    `GIT_TERMINAL_PROMPT=0` is forced on both the tag lookup and the install:
    the repository is private, so a machine without an ssh key would
    otherwise sit at git's username prompt with a captured stdout, looking
    like a hang. With it, git fails immediately and the failure is reported.
    """
    import os
    import subprocess

    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(command, capture_output=capture, text=True, check=False, env=env)


def _which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def current_version() -> str:
    """The version of the credux distribution this process is running from."""
    try:
        return importlib.metadata.version("credux")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - not installed
        raise UpgradeError(
            "credux is running from a source tree with no installed distribution, "
            "so there is nothing to upgrade."
        )


def _is_editable() -> bool:
    """True if this distribution was installed with `pip install -e`."""
    try:
        dist = importlib.metadata.distribution("credux")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover
        return False
    raw = dist.read_text("direct_url.json")
    if not raw:
        return False
    try:
        return bool(json.loads(raw).get("dir_info", {}).get("editable"))
    except (TypeError, ValueError):  # pragma: no cover - malformed metadata
        return False


def normalize_ref(version: str) -> str:
    """Turn '0.2.0' or 'v0.2.0' into the tag name 'v0.2.0'.

    Only release tags are accepted. A branch or a commit sha would install
    unreleased work under a command whose whole promise is "give me a
    release", so it is refused rather than quietly allowed.
    """
    candidate = version if version.startswith("v") else f"v{version}"
    if not _VERSION_TAG.match(candidate):
        raise UsageError(
            f"{version!r} is not a release version. Pass a number like '0.2.0', "
            "or use --from to install from a local clone."
        )
    return candidate


def _sort_key(tag: str) -> tuple[int, ...]:
    return tuple(int(part) for part in tag[1:].split("."))


def latest_tag(runner=None) -> str:
    """Ask the repository for its newest release tag.

    Sorted numerically, not lexically: as strings 'v0.9.0' sorts after
    'v0.10.0', which would pin every future upgrade to an older release.
    """
    if runner is None:
        runner = _run
    result = runner(["git", "ls-remote", "--tags", "--refs", REPO_URL], capture=True)
    if result.returncode != 0:
        raise UpgradeError(
            f"Could not read the tags of {REPO_URL} (git exited "
            f"{result.returncode}). Check the network, or pass --version."
        )
    tags = [
        line.split("refs/tags/", 1)[1].strip()
        for line in (result.stdout or "").splitlines()
        if "refs/tags/" in line
    ]
    versions = [tag for tag in tags if _VERSION_TAG.match(tag)]
    if not versions:
        raise UpgradeError(f"{REPO_URL} has no release tags to upgrade to.")
    return max(versions, key=_sort_key)


def _unlock(venv: str) -> None:
    """Make a venv's scaffolding writable so it can be recreated over.

    `python -m venv` copies its activate scripts out of the interpreter's own
    stdlib, permissions included. A Homebrew interpreter lives in a
    read-only cellar, so every venv it creates gets mode 444 activate
    scripts - and re-creating that venv then fails with `Permission denied:
    .../activate.fish`, which is what `pipx install --force` does on its way
    to installing a new version. Adding owner-write back is the narrowest
    fix: it touches only files credux's own installer is about to overwrite,
    and only ones that are missing the bit.

    Failures here are deliberately ignored: this is a workaround, not the
    upgrade itself, and the installer's own error is a better thing for the
    user to see than a chmod traceback.
    """
    root = Path(venv)
    for directory in (root / "bin", root / "Scripts"):
        if not directory.is_dir():
            continue
        for item in directory.iterdir():
            try:
                mode = item.stat().st_mode
                if not mode & 0o200:
                    item.chmod(mode | 0o200)
            except OSError:  # pragma: no cover - best effort by design
                continue


def _installer(spec: str) -> tuple[str, list[str], str | None]:
    """Work out which installer owns this installation, and how to drive it.

    Only two layouts are supported, and both are virtual environments: the
    pipx venv that `pipx install` creates, and a plain venv the user made
    themselves. Anything else - a system or user-site install - is refused
    rather than pip-installed over, because that writes outside a venv and
    can fight with the OS package manager.
    """
    if _is_editable():
        raise UpgradeError(
            "This credux is an editable install (pip install -e), so it tracks a "
            "working tree rather than a release. Upgrade it with 'git pull' in "
            "that checkout instead."
        )

    prefix = Path(sys.prefix)
    manual = f"pipx install --force {spec}"
    if prefix.parent.name == "venvs" and prefix.parent.parent.name == "pipx":
        pipx = _which("pipx")
        if pipx is None:
            raise UpgradeError(
                f"This credux was installed by pipx, but pipx is not on PATH. "
                f"Run: {manual}"
            )
        return "pipx", [pipx, "install", "--force", spec], str(prefix)

    if sys.prefix != sys.base_prefix:
        return "pip", [sys.executable, "-m", "pip", "install", "--upgrade", spec], None

    raise UpgradeError(
        "credux is not installed in a virtual environment, so it cannot upgrade "
        f"itself safely. Reinstall it with pipx: {manual}"
    )


def plan(version: str | None = None, source: str | None = None, runner=None) -> UpgradePlan:
    """Decide what to install and how, without installing anything."""
    if source is not None:
        if version is not None:
            raise UsageError(
                "--from installs whatever is checked out in that clone, so it "
                "cannot be combined with --version."
            )
        path = Path(source).expanduser()
        if not path.exists():
            raise UsageError(f"No such directory: {source}")
        spec = str(path)
        target = f"the clone at {path}"
    else:
        tag = normalize_ref(version) if version is not None else latest_tag(runner=runner)
        spec = f"git+{REPO_URL}@{tag}"
        target = tag

    manager, command, venv = _installer(spec)
    return UpgradePlan(manager=manager, target=target, command=command, venv=venv)


def apply(plan: UpgradePlan, runner=None) -> int:
    """Run the planned install, letting its output through to the terminal."""
    if runner is None:
        runner = _run
    if plan.venv is not None:
        _unlock(plan.venv)
    result = runner(plan.command)
    if result.returncode != 0:
        raise UpgradeError(
            f"'{' '.join(plan.command)}' exited {result.returncode}; credux was "
            "not upgraded."
        )
    return result.returncode
