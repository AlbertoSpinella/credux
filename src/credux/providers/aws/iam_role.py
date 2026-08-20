"""Assumed IAM role credentials, chained onto another Credux credential."""

from __future__ import annotations

from ...errors import ResolutionError
from ...model import Credential
from ..base import ResolutionContext, ResolvedCredentials, from_sts_payload

DEFAULT_DURATION_SECONDS = 3600
DEFAULT_SESSION_NAME = "credux"


def _required(cred: Credential, key: str) -> str:
    value = cred.params.get(key)
    if not value:
        raise ResolutionError(
            f"Credential {cred.profile!r} is missing params.{key}; "
            "fix it with 'credux config edit' or re-add the credential"
        )
    return str(value)


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
    role_arn = _required(cred, "role_arn")
    source_id = _required(cred, "source")
    source = ctx.resolve_id(source_id)

    request = {
        "RoleArn": role_arn,
        "RoleSessionName": str(cred.params.get("session_name") or DEFAULT_SESSION_NAME),
        "DurationSeconds": _duration_seconds(cred),
    }
    external_id = cred.params.get("external_id")
    if external_id:
        request["ExternalId"] = str(external_id)
    mfa_serial = cred.params.get("mfa_serial")
    if mfa_serial:
        request["SerialNumber"] = str(mfa_serial)
        request["TokenCode"] = ctx.mfa_code(str(mfa_serial))

    sts = ctx.client_factory("sts", region=cred.region, credentials=source)
    response = sts.assume_role(**request)
    return from_sts_payload(response["Credentials"], region=cred.region)
