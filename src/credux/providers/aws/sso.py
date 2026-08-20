"""Identity Center credentials: resolving one role, and discovering all of them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from botocore.exceptions import ClientError

from ...errors import ResolutionError, SsoTokenError
from ...model import Credential
from ..base import ResolutionContext, ResolvedCredentials
from . import sso_login
from .sso_login import _required_field

PAGE_SIZE = 100


@dataclass
class DiscoveredRole:
    account_id: str
    account_name: str
    role_name: str


def _required(cred: Credential, key: str) -> str:
    """Same wording as iam_role.py's helper of the same purpose, for consistency."""
    value = cred.params.get(key)
    if not value:
        raise ResolutionError(
            f"Credential {cred.profile!r} is missing params.{key}; "
            "fix it with 'credux config edit' or re-add the credential"
        )
    return str(value)


def session_config(ctx: ResolutionContext, session_name: str) -> dict[str, Any]:
    config = ctx.config.sso_sessions.get(session_name)
    if not config:
        known = ", ".join(sorted(ctx.config.sso_sessions)) or "none"
        raise ResolutionError(
            f"Unknown Identity Center session {session_name!r}; configured sessions: {known}"
        )
    return config


def _call(identifier: str, action: str, fn: Callable[[], Any]) -> Any:
    """Run an SSO API call, turning any ClientError into an SsoTokenError.

    Same catch-and-wrap shape as sso_login._call, but that helper's message is
    specific to the OIDC login flow ("refused the login"); resolution and
    discovery calls need their own wording, so it is not reused directly.
    """
    try:
        return fn()
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        raise SsoTokenError(
            f"Identity Center rejected {action} for {identifier!r}: {code}"
        ) from exc


def _from_role_credentials(
    payload: dict[str, Any], region: str | None, session_name: str
) -> ResolvedCredentials:
    """SSO returns camelCase keys and expiration as epoch milliseconds."""
    access_key_id = _required_field(payload, "accessKeyId", session_name)
    secret_access_key = _required_field(payload, "secretAccessKey", session_name)
    expiration = payload.get("expiration")
    expires_at = (
        datetime.fromtimestamp(int(expiration) / 1000, tz=timezone.utc)
        if expiration is not None
        else None
    )
    return ResolvedCredentials(
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        session_token=payload.get("sessionToken"),
        expiration=expires_at,
        region=region,
    )


def role_credentials(
    session_cfg: dict[str, Any],
    token: str,
    account_id: str,
    role_name: str,
    client_factory: Callable[..., Any],
    region: str | None = None,
    identifier: str | None = None,
) -> ResolvedCredentials:
    """Credentials for one (account, role) pair, taking the pair directly.

    Separate from `resolve` because a caller may need credentials for a pair
    that has no `Credential` record at all: `discover` has only just reported
    it, and nothing has been added to the registry yet (see
    `org.management_account_id_via_sso`). `resolve` is the thin wrapper that
    reads the pair off a credential and calls this.

    `identifier` only names the session in error messages; like `discover`, a
    caller that has no session name falls back to the start URL.
    """
    identifier = identifier or str(session_cfg.get("start_url", "identity center session"))
    client = client_factory("sso", region=session_cfg["sso_region"])
    response = _call(
        identifier,
        "a role credentials request",
        lambda: client.get_role_credentials(
            roleName=role_name,
            accountId=account_id,
            accessToken=token,
        ),
    )
    payload = _required_field(response, "roleCredentials", identifier)
    return _from_role_credentials(payload, region, identifier)


def resolve(cred: Credential, ctx: ResolutionContext) -> ResolvedCredentials:
    session_name = _required(cred, "sso_session")
    role_name = _required(cred, "role_name")
    account_id = _required(cred, "account_id")
    config = session_config(ctx, session_name)
    token = sso_login.token_or_raise(session_name)

    return role_credentials(
        config,
        token,
        account_id,
        role_name,
        ctx.client_factory,
        region=cred.region,
        identifier=session_name,
    )


def discover(
    session_cfg: dict[str, Any], token: str, client_factory: Callable[..., Any]
) -> list[DiscoveredRole]:
    """Every (account, role) pair the token can reach, in portal order."""
    identifier = str(session_cfg.get("start_url", "identity center session"))
    client = client_factory("sso", region=session_cfg["sso_region"])

    def _pages(paginator_name: str, **kwargs: Any) -> list[dict[str, Any]]:
        # The paginator only makes requests while its iterator is consumed, so
        # the list() call (not just .paginate()) must run inside _call for a
        # ClientError raised mid-pagination to be caught and wrapped.
        return _call(
            identifier,
            f"a {paginator_name} request",
            lambda: list(client.get_paginator(paginator_name).paginate(**kwargs)),
        )

    accounts: list[dict[str, Any]] = []
    for page in _pages("list_accounts", accessToken=token, maxResults=PAGE_SIZE):
        accounts.extend(page.get("accountList", []))

    found: list[DiscoveredRole] = []
    for account in accounts:
        account_id = _required_field(account, "accountId", identifier)
        account_name = account.get("accountName", account_id)
        for page in _pages(
            "list_account_roles", accessToken=token, accountId=account_id, maxResults=PAGE_SIZE
        ):
            for role in page.get("roleList", []):
                found.append(
                    DiscoveredRole(
                        account_id=account_id,
                        account_name=account_name,
                        role_name=_required_field(role, "roleName", identifier),
                    )
                )
    return found
