"""Replacing credux's own installation with another release of it."""

from __future__ import annotations

import dataclasses
import importlib.metadata
import json
import re
import sys
from pathlib import Path

from .errors import UpgradeError, UsageError

# Version resolution reads the index rather than the repository. One project
# document answers the only question `upgrade` asks: what is the newest
# release. Keeping it stdlib means no runtime dependency is added for it.
PYPI_JSON_URL = "https://pypi.org/pypi/credux/json"

# The installer must not be answered out of a stale copy of the index. PyPI
# serves the file listing with `cache-control: max-age=600`, and pip keeps its
# own copy for that long, while `latest_version` below reads the JSON document
# over urllib and never sees that cache at all. Minutes after a release the two
# disagree, and credux then asks pip for a version pip is convinced does not
# exist. Bypassing pip's cache costs a re-download of the wheels on every
# upgrade - about 17 MB, nearly all of it botocore - which is the cheaper half
# of the trade against an upgrade that fails for a reason nothing on screen
# explains.
_NO_CACHE = "--no-cache-dir"
_PIPX_NO_CACHE = f"--pip-args={_NO_CACHE}"

# What pip says when the version it was asked for is not in the listing it
# read. Both spellings occur: the first is the resolver's, the second the
# summary pipx quotes back.
_VERSION_NOT_FOUND = re.compile(
    r"no matching distribution found|could not find a version that satisfies",
    re.IGNORECASE,
)

# A socket that never answers looks exactly like a hang, and this call has no
# output of its own to suggest otherwise.
_FETCH_TIMEOUT = 10.0

_VERSION_TAG = re.compile(r"^v?(\d+)(?:\.(\d+))*$")


@dataclasses.dataclass(frozen=True)
class UpgradePlan:
    """What an upgrade would do, before anything is run."""

    manager: str  # "pipx" or "pip"
    target: str  # human-readable: a tag, or a path
    command: list[str]
    # The venv the installer will recreate, if it recreates one at all. Only
    # pipx does; see `_unlock` for why apply() needs to know.
    venv: str | None = None
    # True when the version came from the index rather than from --version or
    # --from. Only then does "the installer cannot find it" contradict
    # something credux said itself, which is what `apply` reports on.
    resolved_from_index: bool = False


def _run(command: list[str]):
    """Run a command through subprocess, resolved at call time.

    stderr is teed rather than captured: `apply` has to read it - one
    installer failure is a stale index and every other one is not, and the
    text is the only thing that tells them apart - but pipx writes its whole
    running commentary there, so swallowing it until the process exits would
    leave a minute-long install looking hung. Each line is written through as
    it arrives and kept. stdout is not touched at all.
    """
    import subprocess

    process = subprocess.Popen(command, stderr=subprocess.PIPE, text=True)
    captured: list[str] = []
    with process:
        for line in process.stderr:  # type: ignore[union-attr]
            sys.stderr.write(line)
            # The installer's output is worth nothing late: unbuffer it so it
            # reaches a redirected stderr at the same time as a terminal one.
            sys.stderr.flush()
            captured.append(line)
    return subprocess.CompletedProcess(command, process.returncode, None, "".join(captured))


def _fetch_json(url: str) -> dict:
    """GET a JSON document, with the import resolved at call time."""
    import urllib.request

    with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def latest_version(fetch=None) -> str:
    """Ask the index for the newest released version of credux.

    No sorting happens here: the index answers with one version, the same one
    pip would resolve, so there is no list to order and no chance of the
    lexical-versus-numeric mistake a tag listing invites.
    """
    if fetch is None:
        fetch = _fetch_json
    try:
        document = fetch(PYPI_JSON_URL)
    except (OSError, ValueError) as exc:
        raise UpgradeError(
            f"Could not read {PYPI_JSON_URL} ({exc}). Check the network, or "
            "pass --version."
        ) from exc
    version = (document.get("info") or {}).get("version")
    if not version:
        raise UpgradeError(f"{PYPI_JSON_URL} names no released version to upgrade to.")
    return version


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


def normalize_version(version: str) -> str:
    """Turn 'v0.2.0' or '0.2.0' into the release version '0.2.0'.

    Only release versions are accepted. A branch or a commit sha would
    install unreleased work under a command whose whole promise is "give me a
    release", so it is refused rather than quietly allowed. The tag prefix is
    accepted and stripped: tags carry it, the index does not.
    """
    if not _VERSION_TAG.match(version):
        raise UsageError(
            f"{version!r} is not a release version. Pass a number like '0.2.0', "
            "or use --from to install from a local clone."
        )
    return version[1:] if version.startswith("v") else version


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


