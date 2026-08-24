import os
import shutil
import subprocess

import pytest
from click.testing import CliRunner

from credux.cli import cli


def wrapper_source(shell: str) -> str:
    result = CliRunner().invoke(cli, ["shell-init", "--shell", shell])
    assert result.exit_code == 0, result.output
    return result.output


def stub_credux(directory, exit_code: int, stdout: str = "") -> None:
    """Put a fake `credux` on PATH so the wrapper can be run for real.

    The wrapper shells out to whatever `credux` resolves to, so this is the
    only way to exercise it without the real CLI (and its config, keychain
    and AWS files) being involved.
    """
    script = directory / "credux"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s" {stdout!r}\n'
        'echo "credux: boom" >&2\n'
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    os.chmod(script, 0o755)


def run_pu(tmp_path, argument: str | None) -> subprocess.CompletedProcess:
    script = tmp_path / "run.sh"
    invocation = "pu" if argument is None else f"pu {argument}"
    script.write_text(f"{wrapper_source('posix')}\n{invocation}\n", encoding="utf-8")
    return subprocess.run(
        ["bash", str(script)],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash to run the wrapper")
def test_the_posix_wrapper_propagates_a_credux_failure(credux_home, tmp_path):
    """`eval "$(credux env ...)"` reports eval's status, not credux's, so
    `pu badprofile` used to exit 0 and silently do nothing."""
    stub_credux(tmp_path, exit_code=2)

    result = run_pu(tmp_path, "badprofile")

    assert result.returncode == 2
    assert "credux: boom" in result.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash to run the wrapper")
def test_the_posix_wrapper_propagates_a_failure_with_no_argument(credux_home, tmp_path):
    stub_credux(tmp_path, exit_code=3)

    result = run_pu(tmp_path, None)

    assert result.returncode == 3


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash to run the wrapper")
def test_the_posix_wrapper_evaluates_the_statement_on_success(credux_home, tmp_path):
    stub_credux(tmp_path, exit_code=0, stdout='export AWS_PROFILE="acme-dev"\n')
    script = tmp_path / "run.sh"
    script.write_text(
        f'{wrapper_source("posix")}\npu acme-dev\necho "got:$AWS_PROFILE"\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(script)],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "got:acme-dev" in result.stdout


def test_the_fish_wrapper_checks_the_status_before_sourcing(credux_home):
    """No fish interpreter here, so this pins the shape rather than the
    behaviour: the capture must be tested before anything is sourced."""
    source = wrapper_source("fish")

    assert "$status" in source
    assert "return $credux_status" in source
    # rindex, not index: the header comment shows the install line, which
    # also ends in "| source".
    assert source.index("if test $credux_status -ne 0") < source.rindex("| source")


def test_the_powershell_wrapper_avoids_the_automatic_profile_variable(credux_home):
    """$Profile is a PowerShell automatic variable (the path to the current
    profile script); declaring a parameter with that name shadows it."""
    source = wrapper_source("powershell")

    assert "param([string]$Profile)" not in source
    assert "param([string]$Name)" in source
    assert "$Name" in source.split("param([string]$Name)", 1)[1]


def test_the_powershell_wrapper_checks_the_exit_code_before_invoking(credux_home):
    source = wrapper_source("powershell")

    assert "$LASTEXITCODE" in source
    assert source.index("if ($LASTEXITCODE -ne 0)") < source.index("Invoke-Expression\n}")


def test_shell_init_prints_a_posix_function(credux_home, monkeypatch):
    monkeypatch.setenv("CREDUX_SHELL", "posix")

    result = CliRunner().invoke(cli, ["shell-init"])

    assert result.exit_code == 0, result.output
    assert "pu()" in result.output
    assert "credux env" in result.output


def test_shell_init_supports_powershell(credux_home):
    result = CliRunner().invoke(cli, ["shell-init", "--shell", "powershell"])

    assert "function pu" in result.output


def test_shell_init_supports_fish(credux_home):
    result = CliRunner().invoke(cli, ["shell-init", "--shell", "fish"])

    assert "function pu" in result.output


def test_shell_init_rejects_an_unknown_shell(credux_home):
    result = CliRunner().invoke(cli, ["shell-init", "--shell", "cmd"])

    assert result.exit_code == 2
    assert "cmd" in result.output


# --- tab completion ---------------------------------------------------------
#
# The posix wrapper is one file for both bash and zsh, but their completion
# scripts are not interchangeable and are not even mutually parseable: bash
# cannot parse zsh's `for type key descr in ...`, and the failure is at parse
# time, so guarding it with `if [ -n "$ZSH_VERSION" ]` inside a compound
# command would not help - bash parses the whole `if ... fi` before running
# any of it. The two parseability tests below are what keeps that from
# regressing; neither can be replaced by an assertion on the text.


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_posix_shell_init_output_parses_under_bash(credux_home, tmp_path):
    script = tmp_path / "init.sh"
    script.write_text(wrapper_source("posix"), encoding="utf-8")

    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("zsh") is None, reason="needs zsh")
def test_the_posix_shell_init_output_parses_under_zsh(credux_home, tmp_path):
    script = tmp_path / "init.zsh"
    script.write_text(wrapper_source("posix"), encoding="utf-8")

    result = subprocess.run(["zsh", "-n", str(script)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_bash_registers_the_completion_and_not_zshs(credux_home, tmp_path):
    """Registration, not just parseability: `complete -p credux` is bash's own
    record of what it will call on TAB."""
    script = tmp_path / "init.sh"
    script.write_text(
        f"{wrapper_source('posix')}\ncomplete -p credux\n",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(script)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "_credux_completion" in result.stdout
    # The zsh script defines a same-named function; if bash had eval'd that
    # one instead, `complete -p` would still name it. What distinguishes the
    # two is that only bash's script calls `complete` at all, so a successful
    # `complete -p` here means bash's own script ran.
    assert "-F _credux_completion credux" in result.stdout


@pytest.mark.skipif(shutil.which("zsh") is None, reason="needs zsh")
def test_zsh_registers_the_completion_and_not_bashs(credux_home, tmp_path):
    script = tmp_path / "init.zsh"
    script.write_text(
        f"autoload -Uz compinit && compinit -u -d {tmp_path}/zcompdump\n"
        f"{wrapper_source('posix')}\n"
        "print -r -- \"registered:${_comps[credux]}\"\n",
        encoding="utf-8",
    )

    result = subprocess.run(["zsh", str(script)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "registered:_credux_completion" in result.stdout


def test_the_fish_shell_init_output_includes_the_fish_completion(credux_home):
    source = wrapper_source("fish")

    assert "function pu" in source
    assert "complete --no-files --command credux" in source


def test_the_powershell_shell_init_output_is_wrapper_only(credux_home):
    """Click has no PowerShell completion backend, so shell-init must print
    the wrapper alone rather than an empty or half-written block."""
    source = wrapper_source("powershell")

    assert "function pu" in source
    assert "_CREDUX_COMPLETE" not in source


# --- tab completion for `pu` ------------------------------------------------
#
# `pu` is a shell function, so Click's generated script - which registers
# itself against the `credux` command alone - never completes it. Each
# wrapper registers its own completer, which asks `credux completions
# profiles` for the candidates so `pu <TAB>` offers exactly what
# `credux env <TAB>` offers.


def stub_completions(directory, names) -> None:
    """A fake `credux` that answers `completions profiles` and records its argv.

    Also writes to stderr, because the completer must discard that: a
    diagnostic offered as a completion candidate would be inserted into the
    user's command line.
    """
    script = directory / "credux"
    body = "".join(f'printf "%s\\n" {name!r}\n' for name in names)
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > {directory}/argv\n'
        'echo "credux: boom" >&2\n'
        f"{body}"
        "exit 0\n",
        encoding="utf-8",
    )
    os.chmod(script, 0o755)


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_bash_registers_a_completer_for_pu(credux_home, tmp_path):
    script = tmp_path / "init.sh"
    script.write_text(f"{wrapper_source('posix')}\ncomplete -p pu\n", encoding="utf-8")

    result = subprocess.run(["bash", str(script)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "-F _credux_pu_completion_bash pu" in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_bash_pu_completer_offers_the_names_credux_prints(credux_home, tmp_path):
    stub_completions(tmp_path, ["acme-billing-User-ada", "beta-billing-User-bob"])
    script = tmp_path / "init.sh"
    script.write_text(
        f"{wrapper_source('posix')}\n"
        "COMP_WORDS=(pu billing)\n"
        "COMP_CWORD=1\n"
        "_credux_pu_completion_bash\n"
        'printf "reply:%s\\n" "${COMPREPLY[@]}"\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(script)],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "reply:acme-billing-User-ada" in result.stdout
    assert "reply:beta-billing-User-bob" in result.stdout
    # The completer redirects stderr away; a candidate made of credux's own
    # diagnostics would be inserted into the command line on TAB.
    assert "boom" not in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_bash_pu_completer_forwards_the_word_being_typed(credux_home, tmp_path):
    """Without the current word, `pu mana<TAB>` would offer every profile."""
    stub_completions(tmp_path, ["acme-billing-User-ada"])
    script = tmp_path / "init.sh"
    script.write_text(
        f"{wrapper_source('posix')}\n"
        "COMP_WORDS=(pu billing)\n"
        "COMP_CWORD=1\n"
        "_credux_pu_completion_bash\n",
        encoding="utf-8",
    )

    subprocess.run(
        ["bash", str(script)],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
    )

    argv = (tmp_path / "argv").read_text(encoding="utf-8").split()
    assert argv == ["completions", "profiles", "billing"]


@pytest.mark.skipif(shutil.which("zsh") is None, reason="needs zsh")
def test_zsh_registers_a_completer_for_pu(credux_home, tmp_path):
    script = tmp_path / "init.zsh"
    script.write_text(
        f"autoload -Uz compinit && compinit -u -d {tmp_path}/zcompdump\n"
        f"{wrapper_source('posix')}\n"
        'print -r -- "registered:${_comps[pu]}"\n',
        encoding="utf-8",
    )

    result = subprocess.run(["zsh", str(script)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "registered:_credux_pu_completion_zsh" in result.stdout


def test_the_zsh_pu_completer_does_not_filter_out_substring_matches(credux_home):
    """`compadd -U` is what makes `pu mana<TAB>` able to offer
    `prod-management`: without it zsh drops every candidate that does not
    start with the word already typed, and the substring matching that
    `credux env` does would be invisible."""
    source = wrapper_source("posix")
    # Split on the closing brace on its own line: the body itself contains
    # braces, in "${words[CURRENT]}".
    body = source.split("_credux_pu_completion_zsh()", 1)[1].split("\n}\n", 1)[0]

    assert "compadd -U" in body


def test_the_fish_wrapper_completes_pu_from_credux(credux_home):
    source = wrapper_source("fish")

    assert "--command pu" in source
    assert "credux completions profiles" in source


def test_the_powershell_wrapper_completes_pu_from_credux(credux_home):
    source = wrapper_source("powershell")

    assert "Register-ArgumentCompleter -CommandName pu -ParameterName Name" in source
    assert "credux completions profiles" in source


def test_the_zsh_pu_completer_keeps_the_typed_word_when_several_names_match(credux_home):
    """`compadd -U` also makes zsh replace the typed word with the matches'
    common prefix, and for substring matches that is usually empty: on
    `pu mana<TAB>` zsh would erase "mana" and offer nothing at all. Cycling
    through the matches instead keeps the line intact until one is chosen."""
    source = wrapper_source("posix")
    body = source.split("_credux_pu_completion_zsh()", 1)[1].split("\n}\n", 1)[0]

    assert "compstate[insert]=menu" in body
