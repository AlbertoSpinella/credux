"""Secret material access. The OS keychain is the only place secrets live."""

from __future__ import annotations

from typing import Iterable

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from .errors import SecretStoreError

SERVICE = "credux"

UNAVAILABLE_PREFIX = "unavailable"

_UNAVAILABLE = (
    "The OS keychain is unavailable ({detail}). "
    "On Linux install and start a Secret Service provider such as gnome-keyring "
    "or kwallet; on Windows the Credential Manager service must be running. "
    "Run 'credux doctor' for details."
)


def _key(owner: str, field: str) -> str:
    return f"{owner}:{field}"


def set_secret(owner: str, field: str, value: str) -> None:
    """Store one secret field for one owner (a credential id or sso-session:<name>)."""
    try:
        keyring.set_password(SERVICE, _key(owner, field), value)
    except KeyringError as exc:
        raise SecretStoreError(_UNAVAILABLE.format(detail=exc)) from exc


def get_secret(owner: str, field: str) -> str | None:
    try:
        return keyring.get_password(SERVICE, _key(owner, field))
    except KeyringError as exc:
        raise SecretStoreError(_UNAVAILABLE.format(detail=exc)) from exc


def delete_secret(owner: str, field: str) -> None:
    try:
        keyring.delete_password(SERVICE, _key(owner, field))
    except PasswordDeleteError:
        # Deleting something that is already gone is not a failure.
        return
    except KeyringError as exc:
        raise SecretStoreError(_UNAVAILABLE.format(detail=exc)) from exc


def delete_owner(owner: str, fields: Iterable[str]) -> None:
    for field in fields:
        delete_secret(owner, field)


def backend_name() -> str:
    """Fully qualified 'module.ClassName' of the active keyring backend.

    The module half is not decoration: keyring names its own no-op backends
    `keyring.backends.fail.Keyring` and `keyring.backends.null.Keyring`, so
    the bare class name of "there is no keychain here" is indistinguishable
    from a working backend's. Only the module tells them apart, and
    `doctor._secret_store` needs that to report a headless box honestly.

    On failure the result starts with UNAVAILABLE_PREFIX, which is also
    treated as unusable rather than parsed as a dotted path.
    """
    try:
        backend = type(keyring.get_keyring())
        return f"{backend.__module__}.{backend.__qualname__}"
    except KeyringError as exc:  # pragma: no cover - defensive
        return f"{UNAVAILABLE_PREFIX} ({exc})"
