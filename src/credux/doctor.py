"""Environment diagnostics: what is wrong, and what to do about it."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from . import store
from .awsfiles import has_unmanaged_default
from .config import Config
from .errors import ConfigError, CreduxError
from .fsutil import BACKUP_SUFFIX, expand
from .model import Registry
from .naming import profile_name
from .state import State, utcnow

SEVERITY = {"ok": 0, "warn": 1, "fail": 2}


@dataclass
class Check:
    name: str
    status: str
    detail: str


def worst_status(checks: list[Check]) -> str:
    return max((check.status for check in checks), key=lambda value: SEVERITY[value], default="ok")


UNUSABLE_BACKEND_MODULES = frozenset(
    {"keyring.backends.fail", "keyring.backends.null"}
)


def _secret_store(backend: Callable[[], str]) -> Check:
    name = backend()
    # `backend()` yields a fully qualified "module.ClassName". Match on the
    # module, never on a substring of the whole string: keyring's two no-op
    # backends are both literally called `Keyring`
    # (keyring.backends.fail.Keyring, keyring.backends.null.Keyring), so
    # looking for "fail" or "unavailable" anywhere in the name misses the
    # exact case this check exists for and reports a headless Linux box or a
    # default WSL session as `ok`.
    module = name.rpartition(".")[0]
    if not name or module in UNUSABLE_BACKEND_MODULES or name.startswith(
        store.UNAVAILABLE_PREFIX
    ):
        return Check(
            "secret store",
            "fail",
            f"No usable keychain backend ({name}). On Linux start a Secret Service "
            "provider such as gnome-keyring; on Windows enable Credential Manager.",
        )
    return Check("secret store", "ok", f"keyring backend: {name}")


def _credentials_backup(path) -> Check:
    """Check the one-time backup credux takes before its first managed write.

    That copy holds whatever real credentials the user already had, so it is
    just as sensitive as the live file - and it is not rewritten afterwards,
    so nothing else ever fixes its mode. Checking only the live path would
    report `ok` while a loose copy sits next to it.
    """
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.exists():
        return Check("credentials backup", "ok", "no backup file")
    if sys.platform == "win32":
        return Check("credentials backup", "ok", str(backup))
    try:
        mode = backup.stat().st_mode & 0o777
    except OSError as exc:
        return Check("credentials backup", "fail", f"could not stat {backup}: {exc}")
    if mode & 0o077:
        return Check(
            "credentials backup",
            "warn",
            f"{backup} is mode {oct(mode)}; it holds a copy of your real credentials "
            "and should be 0o600. Tighten it with chmod, or delete it.",
        )
    return Check("credentials backup", "ok", f"{backup} is {oct(mode)}")


def _credentials_file(config: Config) -> list[Check]:
    path = expand(config.setting("aws_credentials_file", "~/.aws/credentials"))
    checks: list[Check] = []
    if not path.exists():
        checks.append(Check("credentials file", "ok", f"{path} does not exist yet"))
        checks.append(_credentials_backup(path))
        checks.append(Check("default profile", "ok", "nothing written yet"))
        checks.append(Check("shadowed default profile", "ok", "nothing written yet"))
        return checks

    checks.append(Check("credentials file", "ok", str(path)))

    # Windows uses ACLs rather than POSIX mode bits, so st_mode carries no
    # meaningful permission signal there. This check is deliberately
    # POSIX-only rather than silently skipped.
    if sys.platform != "win32":
        try:
            mode = path.stat().st_mode & 0o777
        except OSError as exc:
            checks.append(
                Check("credentials permissions", "fail", f"could not stat {path}: {exc}")
            )
        else:
            if mode & 0o077:
                checks.append(
                    Check(
                        "credentials permissions",
                        "warn",
                        f"{path} is mode {oct(mode)}; it should be 0o600. "
                        "Credux will fix this on its next write.",
                    )
                )
            else:
                checks.append(Check("credentials permissions", "ok", oct(mode)))

    checks.append(_credentials_backup(path))

    wanted = config.setting("default_profile", {}) or {}
    if not wanted.get("enabled", True):
        checks.append(Check("default profile", "ok", "disabled by configuration"))
        # With credux writing no `default` of its own, a hand-written one is
        # simply the only one in the file and nothing is being shadowed.
        checks.append(
            Check("shadowed default profile", "ok", "credux writes no default profile")
        )
    else:
        try:
            # `text` exists only for the membership test below and must never
            # be copied - directly or via an exception message - into a
            # Check.detail, since it may contain real AWS secrets. This also
            # means the read failure below must stay generic: OSError (e.g.
            # PermissionError) says nothing but the path, but
            # UnicodeDecodeError's own message embeds the offending byte
            # value and its offset in the file, which is file content too.
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            checks.append(
                Check(
                    "default profile",
                    "warn",
                    f"could not read {path} to check for the fake default profile",
                )
            )
            checks.append(
                Check(
                    "shadowed default profile",
                    "warn",
                    f"could not read {path} to check for a second default profile",
                )
            )
        else:
            if has_unmanaged_default(text):
                checks.append(
                    Check(
                        "shadowed default profile",
                        "warn",
                        f"{path} has a default profile outside credux's managed block as "
                        "well as the placeholder credux writes inside it. Which one an AWS "
                        "SDK uses is decided by that SDK. Rename the one you wrote, or run "
                        "'credux config set default_profile.enabled false'.",
                    )
                )
            else:
                checks.append(
                    Check("shadowed default profile", "ok", "only credux's default profile")
                )
            if f"aws_access_key_id = {wanted.get('access_key_id', 'test')}" in text:
                checks.append(Check("default profile", "ok", "fake default credentials present"))
            else:
                checks.append(
                    Check(
                        "default profile",
                        "warn",
                        "the fake default profile is missing; run any 'credux start' or "
                        "'credux stop' to restore it",
                    )
                )
    return checks


def _browser(config: Config, which: Callable[[str], str | None]) -> Check:
    command = config.setting("browser_command", []) or []
    if not command:
        return Check("browser", "warn", "settings.browser_command is empty")
    binary = command[0]
    if which(binary) is None:
        return Check(
            "browser",
            "warn",
            f"{binary!r} is not on PATH; 'credux console' will fail until it is, or "
            "point settings.browser_command elsewhere",
        )
    return Check(
        "browser",
        "ok",
        f"{binary} found; container links need an extension that handles ext+container URLs",
    )


def _chains(registry: Registry) -> Check:
    ids = {cred.id for cred in registry.all()}
    broken = [
        f"{cred.profile} -> {cred.params['source']}"
        for cred in registry.all()
        if cred.kind == "iam-role"
        and cred.params.get("source")
        and cred.params["source"] not in ids
    ]
    if broken:
        return Check("credential chains", "fail", "dangling source: " + ", ".join(broken))
    return Check("credential chains", "ok", f"{len(ids)} credential(s), no dangling sources")


def _naming(config: Config, registry: Registry) -> Check:
    problems: list[str] = []
    for cred in registry.all():
        variables = dict(cred.params)
        variables.setdefault("role", cred.params.get("role_name"))
        variables.setdefault("region", cred.region)
        try:
            profile_name(cred.kind, config.naming, variables)
        except (ConfigError, CreduxError) as exc:
            problems.append(f"{cred.profile}: {exc}")
    if problems:
        return Check("naming templates", "warn", "; ".join(problems))
    return Check("naming templates", "ok", "every credential renders under the current templates")


def _sessions(state: State, now: datetime | None) -> Check:
    reference = now or utcnow()
    expired = [
        session.profile
        for session in state.all()
        if session.expires_in(reference) is not None and session.expires_in(reference).total_seconds() <= 0
    ]
    if expired:
        return Check(
            "active sessions",
            "warn",
            "expired: " + ", ".join(expired) + "; run 'credux refresh --all'",
        )
    return Check("active sessions", "ok", f"{len(state.all())} active")


def _safe(name: str, fn: Callable[[], list[Check]]) -> list[Check]:
    """Run one diagnostic in isolation.

    `credux doctor` is what a user reaches for when something is already
    broken, so a single check raising - a keyring with no backend, a
    credentials file that suddenly can't be read, a dangling source chain -
    must never stop the remaining checks from reporting.
    """
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - a diagnostic must never crash itself
        return [Check(name, "fail", f"this check failed unexpectedly: {exc}")]


def run_checks(
    config: Config,
    registry: Registry,
    state: State,
    backend: Callable[[], str] | None = None,
    which: Callable[[str], str | None] | None = None,
    now: datetime | None = None,
) -> list[Check]:
    """Run every diagnostic and return its checks."""
    if backend is None:
        backend = store.backend_name
    if which is None:
        which = shutil.which
    checks: list[Check] = []
    checks.extend(_safe("secret store", lambda: [_secret_store(backend)]))
    checks.extend(_safe("credentials file", lambda: _credentials_file(config)))
    checks.extend(_safe("browser", lambda: [_browser(config, which)]))
    checks.extend(_safe("credential chains", lambda: [_chains(registry)]))
    checks.extend(_safe("naming templates", lambda: [_naming(config, registry)]))
    checks.extend(_safe("active sessions", lambda: [_sessions(state, now)]))
    return checks
