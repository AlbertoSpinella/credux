"""Locating a shell that subprocess can actually launch, on any platform.

Three Windows traps, none of which exist on a POSIX host:

1. `shutil.which("bash")` searches PATH, but `subprocess.run(["bash", ...])`
   hands the bare name to CreateProcess, which searches System32 *before*
   PATH. Windows ships a WSL launcher there named `bash.exe`, so the bash a
   test probes is not the bash it runs, and on a machine with no WSL
   distribution the run dies with `execvpe(/bin/bash) failed`. Resolving to an
   absolute path is what keeps the two in agreement.
2. A `.sh` file cannot be executed directly: CreateProcess has no notion of a
   shebang and fails with `WinError 193`. Every script has to be passed to the
   interpreter as an argument.
3. PATH entries are joined with `os.pathsep`, which is `;` here. A `:` would
   be read as part of a drive letter.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
from pathlib import Path

# Where Git for Windows puts the bash that PATH usually does not reach.
_WINDOWS_FALLBACKS = (
    Path("C:/Program Files/Git/bin/bash.exe"),
    Path("C:/Program Files (x86)/Git/bin/bash.exe"),
)


def _runs(candidate: str) -> bool:
    """Whether this executable is a working shell rather than a broken stub."""
    try:
        return subprocess.run([candidate, "-c", "exit 0"], capture_output=True).returncode == 0
    except OSError:
        return False


@functools.lru_cache(maxsize=None)
def find_shell(name: str) -> str | None:
    """Absolute path to a working `name` shell, or None if there is not one."""
    candidates = []
    found = shutil.which(name)
    if found:
        candidates.append(found)
    if name == "bash":
        candidates.extend(str(path) for path in _WINDOWS_FALLBACKS if path.exists())
    for candidate in candidates:
        if _runs(candidate):
            return candidate
    return None


BASH = find_shell("bash")
ZSH = find_shell("zsh")
