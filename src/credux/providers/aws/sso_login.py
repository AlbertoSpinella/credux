"""Identity Center OIDC device authorization and the resulting token cache."""

from __future__ import annotations

import json
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from botocore.exceptions import ClientError

from ... import store
from ...errors import SsoTokenError

CLIENT_NAME = "credux"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
TOKEN_FIELD = "token"
DEFAULT_INTERVAL_SECONDS = 5
SLOW_DOWN_BACKOFF_SECONDS = 5


def owner(session_name: str) -> str:
    """Keychain owner key for a session-wide token, shared by its credentials."""
    return f"sso-session:{session_name}"


def _utcnow(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def save_token(session_name: str, access_token: str, expires_at: datetime) -> None:
    payload = json.dumps({"access_token": access_token, "expires_at": expires_at.isoformat()})
    store.set_secret(owner(session_name), TOKEN_FIELD, payload)


def load_token(session_name: str, now: datetime | None = None) -> str | None:
    raw = store.get_secret(owner(session_name), TOKEN_FIELD)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        expires_at = datetime.fromisoformat(payload["expires_at"])
    except (json.JSONDecodeError, KeyError, ValueError):
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= _utcnow(now):
        return None
    return payload.get("access_token")


def token_or_raise(session_name: str, now: datetime | None = None) -> str:
    token = load_token(session_name, now=now)
    if not token:
        raise SsoTokenError(
            f"No valid Identity Center token for session {session_name!r}. "
            f"Run 'credux login {session_name}'."
        )
    return token


def _required_field(payload: dict[str, Any], field: str, session_name: str) -> Any:
    """Pull a required key out of an OIDC response, or raise SsoTokenError."""
    value = payload.get(field)
    if not value:
        raise SsoTokenError(
            f"Identity Center response for {session_name!r} is missing required field {field!r}"
        )
    return value


def _call(session_name: str, fn: Callable[[], Any]) -> Any:
    """Run an OIDC call, turning any ClientError into an SsoTokenError."""
    try:
        return fn()
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        raise SsoTokenError(
            f"Identity Center refused the login for {session_name!r}: {code}"
        ) from exc


def login(
    session_name: str,
    session_cfg: dict[str, Any],
    client_factory: Callable[..., Any],
    opener: Callable[[str], Any] | None = None,
    sleeper: Callable[[float], Any] | None = None,
    echo: Callable[..., Any] = print,
    now: datetime | None = None,
) -> str:
    """Run the device authorization flow and cache the resulting token.

    `echo` keeps a real parameter default, unlike `opener` and `sleeper`:
    an unpatched `print` only writes to this process's own stdout, so a
    forgotten override triggers no real-world action.
    """
    if opener is None:
        opener = webbrowser.open
    if sleeper is None:
        sleeper = time.sleep
    start_url = session_cfg.get("start_url")
    region = session_cfg.get("sso_region")
    if not start_url or not region:
        raise SsoTokenError(
            f"Session {session_name!r} needs both 'start_url' and 'sso_region' in config"
        )

    oidc = client_factory("sso-oidc", region=region)
    client = _call(
        session_name, lambda: oidc.register_client(clientName=CLIENT_NAME, clientType="public")
    )
    client_id = _required_field(client, "clientId", session_name)
    client_secret = _required_field(client, "clientSecret", session_name)

    authorization = _call(
        session_name,
        lambda: oidc.start_device_authorization(
            clientId=client_id,
            clientSecret=client_secret,
            startUrl=start_url,
        ),
    )
    device_code = _required_field(authorization, "deviceCode", session_name)
    verification_url = _required_field(authorization, "verificationUriComplete", session_name)
    user_code = _required_field(authorization, "userCode", session_name)

    echo(f"Confirm the code {user_code} at {verification_url}")
    opener(verification_url)

    interval = int(authorization.get("interval") or DEFAULT_INTERVAL_SECONDS)
    deadline = _utcnow(now) + timedelta(seconds=int(authorization.get("expiresIn", 600)))
    while True:
        try:
            token = oidc.create_token(
                clientId=client_id,
                clientSecret=client_secret,
                grantType=GRANT_TYPE,
                deviceCode=device_code,
            )
            break
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("AuthorizationPendingException", "SlowDownException"):
                if code == "SlowDownException":
                    interval += SLOW_DOWN_BACKOFF_SECONDS
                if _utcnow(now) >= deadline:
                    raise SsoTokenError(
                        f"Authorization for {session_name!r} timed out before it was confirmed"
                    ) from exc
                sleeper(interval)
                continue
            raise SsoTokenError(
                f"Identity Center refused the login for {session_name!r}: {code}"
            ) from exc

    access_token = _required_field(token, "accessToken", session_name)
    expires_at = _utcnow(now) + timedelta(seconds=int(token.get("expiresIn", 3600)))
    save_token(session_name, access_token, expires_at)
    return access_token
