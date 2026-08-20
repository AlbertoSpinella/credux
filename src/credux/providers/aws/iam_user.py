"""IAM user credentials: long-term keys, optionally traded for a session token."""

from __future__ import annotations

from ... import store
from ...errors import ResolutionError
from ...model import Credential
from ..base import ResolutionContext, ResolvedCredentials, from_sts_payload

SECRET_FIELDS = ("aws_access_key_id", "aws_secret_access_key")

DEFAULT_DURATION_SECONDS = 3600


def save_keys(cred_id: str, access_key_id: str, secret_access_key: str) -> None:
    """Store both halves of a key pair, or neither.

    There is no transaction across two keychain writes. If the second fails,
    the first is deleted before the error propagates: `cli.py`'s own rollback
    around this call only undoes the registry entry, so a half-written pair
    would otherwise leave an access key id in the keychain under an id
    nothing references and nothing can reach to clean up.

    The delete is itself best-effort - a keychain that just refused a write
    may well refuse a delete too, and the original failure is the one worth
    reporting.
    """
    store.set_secret(cred_id, "aws_access_key_id", access_key_id)
    try:
        store.set_secret(cred_id, "aws_secret_access_key", secret_access_key)
    except BaseException:
        try:
            store.delete_secret(cred_id, "aws_access_key_id")
        except Exception:
            pass
        raise


def _duration_seconds(cred: Credential) -> int:
    raw = cred.params.get("duration_seconds", DEFAULT_DURATION_SECONDS)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ResolutionError(
            f"Credential {cred.profile!r} has an invalid params.duration_seconds "
            f"value {raw!r}; it must be a whole number of seconds"
        ) from exc


def resolve(cred: Credential, ctx: ResolutionContext) -> ResolvedCredentials:
    access_key_id = store.get_secret(cred.id, "aws_access_key_id")
    secret_access_key = store.get_secret(cred.id, "aws_secret_access_key")
    if not access_key_id or not secret_access_key:
        raise ResolutionError(
            f"No access key stored for {cred.profile!r}. "
            f"Remove it with 'credux remove {cred.profile}' and run "
            "'credux add iam-user' again."
        )
    static = ResolvedCredentials(
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        region=cred.region,
    )

    mfa_serial = cred.params.get("mfa_serial")
    if not mfa_serial:
        return static

    duration = _duration_seconds(cred)
    sts = ctx.client_factory("sts", region=cred.region, credentials=static)
    response = sts.get_session_token(
        DurationSeconds=duration,
        SerialNumber=mfa_serial,
        TokenCode=ctx.mfa_code(mfa_serial),
    )
    return from_sts_payload(response["Credentials"], region=cred.region)
