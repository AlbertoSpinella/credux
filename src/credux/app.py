"""Wiring: one object that owns config, registry, state, and resolution."""

from __future__ import annotations

from typing import Any, Callable

import click

from .config import Config
from .model import Registry
from .providers import aws as aws_provider  # noqa: F401  registers the aws provider
from .providers import base as provider_base
from .providers.base import ResolutionContext
from .sessions import SessionManager
from .state import State


def prompt_mfa(serial: str) -> str:
    return click.prompt(f"MFA code for {serial}", hide_input=False, type=str)


class App:
    """Built once per CLI invocation and handed to every command."""

    def __init__(
        self,
        client_factory: Callable[..., Any] | None = None,
        mfa_code: str | None = None,
    ) -> None:
        self.config = Config.load()
        self.registry = Registry(self.config)
        self.state = State.load()
        if client_factory is None:
            client_factory = provider_base.default_client_factory
        self.client_factory = client_factory
        self._mfa_code = mfa_code

    def _prompt_mfa(self, serial: str) -> str:
        if self._mfa_code:
            return self._mfa_code
        return prompt_mfa(serial)

    def context(self) -> ResolutionContext:
        return ResolutionContext(
            self.config,
            self.registry,
            client_factory=self.client_factory,
            prompt_mfa=self._prompt_mfa,
        )

    def sessions(self, ctx: ResolutionContext | None = None) -> SessionManager:
        return SessionManager(self.config, self.registry, self.state, ctx or self.context())

    def save(self) -> None:
        self.config.save()
