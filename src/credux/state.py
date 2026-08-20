"""Which credentials are materialized right now, and when they go stale."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import credux_home
from .errors import ConfigError
from .fsutil import atomic_write_text

STATE_FILENAME = "state.json"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ActiveSession:
    profile: str
    credential_id: str
    kind: str
    expires_at: str | None = None
    region: str | None = None

    def expiry(self) -> datetime | None:
        if not self.expires_at:
            return None
        try:
            parsed = datetime.fromisoformat(self.expires_at)
        except ValueError as exc:
            raise ConfigError(
                f"Session {self.profile!r} has an unparseable expires_at "
                f"{self.expires_at!r}: {exc}"
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def expires_in(self, now: datetime | None = None) -> timedelta | None:
        expiry = self.expiry()
        if expiry is None:
            return None
        return expiry - (now or utcnow())

    def is_expiring(self, threshold_minutes: int, now: datetime | None = None) -> bool:
        remaining = self.expires_in(now)
        if remaining is None:
            return False
        return remaining <= timedelta(minutes=threshold_minutes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "credential_id": self.credential_id,
            "kind": self.kind,
            "expires_at": self.expires_at,
            "region": self.region,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ActiveSession":
        try:
            return cls(
                profile=data["profile"],
                credential_id=data["credential_id"],
                kind=data["kind"],
                expires_at=data.get("expires_at"),
                region=data.get("region"),
            )
        except KeyError as exc:
            raise ConfigError(f"Active session entry is missing {exc.args[0]!r}: {data}") from exc
        except TypeError as exc:
            raise ConfigError(f"Active session entry is malformed: {data!r} ({exc})") from exc


class State:
    """The set of sessions currently written into the credentials file."""

    def __init__(self, path: Path, sessions: dict[str, ActiveSession]) -> None:
        self.path = path
        self._sessions = sessions

    @classmethod
    def load(cls, home: Path | None = None) -> "State":
        path = (home or credux_home()) / STATE_FILENAME
        if not path.exists():
            return cls(path, {})
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"{path} must contain a JSON object")
        active = raw.get("active", [])
        if not isinstance(active, list):
            raise ConfigError(f"{path} field 'active' must be a list")
        sessions: dict[str, ActiveSession] = {}
        for entry in active:
            session = ActiveSession.from_dict(entry)
            sessions[session.profile] = session
        return cls(path, sessions)

    def set(self, session: ActiveSession) -> None:
        self._sessions[session.profile] = session

    def clear(self, profile: str) -> None:
        self._sessions.pop(profile, None)

    def clear_all(self) -> None:
        self._sessions.clear()

    def get(self, profile: str) -> ActiveSession | None:
        return self._sessions.get(profile)

    def all(self) -> list[ActiveSession]:
        return list(self._sessions.values())

    def save(self) -> None:
        payload = {"active": [item.to_dict() for item in self.all()]}
        atomic_write_text(self.path, json.dumps(payload, indent=2) + "\n")
