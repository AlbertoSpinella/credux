"""Tests that the shell files keep Unix line endings on every platform."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Extensions whose contents are read by a POSIX shell: a carriage return is not
# whitespace there, it is part of the last token on the line, so `fi\r` is an
# unknown command and `then\r` is a syntax error.
POSIX_SHELL_SUFFIXES = (".sh", ".bash", ".zsh", ".fish")


def tracked() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True
    )
    if result.returncode != 0:
        pytest.skip("not a git checkout")
    return [
        path
        for path in result.stdout.splitlines()
        if path.endswith(POSIX_SHELL_SUFFIXES)
    ]


def test_every_posix_shell_file_is_checked_out_with_lf():
    """git's autocrlf is on by default on Windows, and it rewrites a checkout
    unless an attribute says otherwise. The wrapper under src/credux/shell/ is
    shipped in the wheel and printed by `credux shell-init` for the user to
    eval, so a checkout that turned it into CRLF would build a package that
    hands every user a broken snippet."""
    paths = tracked()
    assert paths, "no shell files found: the glob or the checkout is wrong"

    result = subprocess.run(
        ["git", "check-attr", "eol", "--", *paths], cwd=ROOT, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    wrong = [line for line in result.stdout.splitlines() if not line.endswith(": eol: lf")]
    assert wrong == []


def test_no_shell_file_in_the_working_tree_contains_a_carriage_return():
    """The attribute above is only a promise about future checkouts; this is
    the state on disk right now, which is what actually runs."""
    offenders = [path for path in tracked() if b"\r" in (ROOT / path).read_bytes()]

    assert offenders == []
