import os
import sys

import pytest

from credux import fsutil


def test_atomic_write_creates_parent_and_content(tmp_path):
    target = tmp_path / "nested" / "file.txt"

    fsutil.atomic_write_text(target, "hello")

    assert target.read_text(encoding="utf-8") == "hello"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits only")
def test_atomic_write_sets_owner_only_permissions(tmp_path):
    target = tmp_path / "secret.txt"

    fsutil.atomic_write_text(target, "hello")

    assert oct(target.stat().st_mode & 0o777) == "0o600"


def test_atomic_write_leaves_no_temp_files_behind(tmp_path):
    target = tmp_path / "file.txt"

    fsutil.atomic_write_text(target, "one")
    fsutil.atomic_write_text(target, "two")

    assert sorted(p.name for p in tmp_path.iterdir()) == ["file.txt"]
    assert target.read_text(encoding="utf-8") == "two"


def test_backup_once_copies_first_time_only(tmp_path):
    target = tmp_path / "credentials"
    target.write_text("original", encoding="utf-8")

    first = fsutil.backup_once(target)
    target.write_text("changed", encoding="utf-8")
    second = fsutil.backup_once(target)

    assert first == tmp_path / "credentials.credux.bak"
    assert first.read_text(encoding="utf-8") == "original"
    assert second is None


def test_backup_once_returns_none_for_missing_file(tmp_path):
    assert fsutil.backup_once(tmp_path / "absent") is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits only")
def test_backup_once_does_not_inherit_a_loose_source_mode(tmp_path):
    """shutil.copy2 preserves the source mode, which is the wrong default here.

    The only caller backs up ~/.aws/credentials before credux's first managed
    write, so the source is the user's real keys and may already be 0644.
    Credux tightens the live file on every write but never touches the backup
    again, so a copy taken at 0644 stays 0644 forever.
    """
    target = tmp_path / "credentials"
    target.write_text("aws_access_key_id = AKIAREALKEY\n", encoding="utf-8")
    os.chmod(target, 0o644)

    backup = fsutil.backup_once(target)

    assert oct(backup.stat().st_mode & 0o777) == "0o600"


def test_expand_resolves_user_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    assert fsutil.expand("~/.aws/credentials") == tmp_path / ".aws" / "credentials"
