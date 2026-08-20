"""Writing the AWS shared credentials and config files.

Credux owns a marker-delimited region. Anything a human wrote outside it is
copied through untouched.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .errors import ConfigError
from .fsutil import atomic_write_text, backup_once

BEGIN_MARKER = "# >>> credux managed block >>> do not edit inside"
END_MARKER = "# <<< credux managed block <<<"


@dataclass
class WriteReport:
    """What the caller should tell the user about a write it just made.

    Both facts are about the file as it was *before* the write, and neither
    can be recovered afterwards: the backup is taken once and never again,
    and credux's own `[default]` section lands inside the managed region on
    every write, so afterwards the file always contains one.

    Carries no file content, only a path and a flag, because everything this
    module writes is credential material.
    """

    backup: Path | None = None
    shadowed_default: bool = False


@dataclass
class ProfileEntry:
    profile: str
    access_key_id: str
    secret_access_key: str
    session_token: str | None = None
    region: str | None = None


def _default_entry(default_profile: dict | None) -> ProfileEntry | None:
    if not default_profile or not default_profile.get("enabled", True):
        return None
    return ProfileEntry(
        profile="default",
        access_key_id=default_profile.get("access_key_id", "test"),
        secret_access_key=default_profile.get("secret_access_key", "test"),
    )


def render_credentials_block(entries: Iterable[ProfileEntry]) -> str:
    """The managed region of the credentials file, markers included."""
    lines = [BEGIN_MARKER]
    for entry in entries:
        lines.append("")
        lines.append(f"[{entry.profile}]")
        lines.append(f"aws_access_key_id = {entry.access_key_id}")
        lines.append(f"aws_secret_access_key = {entry.secret_access_key}")
        if entry.session_token:
            lines.append(f"aws_session_token = {entry.session_token}")
    lines.append("")
    lines.append(END_MARKER)
    return "\n".join(lines) + "\n"


def render_config_block(entries: Iterable[ProfileEntry]) -> str:
    """The managed region of the AWS config file, markers included."""
    lines = [BEGIN_MARKER]
    for entry in entries:
        if not entry.region:
            continue
        lines.append("")
        lines.append(f"[profile {entry.profile}]")
        lines.append(f"region = {entry.region}")
    lines.append("")
    lines.append(END_MARKER)
    return "\n".join(lines) + "\n"


def _locate_managed_region(text: str) -> tuple[int, int] | None:
    """Find the (start, end) string indices of BEGIN_MARKER and END_MARKER.

    The end marker is searched for starting at the begin marker's index,
    never from the start of the string. Otherwise human-written text that
    happens to contain an END_MARKER-like line before the real BEGIN_MARKER
    would be mistaken for the close of the region: `end` would land before
    `start`, and every caller built on this - replacing the region on write,
    or reading it back - would either treat the region as absent or slice
    out the wrong text as the managed body.

    This is the single source of truth for "where is the managed region";
    `replace_managed_region` and `read_managed_entries` both call it so the
    two can never drift apart on how they find it.

    Returns None if no complete BEGIN_MARKER...END_MARKER pair is found.
    """
    start = text.find(BEGIN_MARKER)
    if start == -1:
        return None
    end = text.find(END_MARKER, start)
    if end == -1:
        return None
    return start, end


def replace_managed_region(existing: str, block: str) -> str:
    """Swap the managed region inside existing text, or append it.

    One asymmetry is intentional rather than a bug: a BEGIN_MARKER left
    dangling without its matching END_MARKER (a human deleted only the
    closing line) is not repaired on this call - the orphaned marker is
    kept as part of the preserved prefix and a fresh block is appended
    after it. On the *next* write, `_locate_managed_region` finds that
    orphaned BEGIN_MARKER first, its search for an END_MARKER lands on the
    newly appended block's closing line, and the two collapse into one
    region. So a half-deleted marker pair self-heals after one further
    write, while a stray END_MARKER-like string before any real
    BEGIN_MARKER can no longer confuse region detection at all (see
    `_locate_managed_region`).
    """
    located = _locate_managed_region(existing)
    if located is None:
        prefix = existing
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        if prefix:
            prefix += "\n"
        return prefix + block
    start, end = located
    tail = existing[end + len(END_MARKER) :]
    tail = tail.lstrip("\n")
    if tail:
        tail = "\n" + tail
    return existing[:start] + block + tail


def has_unmanaged_default(existing: str) -> bool:
    """Whether a `[default]` section sits outside the managed region.

    Only the text a human owns is searched: the managed region is cut out
    first, because credux writes its own `[default]` in there on every write
    and finding that one would report a collision with itself.

    A file holding two `[default]` sections is not an error credux can fix
    for the user - which set of keys an AWS SDK ends up using is decided by
    that SDK's parser, not by credux - so this only ever feeds a warning.
    """
    located = _locate_managed_region(existing)
    if located is None:
        outside = existing
    else:
        start, end = located
        outside = existing[:start] + existing[end + len(END_MARKER) :]
    return any(line.strip() == "[default]" for line in outside.splitlines())


def _write(path: Path, block: str) -> tuple[str, Path | None]:
    """Replace the managed region. Returns the prior text and any backup.

    The backup captures the file as it was *before credux ever touched it*,
    so it is taken only when the existing text carries no managed region.
    Backing up a file that already has one would snapshot credux's own
    output - live session tokens included - and, since `backup_once` skips
    an existing backup, a user who deleted the real snapshot would silently
    get a credux-written file in its place under the same name.
    """
    path = Path(path)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    backup = (
        backup_once(path) if existing and _locate_managed_region(existing) is None else None
    )
    atomic_write_text(path, replace_managed_region(existing, block))
    return existing, backup


def write_credentials(
    path: Path, entries: list[ProfileEntry], default_profile: dict | None
) -> WriteReport:
    """Rewrite the managed region with the given active sessions."""
    ordered: list[ProfileEntry] = []
    fake_default = _default_entry(default_profile)
    if fake_default is not None:
        ordered.append(fake_default)
    ordered.extend(entry for entry in entries if entry.profile != "default")
    existing, backup = _write(path, render_credentials_block(ordered))
    # A handwritten `[default]` only collides with something if credux is
    # actually writing a `default` of its own; with the fake default turned
    # off, the user's is simply the only one in the file.
    shadowed = fake_default is not None and has_unmanaged_default(existing)
    return WriteReport(backup=backup, shadowed_default=shadowed)


def write_config(path: Path, entries: list[ProfileEntry]) -> WriteReport:
    """Rewrite the managed region of the AWS config file."""
    filtered = [entry for entry in entries if entry.profile != "default"]
    _, backup = _write(path, render_config_block(filtered))
    return WriteReport(backup=backup)


def _malformed_block_message(path: Path, exc: configparser.Error) -> str:
    """Describe a parse failure without quoting any of the file's content.

    configparser's own message text is not safe to interpolate here: its
    parse errors embed the offending source line verbatim, and in this file
    every line is either a profile header or a key line carrying real
    credential material, so `{exc}` reliably leaks a secret into an error
    message that `CreduxGroup.invoke` echoes to stderr. Only the *line number*
    is reported, which is enough to find the problem and carries no content.
    """
    lineno = getattr(exc, "lineno", None)
    where = f", line {lineno} of the block" if isinstance(lineno, int) else ""
    return (
        f"{path} has a malformed managed credentials block "
        f"({type(exc).__name__}{where}). "
        "Credux owns everything between the credux-managed markers in "
        "this file; delete that block (the lines between "
        f"{BEGIN_MARKER!r} and {END_MARKER!r}) and credux will "
        "regenerate it on the next write."
    )


def read_managed_entries(path: Path) -> list[ProfileEntry]:
    """Parse the managed region back into entries, skipping the fake default.

    Reading what is already materialized means starting one profile does not
    require re-resolving every other active profile.

    Uses `_locate_managed_region`, the same offset-aware search
    `replace_managed_region` uses, so a human-written line that happens to
    look like END_MARKER before the real block cannot make this return an
    empty list and cause a caller (like `SessionManager.start`) to rewrite
    the file believing no profile is active yet.
    """
    path = Path(path)
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    located = _locate_managed_region(text)
    if located is None:
        return []
    start, end = located
    body = text[start + len(BEGIN_MARKER) : end]
    # interpolation=None: configparser's default BasicInterpolation treats '%'
    # as an escape, so a secret key containing one (AWS generates '+' and '/',
    # but a user-supplied value is not constrained) raises
    # InterpolationSyntaxError - and that exception's message quotes the
    # offending fragment of the value. Credential material is opaque bytes to
    # this parser and must never be interpolated.
    parser = configparser.ConfigParser(interpolation=None)
    entries: list[ProfileEntry] = []
    # The section loop stays inside the try, not after it: one handler over the
    # whole parse is what stops any configparser error from bypassing the
    # content-free wrapper below and reaching the user with the value quoted.
    try:
        parser.read_string(body)
        for name in parser.sections():
            if name == "default":
                continue
            section = parser[name]
            entries.append(
                ProfileEntry(
                    profile=name,
                    access_key_id=section.get("aws_access_key_id", ""),
                    secret_access_key=section.get("aws_secret_access_key", ""),
                    session_token=section.get("aws_session_token") or None,
                )
            )
    except configparser.Error as exc:
        raise ConfigError(_malformed_block_message(path, exc)) from exc
    return entries
