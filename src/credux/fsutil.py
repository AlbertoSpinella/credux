"""Filesystem primitives: expansion, atomic writes, one-time backups."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKUP_SUFFIX = ".credux.bak"


def expand(value: str | Path) -> Path:
    """Expand ~ and environment variables into an absolute-ish Path."""
    return Path(os.path.expandvars(str(value))).expanduser()


def atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    """Write text so readers never observe a partial file.

    The temporary file is created in the destination directory so that
    os.replace stays within one filesystem and is therefore atomic.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def backup_once(path: Path, mode: int = 0o600) -> Path | None:
    """Copy path aside the first time only. Returns the backup path or None.

    The copy is tightened to `mode` rather than inheriting the source's
    permissions. `shutil.copy2` preserves the source mode, and the only
    caller backs up `~/.aws/credentials` before credux's first managed write -
    a file that may well already be world-readable at that point, and whose
    contents are the user's real, pre-existing AWS keys. Credux fixes the live
    file's mode on every write; the backup it leaves beside it must not be
    the loose copy that outlives that fix.

    Windows uses ACLs rather than POSIX mode bits, so the chmod is skipped
    there, matching the platform guard in `doctor.py`.
    """
    path = Path(path)
    if not path.exists():
        return None
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if backup.exists():
        return None
    shutil.copy2(path, backup)
    if sys.platform != "win32":
        os.chmod(backup, mode)
    return backup
