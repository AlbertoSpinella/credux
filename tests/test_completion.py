"""Shell tab-completion: the emitted scripts and the value completers."""

from __future__ import annotations

import json

import pytest
from click.shell_completion import ShellComplete

from credux import completion
from credux.cli import cli
from credux.errors import ConfigError


def write_config(credux_home, credentials, sso_sessions=None) -> None:
    (credux_home / "config.json").write_text(
        json.dumps(
            {
                "version": 1,
                "credentials": credentials,
                "sso_sessions": sso_sessions or {},
            }
        ),
        encoding="utf-8",
    )


def credential(profile: str) -> dict:
    return {
        "id": profile,
        "kind": "iam-user",
        "provider": "aws",
        "profile": profile,
        "params": {},
    }


def complete(args: list[str], incomplete: str) -> list[str]:
    """What the shell would be offered for `credux <args> <incomplete>`.

    Goes through Click's own completion machinery rather than calling a
    completer directly, so the test fails if a completer exists but is not
    attached to the parameter it is meant to complete.
    """
    engine = ShellComplete(cli, {}, "credux", completion.COMPLETE_VAR)
    return [item.value for item in engine.get_completions(args, incomplete)]


# --- the completion scripts -------------------------------------------------


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_source_is_clicks_script_for_a_supported_shell(shell):
    script = completion.source(shell, cli)

    assert completion.COMPLETE_VAR in script
    assert "credux" in script


@pytest.mark.parametrize("shell", ["powershell", "cmd"])
def test_source_is_empty_for_a_shell_click_cannot_complete(shell):
    """Click ships no completion backend for these. An empty string, not a
    crash: `credux shell-init --shell powershell` must still print its
    wrapper."""
    assert completion.source(shell, cli) == ""


def test_the_posix_snippet_carries_both_scripts_without_either_being_code():
    """The zsh and bash scripts are mutually unparseable, so each must reach
    the shell as heredoc data and only the matching one may be eval'd."""
    snippet = completion.posix_snippet(cli)

    assert "ZSH_VERSION" in snippet
    assert "BASH_VERSION" in snippet
    # zsh's script, verbatim, is present but never on a line the shell parses.
    assert "for type key descr in" in snippet


def test_a_script_containing_the_heredoc_delimiter_is_refused(monkeypatch):
    """A generated script with a line equal to the delimiter would close the
    heredoc early and hand the remainder to the shell as commands. Refuse to
    emit that rather than emit something that executes."""
    monkeypatch.setattr(
        completion,
        "source",
        lambda shell, command: f"echo one\n{completion.HEREDOC_DELIMITER}\nrm -rf /\n",
    )

    with pytest.raises(ConfigError) as excinfo:
        completion.posix_snippet(cli)

    assert completion.HEREDOC_DELIMITER in str(excinfo.value)


# --- the value completers ---------------------------------------------------


def test_subcommands_complete(credux_home):
    assert "list" in complete([], "li")


def test_start_completes_configured_profile_names(credux_home):
    write_config(credux_home, [credential("acme-billing-User-ada"), credential("acme-dev-User-bob")])

    assert complete(["start"], "acme-b") == ["acme-billing-User-ada"]


@pytest.mark.parametrize("command", ["start", "stop", "env", "console", "remove"])
def test_every_command_taking_a_profile_completes_profile_names(credux_home, command):
    write_config(credux_home, [credential("acme-billing-User-ada")])

    assert complete([command], "acme") == ["acme-billing-User-ada"]


def test_profile_completion_matches_a_substring_not_just_a_prefix(credux_home):
    """`_find` accepts any substring via Registry.search, so completion must
    offer the same set - otherwise it hides names the command would accept."""
    write_config(credux_home, [credential("acme-billing-User-ada")])

    assert complete(["start"], "billing") == ["acme-billing-User-ada"]


def test_profile_completion_is_case_insensitive(credux_home):
    write_config(credux_home, [credential("acme-billing-User-ada")])

    assert complete(["start"], "BILLING") == ["acme-billing-User-ada"]


def test_login_completes_configured_session_names(credux_home):
    write_config(credux_home, [], sso_sessions={"acme": {}, "other": {}})

    assert complete(["login"], "ac") == ["acme"]


def test_sync_completes_configured_session_names(credux_home):
    write_config(credux_home, [], sso_sessions={"acme": {}})

    assert complete(["sync"], "") == ["acme"]


def test_completion_offers_nothing_when_the_config_is_unreadable(credux_home):
    """A traceback here would be printed over the user's prompt line at the
    moment they pressed TAB, so a broken config yields no completions rather
    than an error."""
    (credux_home / "config.json").write_text("{not json", encoding="utf-8")

    assert complete(["start"], "acme") == []


def test_completion_offers_nothing_when_there_is_no_config_at_all(credux_home):
    assert complete(["start"], "acme") == []
