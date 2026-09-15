"""Provider interface, credential resolution, and the provider registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from ..config import Config
from ..errors import ResolutionError
from ..model import Credential, Registry


@dataclass
class ResolvedCredentials:
    """Usable credential material for exactly one profile."""

    access_key_id: str
    secret_access_key: str
    session_token: str | None = None
    expiration: datetime | None = None
    region: str | None = None

    def as_boto_kwargs(self) -> dict[str, str]:
        kwargs = {
            "aws_access_key_id": self.access_key_id,
            "aws_secret_access_key": self.secret_access_key,
        }
        if self.session_token:
            kwargs["aws_session_token"] = self.session_token
        return kwargs


def from_sts_payload(payload: dict[str, Any], region: str | None = None) -> ResolvedCredentials:
    """Map an STS or SSO credentials payload onto ResolvedCredentials."""
    return ResolvedCredentials(
        access_key_id=payload["AccessKeyId"],
        secret_access_key=payload["SecretAccessKey"],
        session_token=payload.get("SessionToken"),
        expiration=payload.get("Expiration"),
        region=region,
    )


class Provider(ABC):
    """Everything one cloud needs to turn a credential into material."""

    name: str = ""
    kinds: tuple[str, ...] = ()

    @abstractmethod
    def resolve(self, cred: Credential, ctx: "ResolutionContext") -> ResolvedCredentials:
        """Produce usable credentials for this credential record."""

    def console_url(
        self, cred: Credential, ctx: "ResolutionContext", resolved: ResolvedCredentials
    ) -> str:
        raise ResolutionError(f"Provider {self.name!r} cannot open a web console")


_PROVIDERS: dict[str, Provider] = {}


def register(provider: Provider) -> None:
    _PROVIDERS[provider.name] = provider


def get_provider(name: str) -> Provider:
    provider = _PROVIDERS.get(name)
    if provider is None:
        known = ", ".join(sorted(_PROVIDERS)) or "none"
        raise ResolutionError(f"Unknown provider {name!r}; registered providers: {known}")
    return provider


def provider_for(cred: Credential) -> Provider:
    return get_provider(cred.provider)


def default_client_factory(
    service: str, region: str | None = None, credentials: ResolvedCredentials | None = None
) -> Any:
    """Build a boto3 client. Injected so tests can hand back a stubbed client."""
    import boto3
    import botocore.session

    kwargs: dict[str, Any] = {}
    if region:
        kwargs["region_name"] = region
    if credentials is not None:
        kwargs.update(credentials.as_boto_kwargs())
    # credux writes profile names, it never reads them: `credux env` exports
    # AWS_PROFILE for the user's shell, and boto3's default session would read
    # that export back in here, so a stale or not-yet-written profile would
    # break the very command that writes it. Dropping the env lookup for the
    # profile variable leaves every other AWS setting alone.
    botocore_session = botocore.session.Session(
        session_vars={"profile": (None, None, None, None)}
    )
    return boto3.session.Session(botocore_session=botocore_session).client(service, **kwargs)


class ResolutionContext:
    """Carries everything resolution needs, and guards against chain cycles."""

    def __init__(
        self,
        config: Config,
        registry: Registry,
        client_factory: Callable[..., Any] | None = None,
        prompt_mfa: Callable[[str], str] | None = None,
    ) -> None:
        if client_factory is None:
            client_factory = default_client_factory
        self.config = config
        self.registry = registry
        self.client_factory = client_factory
        self._prompt_mfa = prompt_mfa
        self._stack: list[str] = []
        self._cache: dict[str, ResolvedCredentials] = {}

    def resolve(self, cred: Credential) -> ResolvedCredentials:
        cached = self._cache.get(cred.id)
        if cached is not None:
            return cached
        if cred.id in self._stack:
            chain = " -> ".join(self._stack + [cred.id])
            raise ResolutionError(f"Credential chain contains a cycle: {chain}")
        self._stack.append(cred.id)
        try:
            resolved = provider_for(cred).resolve(cred, self)
        finally:
            self._stack.pop()
        self._cache[cred.id] = resolved
        return resolved

    def resolve_id(self, cred_id: str) -> ResolvedCredentials:
        return self.resolve(self.registry.by_id(cred_id))

    def clear_cache(self) -> None:
        """Forget resolved material so a refresh really hits the API again."""
        self._cache.clear()

    def mfa_code(self, serial: str) -> str:
        if self._prompt_mfa is None:
            raise ResolutionError(
                f"An MFA code is required for {serial}. "
                "Re-run with --mfa-code <code>, or run the command interactively."
            )
        code = self._prompt_mfa(serial)
        if not code:
            raise ResolutionError(f"No MFA code supplied for {serial}")
        return code
