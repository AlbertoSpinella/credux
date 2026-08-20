"""On-disk Credux configuration. Holds metadata only, never secret material."""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigError

CONFIG_VERSION = 1
CONFIG_FILENAME = "config.json"

# Everything `to_dict` writes at the top level of config.json except
# `settings`. `credux config set` only ever writes under `settings`, so naming
# one of these is a mistake rather than a nested setting.
TOP_LEVEL_CONFIG_KEYS = frozenset(
    {"version", "naming", "sso_sessions", "credentials", "integrations", "post_sync"}
)

DEFAULT_NAMING: dict[str, str] = {
    "iam_user": "{org}-{account_name}-User-{user}",
    "iam_role": "{org}-{account_name}-Role-{role}",
    "iam_sso": "{org}-{account_name}-SSO-{role}",
    "management_account_prefix": "management_",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "aws_credentials_file": "~/.aws/credentials",
    "aws_config_file": "~/.aws/config",
    "default_profile": {
        "enabled": True,
        "access_key_id": "test",
        "secret_access_key": "test",
    },
    # The container URL embeds both values inside its own query string, so
    # this default uses the encoded placeholders. A command that passes the
    # URL as a standalone argument wants the raw {profile}/{url} pair
    # instead; see browser.open_container.
    "browser_command": [
        "firefox",
        "ext+container:name={profile_encoded}&url={url_encoded}"
        "&color={color}&icon={icon}",
    ],
    "refresh_threshold_minutes": 10,
    "console_destination": "https://console.aws.amazon.com/",
}


def credux_home() -> Path:
    """Credux's own directory: CREDUX_HOME if set, otherwise ~/.credux."""
    raw = os.environ.get("CREDUX_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".credux"


def _merge_defaults(value: Any, defaults: Any) -> Any:
    """Fill keys absent from value using defaults, recursing into dicts."""
    if not isinstance(value, dict) or not isinstance(defaults, dict):
        return value
    merged = copy.deepcopy(defaults)
    for key, item in value.items():
        merged[key] = _merge_defaults(item, defaults.get(key))
    return merged


@dataclass
class Config:
    """The parsed config.json, with defaults already applied."""

    version: int = CONFIG_VERSION
    settings: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULT_SETTINGS))
    naming: dict[str, str] = field(default_factory=lambda: copy.deepcopy(DEFAULT_NAMING))
    sso_sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    credentials: list[dict[str, Any]] = field(default_factory=list)
    integrations: list[dict[str, Any]] = field(default_factory=list)
    post_sync: list[str] = field(default_factory=list)
    path: Path = field(default_factory=lambda: credux_home() / CONFIG_FILENAME)

    @classmethod
    def load(cls, home: Path | None = None) -> "Config":
        path = (home or credux_home()) / CONFIG_FILENAME
        if not path.exists():
            return cls(path=path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"{path} must contain a JSON object")
        version = raw.get("version", CONFIG_VERSION)
        # config.json is hand-editable, so `version` is whatever the file
        # says. Comparing a string (or a list, or null) against an int raises
        # TypeError, which is not a CreduxError and escapes as a traceback.
        # bool is excluded explicitly: it is a subclass of int, and
        # "version": true is not a version.
        if isinstance(version, bool) or not isinstance(version, int):
            raise ConfigError(
                f"{path} has a non-numeric 'version' field ({version!r}); "
                f"it must be a whole number (the current version is {CONFIG_VERSION})"
            )
        if version > CONFIG_VERSION:
            raise ConfigError(
                f"{path} was written by a newer Credux (version {version}); upgrade credux"
            )
        return cls(
            version=CONFIG_VERSION,
            settings=_merge_defaults(raw.get("settings", {}), DEFAULT_SETTINGS),
            naming=_merge_defaults(raw.get("naming", {}), DEFAULT_NAMING),
            sso_sessions=raw.get("sso_sessions", {}),
            credentials=raw.get("credentials", []),
            integrations=raw.get("integrations", []),
            post_sync=raw.get("post_sync", []),
            path=path,
        )

    @property
    def uses_default_naming(self) -> bool:
        """Whether every naming template is still the one credux ships.

        The profile names credux writes are an opinion - one organization's
        house style baked into a default - and a user who never opened
        config.json has not chosen it. This is what lets the CLI say so
        once, and stop saying it as soon as they have.

        Comparing values, not asking whether config.json carried a `naming`
        block: `to_dict` writes the whole block on every save, so the block
        is present from the first `credux add` onwards regardless.
        """
        return self.naming == DEFAULT_NAMING

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "settings": self.settings,
            "naming": self.naming,
            "sso_sessions": self.sso_sessions,
            "credentials": self.credentials,
            "integrations": self.integrations,
            "post_sync": self.post_sync,
        }

    def save(self) -> None:
        from .fsutil import atomic_write_text

        atomic_write_text(self.path, json.dumps(self.to_dict(), indent=2) + "\n")

    def setting(self, dotted: str, default: Any = None) -> Any:
        """Read settings by dotted path, e.g. 'default_profile.enabled'."""
        node: Any = self.settings
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node
