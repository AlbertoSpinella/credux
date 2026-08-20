"""Deciding whether an account is the organization's management account.

Never fatal: without organizations:DescribeOrganization the answer is simply
unknown, and naming falls back to no prefix.
"""

from __future__ import annotations

from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from ...errors import CreduxError
from ..base import ResolvedCredentials
from . import sso


def management_account_id(
    resolved: ResolvedCredentials,
    client_factory: Callable[..., Any],
    region: str | None = None,
) -> str | None:
    """The organization's management account id, or None when unknowable.

    Reports the id rather than a yes/no so a caller verifying an answer can
    say which account AWS actually named, instead of only that the answer was
    wrong. `detect_management` is the yes/no form, in terms of this.
    """
    try:
        client = client_factory("organizations", region=region, credentials=resolved)
        organization = client.describe_organization()["Organization"]
    except (ClientError, BotoCoreError):
        return None
    master = organization.get("MasterAccountId")
    if not master:
        return None
    return str(master)


def detect_management(
    account_id: str,
    resolved: ResolvedCredentials,
    client_factory: Callable[..., Any],
    region: str | None = None,
) -> bool | None:
    """True, False, or None when the API would not say."""
    master = management_account_id(resolved, client_factory, region=region)
    if master is None:
        return None
    return master == str(account_id)


def management_account_id_via_sso(
    session_cfg: dict[str, Any],
    token: str,
    account_id: str,
    role_name: str,
    client_factory: Callable[..., Any],
) -> str | None:
    """Ask Organizations from inside one Identity Center role.

    A member account may call DescribeOrganization, so any role that holds the
    permission can name the management account for the whole organization -
    there is no need to already be in the management account to find out which
    one it is.

    Every failure is None, never an exception: the token may have expired, the
    role may not exist any more, and the role may simply lack
    organizations:DescribeOrganization. None means "unknown", which leaves the
    caller trusting whatever it already had rather than aborting a sync over a
    check that is only there to catch a contradiction.
    """
    try:
        resolved = sso.role_credentials(
            session_cfg, token, account_id, role_name, client_factory
        )
    except CreduxError:
        return None
    return management_account_id(
        resolved, client_factory, region=session_cfg.get("sso_region")
    )
