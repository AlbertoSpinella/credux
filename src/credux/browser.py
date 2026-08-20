"""Launching a browser session isolated per profile."""

from __future__ import annotations

import hashlib
import subprocess
from typing import Any, Callable
from urllib.parse import quote

from .errors import UsageError

# The exact vocabularies Firefox's container API accepts. A value outside
# either list is rejected by the container extension, which refuses the whole
# link rather than falling back, so these are not suggestions.
CONTAINER_COLORS = (
    "blue",
    "turquoise",
    "green",
    "yellow",
    "orange",
    "red",
    "pink",
    "purple",
)
CONTAINER_ICONS = (
    "fingerprint",
    "briefcase",
    "dollar",
    "cart",
    "circle",
    "gift",
    "vacation",
    "food",
    "fruit",
    "pet",
    "tree",
    "chill",
)


def _profile_digest(profile: str) -> bytes:
    """A stable digest of a profile name, for deriving its container styling.

    blake2b, not the built-in hash(): hash() is salted per interpreter process
    unless PYTHONHASHSEED is set, so it would repaint every container on every
    run. The point of deriving these from the name at all is that an account
    keeps the same color and icon forever, on every machine, with nothing
    stored anywhere.
    """
    return hashlib.blake2b(profile.encode("utf-8"), digest_size=8).digest()


def container_color(profile: str) -> str:
    """The container color for a profile: derived, stable, never configured."""
    return CONTAINER_COLORS[_profile_digest(profile)[0] % len(CONTAINER_COLORS)]


def container_icon(profile: str) -> str:
    """The container icon for a profile. Independent of the color: a separate
    digest byte, so two profiles sharing a color rarely also share an icon."""
    return CONTAINER_ICONS[_profile_digest(profile)[1] % len(CONTAINER_ICONS)]


def open_container(
    profile: str,
    url: str,
    command: list[str],
    runner: Callable[[list[str]], Any] | None = None,
) -> list[str]:
    """Render the configured browser command and run it. Returns the argv used.

    Four placeholders are offered, in two pairs, because whether the value
    needs percent-encoding depends entirely on where the command puts it:

    - `{profile_encoded}` / `{url_encoded}` for a value embedded *inside
      another URL*, which is what the default Firefox container command does
      ("ext+container:name=...&url=..."). Both values can legitimately
      contain '+', '=' or '&', which would otherwise corrupt the container
      URL's own query string.
    - `{profile}` / `{url}` raw, for a value passed as a standalone argv
      element, which is what every non-container browser command does
      ("google-chrome --profile-directory=... <url>"). Encoding there is
      actively wrong: Chrome receives "https%3A%2F%2F..." and treats it as a
      search string rather than a URL.

    Two more placeholders, `{color}` and `{icon}`, carry the container styling
    derived from the profile name (see `container_color`). They are single
    lowercase words from a fixed vocabulary, so they need no encoded variants.
    A command that omits them leaves the styling to the container extension,
    which applies one fixed pair to everything it creates.
    """
    if runner is None:
        runner = subprocess.Popen
    if not command:
        raise UsageError(
            "settings.browser_command is empty; set it with "
            "'credux config set browser_command'"
        )
    argv = [
        part.format(
            profile=profile,
            url=url,
            profile_encoded=quote(profile, safe=""),
            url_encoded=quote(url, safe=""),
            color=container_color(profile),
            icon=container_icon(profile),
        )
        for part in command
    ]
    try:
        runner(argv)
    except FileNotFoundError as exc:
        raise UsageError(
            f"Could not launch {argv[0]!r}. Install it, or point "
            "settings.browser_command at the browser you use."
        ) from exc
    return argv
