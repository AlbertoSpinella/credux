"""Typed errors; each carries the process exit code Credux should return."""

from __future__ import annotations


class CreduxError(Exception):
    """Base for every error Credux reports to the user."""

    exit_code = 1


class UsageError(CreduxError):
    """The command was invoked incorrectly."""

    exit_code = 2


class ConfigError(CreduxError):
    """The configuration on disk is missing something or is malformed."""

    exit_code = 3


class ResolutionError(CreduxError):
    """A credential could not be turned into usable material."""

    exit_code = 4


class SecretStoreError(CreduxError):
    """The OS secret store is unavailable or refused an operation."""

    exit_code = 5


class SsoTokenError(CreduxError):
    """An Identity Center token is missing or expired."""

    exit_code = 6


class IntegrationError(CreduxError):
    """An integration template or post-sync hook failed."""

    exit_code = 7


class UpgradeError(CreduxError):
    """Credux could not replace its own installation."""

    exit_code = 8
