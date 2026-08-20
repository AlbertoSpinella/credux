"""Rendering the statement that puts AWS_PROFILE into the calling shell.

A child process cannot mutate its parent's environment, so Credux prints a
statement for the shell to evaluate. That is an OS constraint, not a choice.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import PurePath
from typing import Mapping

from .errors import UsageError

SHELLS = ("posix", "fish", "powershell", "cmd")
VARIABLE = "AWS_PROFILE"

# Whitelist, not blacklist, and one whitelist for all four shells. posix, fish
# and powershell interpolate the name inside double quotes, where only
# quote/backtick/dollar/backslash/newline can break out; cmd's "set VAR=value"
# is unquoted, so & | % ! ^ < > are live there too. A blacklist tuned to the
# first three would miss cmd entirely, and applying it per-shell would make a
# name's validity depend on the platform rendering it.
#
# The admitted set is the AWS IAM character set ("+=,.@_-", which Identity
# Center permission-set names share) plus ASCII alphanumerics: a profile name
# derived from a legitimate IAM principal must not be rejected by credux's own
# validator. All of those are inert in double quotes, and cmd's unquoted "set"
# takes everything after the first "=" literally, so they survive there too.
_ALLOWED_PROFILE_CHARS = re.compile(r"^[A-Za-z0-9._+=,@-]+$")


def detect_shell(env: Mapping[str, str] | None = None, platform: str | None = None) -> str:
    env = os.environ if env is None else env
    platform = platform or sys.platform

    override = env.get("CREDUX_SHELL")
    if override:
        return override

    shell = env.get("SHELL")
    if shell:
        name = PurePath(shell).name.lower()
        if name == "fish":
            return "fish"
        return "posix"

    if platform.startswith("win"):
        return "powershell" if env.get("PSModulePath") else "cmd"
    return "posix"


def _validate(profile: str) -> str:
    if not profile or not _ALLOWED_PROFILE_CHARS.fullmatch(profile):
        raise UsageError(
            f"Profile name {profile!r} contains characters that cannot be placed in a "
            "shell statement safely; rename it or adjust the naming template"
        )
    return profile


def env_statement(profile: str | None, shell: str, unset: bool = False) -> str:
    if shell not in SHELLS:
        raise UsageError(f"Unsupported shell {shell!r}; expected one of {', '.join(SHELLS)}")

    if unset:
        return {
            "posix": f"unset {VARIABLE}",
            "fish": f"set -e {VARIABLE}",
            "powershell": f"Remove-Item Env:\\{VARIABLE} -ErrorAction SilentlyContinue",
            "cmd": f"set {VARIABLE}=",
        }[shell]

    name = _validate(profile or "")
    return {
        "posix": f'export {VARIABLE}="{name}"',
        "fish": f'set -gx {VARIABLE} "{name}"',
        "powershell": f'$env:{VARIABLE} = "{name}"',
        "cmd": f"set {VARIABLE}={name}",
    }[shell]