def _refuse_windows_self_replacement(manual: str) -> None:
    """Refuse an upgrade Windows cannot let a running credux perform.

    Every installer path replaces a file credux is holding open. pipx recreates
    the venv over the `python.exe` that is interpreting this call, and even a
    plain pip upgrade rewrites the `credux.exe` shim that *is* the running
    process. Windows locks an executable's image for as long as it runs, so
    the install stops partway with a permission error, having already spent
    the download. Refusing first costs nothing and says something true.
    """
    raise UpgradeError(
        "credux cannot upgrade itself on Windows: the installer has to replace "
        "the credux.exe that is running this command, and Windows locks an "
        "executable while it is running. Nothing has been downloaded or "
        f"changed. Run this from a shell where credux is not running: {manual}"
    )


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
    manual = f"pipx install --force {_PIPX_NO_CACHE} {spec}"
    if prefix.parent.name == "venvs" and prefix.parent.parent.name == "pipx":
        if sys.platform.startswith("win"):
            _refuse_windows_self_replacement(manual)
        pipx = _which("pipx")
        if pipx is None:
            raise UpgradeError(
                f"This credux was installed by pipx, but pipx is not on PATH. "
                f"Run: {manual}"
            )
        return "pipx", [pipx, "install", "--force", _PIPX_NO_CACHE, spec], str(prefix)

    if sys.prefix != sys.base_prefix:
        if sys.platform.startswith("win"):
            _refuse_windows_self_replacement(
                f"{sys.executable} -m pip install --upgrade {_NO_CACHE} {spec}"
            )
        return (
            "pip",
            [sys.executable, "-m", "pip", "install", "--upgrade", _NO_CACHE, spec],
            None,
        )

    raise UpgradeError(
        "credux is not installed in a virtual environment, so it cannot upgrade "
        f"itself safely. Reinstall it with pipx: {manual}"
    )


def plan(version: str | None = None, source: str | None = None, fetch=None) -> UpgradePlan:
    """Decide what to install and how, without installing anything."""
    from_index = False
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
        if version is not None:
            release = normalize_version(version)
        else:
            release = latest_version(fetch=fetch)
            from_index = True
        spec = f"credux=={release}"
        target = release

    manager, command, venv = _installer(spec)
    return UpgradePlan(
        manager=manager,
        target=target,
        command=command,
        venv=venv,
        resolved_from_index=from_index,
    )


def _index_is_behind(plan: UpgradePlan, stderr: str) -> bool:
    """True when the installer could not find the version the index just named.

    Two sources answer the question "what is credux 0.3.0": the JSON document
    `latest_version` reads, and the file listing the installer reads. Only a
    disagreement between them is worth explaining - a version the *user* named
    and the installer cannot find is a typo, and every other installer failure
    speaks for itself.
    """
    return plan.resolved_from_index and bool(_VERSION_NOT_FOUND.search(stderr))


def _failure_message(plan: UpgradePlan, returncode: int, stderr: str) -> str:
    base = (
        f"'{' '.join(plan.command)}' exited {returncode}; credux was not upgraded."
    )
    if not _index_is_behind(plan, stderr):
        return base
    return (
        f"{base}\n\n"
        f"The installer could not find credux {plan.target}, which the index "
        f"named as the newest release moments ago. That contradiction is a "
        f"cache, not a missing release: the install already bypasses pip's "
        f"own copy of the index, so what is still catching up is PyPI's, "
        f"which it serves with a ten-minute lifetime. The JSON document "
        f"credux reads for the version number is refreshed sooner. Nothing "
        f"has changed on this machine - wait a few minutes and run "
        f"'credux upgrade' again."
    )


def apply(plan: UpgradePlan, runner=None) -> int:
    """Run the planned install, letting its output through to the terminal."""
    if runner is None:
        runner = _run
    if plan.venv is not None:
        _unlock(plan.venv)
    result = runner(plan.command)
    # Already on screen: _run wrote each line through as it arrived. This copy
    # is here to be read, not to be printed.
    stderr = getattr(result, "stderr", "") or ""
    if result.returncode != 0:
        raise UpgradeError(_failure_message(plan, result.returncode, stderr))
    return result.returncode
