"""Shell tab-completion: the completion scripts and the value completers.

Provider-neutral by construction: a completer offers profile names and
Identity Center session names out of `config.json` and never learns what
cloud any of them belongs to.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

import click
from click.shell_completion import (
    CompletionItem,
    ShellComplete,
    add_completion_class,
    get_completion_class,
    split_arg_string,
)

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

SHELL_DIR = Path(__file__).parent / "shell"


# Click ships no PowerShell backend, but the thing it is missing is only the
# *script*: the completion protocol itself is two environment variables and a
# line-based answer on stdout, and PowerShell can drive that as well as bash
# can. Registering a class rather than special-casing PowerShell in
# `shell-init` is what puts it on Click's own machinery: `source()` starts
# returning a script, and `credux` answers `_CREDUX_COMPLETE=powershell_complete`
# without a line of code outside this module.
#
# Fields are tab-separated and one completion is one line, because that is what
# PowerShell can split without a parser. `format_completion` is what guarantees
# neither character reaches the shell inside a field.
_FIELD_SEPARATOR = "\t"

_SOURCE_POWERSHELL = r"""# Tab completion for `%(prog_name)s`. Click generates no PowerShell script, so
# this one is credux's: it hands Click the words typed so far through the same
# environment variables the bash and zsh scripts use, and turns each line of
# the answer back into a completion.
#
# Windows PowerShell 5.1 does not consult a native completer while the token
# under the cursor is a bare "-" or "--", so `%(prog_name)s env --<TAB>`
# offers nothing while `%(prog_name)s env --s<TAB>` completes. That is the
# host, not this script; PowerShell 7 handles it.
Register-ArgumentCompleter -Native -CommandName %(prog_name)s -ScriptBlock {
  param($wordToComplete, $commandAst, $cursorPosition)

  # Click re-splits COMP_WORDS with shlex, so a word holding whitespace has to
  # go back quoted or it would arrive as two words.
  $words = @($commandAst.CommandElements | ForEach-Object {
    $text = $_.Extent.Text
    if ($text -match '\s') { "'" + $text.Replace("'", "'\''") + "'" } else { $text }
  })

  # With a trailing space the word being completed is not an AST element at
  # all, so the index Click should read sits one past the last one: there it
  # finds nothing and treats the incomplete word as empty.
  $cword = if ($wordToComplete) { $words.Count - 1 } else { $words.Count }

  $env:%(complete_var)s = 'powershell_complete'
  $env:COMP_WORDS = $words -join ' '
  $env:COMP_CWORD = $cword
  try {
    # Only stdout is read: credux writes its diagnostics to stderr, and one
    # offered as a candidate would be inserted into the command line on TAB.
    $response = @(%(prog_name)s 2>$null)
  } finally {
    # A completer that leaked these would turn the next
    # `%(prog_name)s` the user actually runs into a completion request.
    Remove-Item Env:\%(complete_var)s, Env:\COMP_WORDS, Env:\COMP_CWORD `
      -ErrorAction SilentlyContinue
  }

  foreach ($line in $response) {
    $fields = $line.Split("`t")
    if ($fields.Count -lt 2) { continue }
    $kind = $fields[0]
    $value = $fields[1]
    # "dir" and "file" mean Click is deferring to the shell's own path
    # completion, which PowerShell does for us when the completer returns
    # nothing at all.
    if ($kind -ne 'plain' -or -not $value) { continue }
    $help = if ($fields.Count -gt 2 -and $fields[2]) { $fields[2] } else { $value }
    # The inserted text is quoted when the value needs it; the listed text is
    # not, so the menu still shows the bare name.
    $insert = if ($value -match '\s') { "'" + $value.Replace("'", "''") + "'" } else { $value }
    [System.Management.Automation.CompletionResult]::new(
      $insert, $value, 'ParameterValue', $help)
  }
}
"""


def _one_line(text: str) -> str:
    """`text` with every character the answer format uses as structure removed.

    The PowerShell script splits stdout by line and then by tab, so a help
    string carrying either would shift every field after it. Click's own fish
    backend escapes the same two for the same reason.
    """
    for character in ("\t", "\r\n", "\r", "\n"):
        text = text.replace(character, " ")
    return text


class PowerShellComplete(ShellComplete):
    """Completion for PowerShell, the backend Click itself does not ship."""

    name = "powershell"
    source_template = _SOURCE_POWERSHELL

    def get_completion_args(self) -> tuple[list[str], str]:
        """The words typed so far, out of the variables the script exports.

        Identical to Click's bash and zsh backends by construction: the point
        of the script above is to speak the protocol they already speak.
        """
        cwords = split_arg_string(os.environ["COMP_WORDS"])
        cword = int(os.environ["COMP_CWORD"])
        args = cwords[1:cword]
        try:
            incomplete = cwords[cword]
        except IndexError:
            incomplete = ""
        return args, incomplete

    def format_completion(self, item: CompletionItem) -> str:
        separator = _FIELD_SEPARATOR
        return separator.join(
            (item.type, _one_line(item.value), _one_line(item.help or ""))
        )


add_completion_class(PowerShellComplete, "powershell")


def source(shell: str, command: click.Command) -> str:
    """The completion script for `shell`, or "" if there is no backend for it.

    An unsupported shell is not an error: `shell-init --shell cmd` would have
    a `cdx` wrapper worth printing even with no completion to go with it.
    """
    completion_class = get_completion_class(shell)
    if completion_class is None:
        return ""
    return completion_class(command, {}, PROGRAM_NAME, COMPLETE_VAR).source()


def cdx_completion(shell: str) -> str:
    """The completer for the `cdx` shell function, for bash or zsh.

    Click's script registers itself against the `credux` command alone, so
    `cdx` - a shell function the CLI never sees - needs a completer of its own.
    It lives here rather than in `credux.sh` so that the wrapper file stays
    parseable by any POSIX shell: these two bodies are bash- and zsh-only.
    """
    return (SHELL_DIR / f"cdx.{shell}").read_text(encoding="utf-8")


def _heredoc(name: str, shell: str, command: click.Command) -> str:
    """One shell script carried as data rather than as code.

    A quoted heredoc (`<<'DELIM'`) is neither expanded nor parsed by the
    surrounding shell, which is the only way to put zsh's script in front of
    bash: the two scripts are not mutually parseable, and bash's failure on
    zsh's `for type key descr in ...` happens at parse time, so no runtime
    `if` around it would help.

    The `cdx` completer rides along in the same heredoc: it has the same
    parseability problem and the same one of the two shells to be eval'd in.
    """
    script = source(shell, command) + cdx_completion(shell)
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
