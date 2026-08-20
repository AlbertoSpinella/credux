"""Building an AWS console sign-in URL from session credentials."""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import urlopen

from ...errors import ResolutionError
from ..base import ResolutionContext, ResolvedCredentials, from_sts_payload

FEDERATION_ENDPOINT = "https://signin.aws.amazon.com/federation"
DEFAULT_ISSUER = "https://credux.local/"
FEDERATION_POLICY_ARN = "arn:aws:iam::aws:policy/AdministratorAccess"
NAME_MAX_LENGTH = 32
_NAME_ALLOWED = "=,.@-_"

# The federation endpoint's maximum, and only meaningful for a sign-in token
# derived from GetFederationToken credentials - see signin_url.
SESSION_DURATION_SECONDS = 43200


def federation_name(profile: str) -> str:
    """A GetFederationToken name: 2 to 32 chars from a restricted alphabet.

    Keeping the tail of the profile (rather than the head) preserves the most
    distinguishing part of names built from credux's naming templates, which
    put the role or user at the end (e.g. "{org}-{account_name}-Role-{role}").
    Truncation can leave a leading separator, which is stripped; if that
    strips everything, or leaves a single character, GetFederationToken's
    2-character minimum is not met, so fall back to a fixed name instead.

    The alphabet test is `isascii() and isalnum()`, not `isalnum()` alone:
    `str.isalnum` is Unicode-aware, so an account name like "Zurich" spelled
    with a u-umlaut passed straight through, while the AWS Name pattern
    ([\\w+=,.@-]) is ASCII.
    """
    cleaned = "".join(
        char if ((char.isascii() and char.isalnum()) or char in _NAME_ALLOWED) else "-"
        for char in profile
    )
    trimmed = cleaned[-NAME_MAX_LENGTH:].lstrip("-")
    return trimmed if len(trimmed) >= 2 else "credux"


def ensure_temporary(
    profile: str,
    resolved: ResolvedCredentials,
    ctx: ResolutionContext,
    region: str | None,
) -> tuple[ResolvedCredentials, bool]:
    """The federation endpoint only accepts temporary credentials.

    Long-term IAM user keys are exchanged for a federation token first. The
    effective permissions are the intersection of the user's policies and the
    policy passed here, so the broad policy ARN cannot grant anything new.

    Returns the usable credentials and whether *this call* minted them via
    GetFederationToken. Callers need that second value, not just the
    credentials: `signin_url` may only send SessionDuration for a token
    derived from GetFederationToken, and anything already carrying a session
    token (an assumed role, an Identity Center role) short-circuits here
    without ever reaching that API.
    """
    if resolved.session_token:
        return resolved, False
    sts = ctx.client_factory("sts", region=region, credentials=resolved)
    response = sts.get_federation_token(
        Name=federation_name(profile),
        PolicyArns=[{"arn": FEDERATION_POLICY_ARN}],
    )
    return from_sts_payload(response["Credentials"], region=region), True


def _fetch_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=15) as response:  # noqa: S310 - fixed AWS endpoint
        return json.loads(response.read().decode("utf-8"))


def signin_url(
    resolved: ResolvedCredentials,
    destination: str,
    issuer: str = DEFAULT_ISSUER,
    fetch: Callable[[str], dict[str, Any]] | None = None,
    session_duration: int | None = None,
) -> str:
    """Exchange session credentials for a one-shot console sign-in URL.

    `session_duration` is sent as the endpoint's SessionDuration parameter
    and defaults to omitting it. AWS documents that parameter as valid only
    when the sign-in token derives from GetFederationToken credentials; with
    AssumeRole-derived credentials the endpoint is expected to reject the
    request rather than ignore the parameter. Sending it unconditionally
    therefore risked failing every `credux console` against an `iam-role` or
    `sso` credential, which are precisely the ones that reach here with a
    session token already in hand. Only the caller knows which case it is in,
    so only the caller passes it (see `ensure_temporary`'s second return
    value).
    """
    if not resolved.session_token:
        raise ResolutionError(
            "The AWS console federation endpoint requires temporary credentials; "
            "call ensure_temporary first"
        )
    session = json.dumps(
        {
            "sessionId": resolved.access_key_id,
            "sessionKey": resolved.secret_access_key,
            "sessionToken": resolved.session_token,
        }
    )
    parameters: dict[str, Any] = {"Action": "getSigninToken", "Session": session}
    if session_duration is not None:
        parameters["SessionDuration"] = session_duration
    request_url = f"{FEDERATION_ENDPOINT}?" + urlencode(parameters)
    try:
        payload = (fetch or _fetch_json)(request_url)
    except ResolutionError:
        raise
    except Exception as exc:
        # Do not interpolate request_url or str(exc) here: request_url carries
        # the session credentials as a query parameter.
        raise ResolutionError(
            "Could not reach the AWS federation endpoint to obtain a sign-in token"
        ) from exc
    token = payload.get("SigninToken")
    if not token:
        raise ResolutionError("The AWS federation endpoint did not return a sign-in token")
    return (
        f"{FEDERATION_ENDPOINT}?"
        + urlencode(
            {
                "Action": "login",
                "Issuer": issuer,
                "Destination": destination,
                "SigninToken": token,
            }
        )
    )
