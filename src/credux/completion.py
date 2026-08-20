"""Shell tab-completion: the completion scripts and the value completers.

Provider-neutral by construction: a completer offers profile names and
Identity Center session names out of `config.json` and never learns what
cloud any of them belongs to.
"""

from __future__ import annotations

from typing import Any, Iterable

import click
from click.shell_completion import get_completion_class

from .config import Config
from .errors import ConfigError
from .model import Registry

# Click derives both the environment variable it looks for and the function
# names in the emitted script from these, so they are part of the installed
# shell's contract: changing either invalidates every shell rc that already
# eval'd `credux shell-init`.
COMPLETE_VAR = "_CREDUX_COMPLETE"
PROGRAM_NAME = "credux"

HEREDOC_DELIMITER = "CREDUX_COMPLETION_SCRIPT"


def source(shell: str, command: click.Command) -> str:
    """Click's completion script for `shell`, or "" if Click has none.

    An unsupported shell is not an error: `shell-init --shell powershell` has
    a `pu` wrapper worth printing even though Click ships no PowerShell
    completion backend.
    """
    completion_class = get_completion_class(shell)
    if completion_class is None:
        return ""
    return completion_class(command, {}, PROGRAM_NAME, COMPLETE_VAR).source()


def _heredoc(name: str, shell: str, command: click.Command) -> str:
    """One shell script carried as data rather than as code.

    A quoted heredoc (`<<'DELIM'`) is neither expanded nor parsed by the
    surrounding shell, which is the only way to put zsh's script in front of
    bash: the two scripts are not mutually parseable, and bash's failure on
    zsh's `for type key descr in ...` happens at parse time, so no runtime
    `if` around it would help.
    """
    script = source(shell, command)
    delimiter = HEREDOC_DELIMITER
    if any(line.strip() == delimiter for line in script.splitlines()):
        # Unreachable with Click's own scripts, and deliberately fatal rather
        # than best-effort: a line equal to the delimiter closes the heredoc
        # early, and everything after it would be handed to the shell as
        # commands to run at startup.
        raise ConfigError(
            f"Refusing to emit the {shell} completion script: it contains a line equal to "
            f"the heredoc delimiter {delimiter!r}, which would make the rest of it execute"
        )
    return f"{name}=$(cat <<'{delimiter}'\n{script}\n{delimiter}\n)"


def posix_snippet(command: click.Command) -> str:
    """The completion block for the one wrapper that serves bash and zsh.

    `shellenv` collapses bash and zsh into a single "posix" shell because
    exporting AWS_PROFILE is identical in both. Completion cannot be
    collapsed the same way, so both scripts are emitted as inert data and the
    shell itself picks one at startup - correct even when $SHELL disagrees
    with the shell actually running the rc file.
    """
    return "\n".join(
        [
            "# Tab completion. Each script is carried as heredoc data and only the",
            "# matching one is eval'd: bash cannot parse zsh's script, or vice versa.",
            _heredoc("_credux_completion_zsh", "zsh", command),
            _heredoc("_credux_completion_bash", "bash", command),
            'if [ -n "${ZSH_VERSION-}" ]; then',
            '  eval "$_credux_completion_zsh"',
            'elif [ -n "${BASH_VERSION-}" ]; then',
            '  eval "$_credux_completion_bash"',
            "fi",
            "unset _credux_completion_zsh _credux_completion_bash",
        ]
    )


def _offer(names: Iterable[str], incomplete: str) -> list[str]:
    """Every name matching `incomplete`, the way the commands themselves match.

    Substring and case-insensitive, mirroring `Registry.search`, so
    completion never hides a name the command would have accepted.
    """
    needle = incomplete.casefold()
    return sorted(name for name in names if needle in name.casefold())


def _config() -> Config | None:
    """The loaded config, or None if it cannot be read.

    Completion runs while the user's cursor sits on a half-typed command
    line, and Click parses resiliently there, so no App has been built and
    the group callback has never run. Anything that goes wrong yields no
    completions: a traceback printed over the prompt line is worse than an
    empty list, and the command the user is typing will report the real
    problem a moment later anyway.
    """
    try:
        return Config.load()
    except Exception:  # noqa: BLE001 - see the docstring; nothing may escape
        return None


def complete_profiles(ctx: Any, param: Any, incomplete: str) -> list[str]:
    """Configured profile names matching `incomplete`."""
    config = _config()
    if config is None:
        return []
    try:
        names = [cred.profile for cred in Registry(config).all()]
    except Exception:  # noqa: BLE001 - a malformed credentials list, same rule
        return []
    return _offer(names, incomplete)


def complete_sso_sessions(ctx: Any, param: Any, incomplete: str) -> list[str]:
    """Configured Identity Center session names matching `incomplete`."""
    config = _config()
    if config is None:
        return []
    sessions = config.sso_sessions
    if not isinstance(sessions, dict):
        return []
    return _offer((str(name) for name in sessions), incomplete)
