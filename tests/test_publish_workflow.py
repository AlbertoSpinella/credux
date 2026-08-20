"""The release workflow uploads only from the published repository."""

from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "publish.yml"

# Must match the pending publisher registered on the index. Renaming either of
# these silently breaks the upload with an authentication error.
PUBLIC_REPOSITORY = "AlbertoSpinella/credux"
GUARD = f"if: github.repository == '{PUBLIC_REPOSITORY}'"


def test_the_workflow_exists() -> None:
    assert WORKFLOW.is_file()


def test_every_job_is_guarded_to_the_published_repository() -> None:
    """This file is copied into more than one checkout.

    A tag pushed anywhere it lands starts a run, and an unguarded run would
    build a distribution out of whatever tree it found. Every job carries the
    guard, so a run outside the published repository does nothing at all.
    Counting `runs-on:` is the cheapest way to count jobs without a YAML
    parser, which is not a dependency this project has.
    """
    text = WORKFLOW.read_text()
    jobs = text.count("runs-on:")
    assert jobs >= 2, "expected at least a test job and a publish job"
    assert text.count(GUARD) == jobs


def test_the_upload_uses_trusted_publishing_rather_than_a_token() -> None:
    text = WORKFLOW.read_text()
    assert "id-token: write" in text
    assert "pypa/gh-action-pypi-publish" in text
    assert "password:" not in text
    assert "PYPI_API_TOKEN" not in text


def test_the_workflow_refuses_a_tag_that_disagrees_with_the_version() -> None:
    """The local reminder about this drift has no reach in CI."""
    text = WORKFLOW.read_text()
    assert "github.ref_name" in text
    assert "pyproject.toml" in text
