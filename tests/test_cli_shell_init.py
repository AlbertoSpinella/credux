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
