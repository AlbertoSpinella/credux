"""The source distribution ships the project and nothing else."""

from __future__ import annotations

import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Mirrors the sdist include list in pyproject.toml. Kept as an allowlist on
# purpose: this file is published, so it cannot name the paths it exists to
# keep out. Anything that is not part of the project simply has no prefix to
# match here.
ALLOWED_PREFIXES = (
    "src/",
    "tests/",
    "docs/",
    "examples/",
    ".github/",
    "README.md",
    "LICENSE",
    "pyproject.toml",
    ".gitignore",
)

# Written into every sdist by the build backend, not part of the tree.
GENERATED = ("PKG-INFO",)


@pytest.fixture(scope="module")
def sdist_members(tmp_path_factory) -> list[str]:
    """Build a real sdist and return its file names, without the root prefix.

    Module-scoped because the build is slow: `python -m build` provisions an
    isolated environment for the backend, so this runs once for the file
    rather than once per test.
    """
    pytest.importorskip("build", reason="the 'build' test dependency is not installed")
    out = tmp_path_factory.mktemp("sdist")
    result = subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--outdir", str(out), str(ROOT)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"building the sdist failed:\n{result.stdout}\n{result.stderr}")
    archives = list(out.glob("*.tar.gz"))
    assert len(archives) == 1, f"expected exactly one sdist, got {archives}"
    with tarfile.open(archives[0]) as tar:
        return [
            member.name.split("/", 1)[1]
            for member in tar.getmembers()
            if member.isfile() and "/" in member.name
        ]


def test_the_sdist_contains_the_package(sdist_members: list[str]) -> None:
    assert "src/credux/cli.py" in sdist_members


def test_the_sdist_contains_only_allowlisted_paths(sdist_members: list[str]) -> None:
    """A file uploaded to an index can be yanked but never withdrawn.

    The build that reaches the index runs against a checkout that has no
    private tooling in it, so this test is defense in depth: it makes a build
    run by hand, in a checkout that does, fail loudly instead of quietly
    producing a poisoned archive.
    """
    stray = [
        name
        for name in sdist_members
        if not name.startswith(ALLOWED_PREFIXES) and name not in GENERATED
    ]
    assert stray == []
