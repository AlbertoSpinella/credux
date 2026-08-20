"""Credential records and the registry that owns them.

A credential is provider-neutral metadata. Everything cloud-specific lives in
`params`, which only that credential's provider interprets.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .errors import ConfigError, UsageError


def new_id() -> str:
    """An identifier that never changes, even when the profile is renamed."""
    return uuid.uuid4().hex[:12]


@dataclass
class Credential:
    id: str
    kind: str
    profile: str
    provider: str = "aws"
    region: str | None = None
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "profile": self.profile,
            "provider": self.provider,
            "region": self.region,
            "params": copy.deepcopy(self.params),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Credential":
        try:
            return cls(
                id=data["id"],
                kind=data["kind"],
                profile=data["profile"],
                provider=data.get("provider", "aws"),
                region=data.get("region"),
                params=copy.deepcopy(data.get("params", {})),
            )
        except KeyError as exc:
            raise ConfigError(f"Credential entry is missing {exc.args[0]!r}: {data}") from exc


class Registry:
    """Read/write view over the credential list inside a Config."""

    def __init__(self, config: Config) -> None:
        self.config = config

    def all(self) -> list[Credential]:
        return [Credential.from_dict(entry) for entry in self.config.credentials]

    def add(self, cred: Credential) -> None:
        if self.by_profile(cred.profile) is not None:
            raise UsageError(f"A credential named {cred.profile!r} already exists")
        if any(entry.get("id") == cred.id for entry in self.config.credentials):
            raise UsageError(f"A credential with id {cred.id!r} already exists")
        self.config.credentials.append(cred.to_dict())

    def replace(self, cred: Credential) -> None:
        colliding = self.by_profile(cred.profile)
        if colliding is not None and colliding.id != cred.id:
            raise UsageError(f"A credential named {cred.profile!r} already exists")
        for index, entry in enumerate(self.config.credentials):
            if entry.get("id") == cred.id:
                self.config.credentials[index] = cred.to_dict()
                return
        raise UsageError(f"No credential with id {cred.id!r}")

    def remove(self, cred_id: str) -> None:
        before = len(self.config.credentials)
        self.config.credentials = [
            entry for entry in self.config.credentials if entry.get("id") != cred_id
        ]
        if len(self.config.credentials) == before:
            raise UsageError(f"No credential with id {cred_id!r}")

    def by_id(self, cred_id: str) -> Credential:
        for cred in self.all():
            if cred.id == cred_id:
                return cred
        raise UsageError(f"No credential with id {cred_id!r}")

    def by_profile(self, profile: str) -> Credential | None:
        for cred in self.all():
            if cred.profile == profile:
                return cred
        return None

    def search(self, term: str) -> list[Credential]:
        """Every credential whose profile matches `term`, best match first.

        An exact profile name is never treated as a search term: it returns
        just that credential, so a name that happens to be a substring of a
        longer one (`acme-dev` next to `acme-dev-User-ada`) stays usable
        instead of becoming permanently ambiguous. Otherwise the term is
        matched case-insensitively anywhere in the profile name, and the
        result is sorted so the same term always offers the same order.
        """
        exact = self.by_profile(term)
        if exact is not None:
            return [exact]
        needle = term.casefold()
        matches = [cred for cred in self.all() if needle in cred.profile.casefold()]
        return sorted(matches, key=lambda cred: cred.profile)

    def resolve(self, name: str) -> Credential:
        """The one credential matching `name`, or a UsageError explaining why not.

        This is the non-interactive form: callers that can ask a human which
        of several matches was meant should use `search` and choose.
        """
        matches = self.search(name)
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise UsageError(
                f"No credential matches {name!r}. Run 'credux list' to see what exists."
            )
        names = ", ".join(cred.profile for cred in matches)
        raise UsageError(f"{name!r} is ambiguous; it matches: {names}")

    def save(self) -> None:
        self.config.save()
