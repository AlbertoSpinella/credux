from datetime import datetime, timedelta, timezone

import pytest

from credux.errors import SsoTokenError
from credux.providers.aws import sso_login
from tests.test_store import memory_keyring  # noqa: F401

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
SESSION = {"start_url": "https://acme.awsapps.com/start", "sso_region": "eu-west-1"}


def test_token_round_trip(memory_keyring):
    sso_login.save_token("acme", "tok", NOW + timedelta(hours=8))

    assert sso_login.load_token("acme", now=NOW) == "tok"


def test_expired_token_is_treated_as_absent(memory_keyring):
    sso_login.save_token("acme", "tok", NOW - timedelta(minutes=1))

    assert sso_login.load_token("acme", now=NOW) is None


def test_token_or_raise_names_the_login_command(memory_keyring):
    with pytest.raises(SsoTokenError) as excinfo:
        sso_login.token_or_raise("acme", now=NOW)

    assert "credux login acme" in str(excinfo.value)
    assert excinfo.value.exit_code == 6


def test_login_completes_the_device_flow(memory_keyring, stubbed_client):
    _, stubber = stubbed_client("sso-oidc")
    stubber.add_response(
        "register_client",
        {"clientId": "cid", "clientSecret": "csecret"},
        {"clientName": "credux", "clientType": "public"},
    )
    stubber.add_response(
        "start_device_authorization",
        {
            "deviceCode": "device",
            "userCode": "ABCD-EFGH",
            "verificationUri": "https://device.sso.eu-west-1.amazonaws.com/",
            "verificationUriComplete": "https://device.sso.eu-west-1.amazonaws.com/?user_code=ABCD-EFGH",
            "expiresIn": 600,
            "interval": 1,
        },
        {"clientId": "cid", "clientSecret": "csecret", "startUrl": SESSION["start_url"]},
    )
    stubber.add_client_error("create_token", service_error_code="AuthorizationPendingException")
    stubber.add_response(
        "create_token",
        {"accessToken": "tok", "expiresIn": 28800, "tokenType": "Bearer"},
        {
            "clientId": "cid",
            "clientSecret": "csecret",
            "grantType": "urn:ietf:params:oauth:grant-type:device_code",
            "deviceCode": "device",
        },
    )
    opened = []
    slept = []

    token = sso_login.login(
        "acme",
        SESSION,
        stubbed_client.factory,
        opener=opened.append,
        sleeper=slept.append,
        echo=lambda *args, **kwargs: None,
        now=NOW,
    )

    assert token == "tok"
    assert opened == ["https://device.sso.eu-west-1.amazonaws.com/?user_code=ABCD-EFGH"]
    assert slept == [1]
    assert sso_login.load_token("acme", now=NOW) == "tok"
    stubber.assert_no_pending_responses()


def test_login_default_opener_and_sleeper_are_read_from_the_module_at_call_time(
    memory_keyring, stubbed_client, monkeypatch
):
    """No opener= or sleeper= is passed to login() here: this is the call
    shape cli.py's `login` command actually uses. Module-level patches of
    sso_login.webbrowser.open and sso_login.time.sleep must be enough to
    intercept both - if they were still parameter defaults bound at import
    time, these patches would have no effect and a real browser would open
    (and a real sleep would happen) instead. Both are patched before the
    call so that even if this regresses, no real browser opens and no real
    sleep happens."""
    opened = []
    slept = []
    monkeypatch.setattr(sso_login.webbrowser, "open", opened.append)
    monkeypatch.setattr(sso_login.time, "sleep", slept.append)

    _, stubber = stubbed_client("sso-oidc")
    stubber.add_response(
        "register_client",
        {"clientId": "cid", "clientSecret": "csecret"},
        {"clientName": "credux", "clientType": "public"},
    )
    stubber.add_response(
        "start_device_authorization",
        {
            "deviceCode": "device",
            "userCode": "ABCD-EFGH",
            "verificationUri": "https://device.sso.eu-west-1.amazonaws.com/",
            "verificationUriComplete": "https://device.sso.eu-west-1.amazonaws.com/?user_code=ABCD-EFGH",
            "expiresIn": 600,
            "interval": 1,
        },
        {"clientId": "cid", "clientSecret": "csecret", "startUrl": SESSION["start_url"]},
    )
    stubber.add_client_error("create_token", service_error_code="AuthorizationPendingException")
    stubber.add_response(
        "create_token",
        {"accessToken": "tok", "expiresIn": 28800, "tokenType": "Bearer"},
        {
            "clientId": "cid",
            "clientSecret": "csecret",
            "grantType": "urn:ietf:params:oauth:grant-type:device_code",
            "deviceCode": "device",
        },
    )

    token = sso_login.login(
        "acme", SESSION, stubbed_client.factory, echo=lambda *args, **kwargs: None, now=NOW
    )

    assert token == "tok"
    assert opened == ["https://device.sso.eu-west-1.amazonaws.com/?user_code=ABCD-EFGH"]
    assert slept == [1]
    stubber.assert_no_pending_responses()


def test_login_reports_a_denied_authorization(memory_keyring, stubbed_client):
    _, stubber = stubbed_client("sso-oidc")
    stubber.add_response("register_client", {"clientId": "cid", "clientSecret": "csecret"}, None)
    stubber.add_response(
        "start_device_authorization",
        {
            "deviceCode": "device",
            "userCode": "ABCD-EFGH",
            "verificationUri": "https://device.example/",
            "verificationUriComplete": "https://device.example/?user_code=ABCD-EFGH",
            "expiresIn": 600,
            "interval": 1,
        },
        None,
    )
    stubber.add_client_error("create_token", service_error_code="AccessDeniedException")

    with pytest.raises(SsoTokenError) as excinfo:
        sso_login.login(
            "acme",
            SESSION,
            stubbed_client.factory,
            opener=lambda url: None,
            sleeper=lambda seconds: None,
            echo=lambda *args, **kwargs: None,
            now=NOW,
        )

    assert "AccessDenied" in str(excinfo.value)


def test_login_rejects_a_session_without_a_start_url(memory_keyring, stubbed_client):
    with pytest.raises(SsoTokenError):
        sso_login.login("acme", {"sso_region": "eu-west-1"}, stubbed_client.factory)


def test_login_raises_when_create_token_response_is_missing_access_token(memory_keyring, stubbed_client):
    _, stubber = stubbed_client("sso-oidc")
    stubber.add_response("register_client", {"clientId": "cid", "clientSecret": "csecret"}, None)
    stubber.add_response(
        "start_device_authorization",
        {
            "deviceCode": "device",
            "userCode": "ABCD-EFGH",
            "verificationUri": "https://device.example/",
            "verificationUriComplete": "https://device.example/?user_code=ABCD-EFGH",
            "expiresIn": 600,
            "interval": 1,
        },
        None,
    )
    stubber.add_response("create_token", {"tokenType": "Bearer"}, None)

    with pytest.raises(SsoTokenError) as excinfo:
        sso_login.login(
            "acme",
            SESSION,
            stubbed_client.factory,
            opener=lambda url: None,
            sleeper=lambda seconds: None,
            echo=lambda *args, **kwargs: None,
            now=NOW,
        )

    assert "accessToken" in str(excinfo.value)
    assert "csecret" not in str(excinfo.value)


def test_login_raises_when_device_authorization_response_is_missing_device_code(
    memory_keyring, stubbed_client
):
    _, stubber = stubbed_client("sso-oidc")
    stubber.add_response("register_client", {"clientId": "cid", "clientSecret": "csecret"}, None)
    stubber.add_response(
        "start_device_authorization",
        {
            "userCode": "ABCD-EFGH",
            "verificationUri": "https://device.example/",
            "verificationUriComplete": "https://device.example/?user_code=ABCD-EFGH",
            "expiresIn": 600,
            "interval": 1,
        },
        None,
    )

    with pytest.raises(SsoTokenError) as excinfo:
        sso_login.login(
            "acme",
            SESSION,
            stubbed_client.factory,
            opener=lambda url: None,
            sleeper=lambda seconds: None,
            echo=lambda *args, **kwargs: None,
            now=NOW,
        )

    assert "deviceCode" in str(excinfo.value)
    assert "csecret" not in str(excinfo.value)


def test_login_wraps_a_register_client_error(memory_keyring, stubbed_client):
    _, stubber = stubbed_client("sso-oidc")
    stubber.add_client_error("register_client", service_error_code="AccessDeniedException")

    with pytest.raises(SsoTokenError) as excinfo:
        sso_login.login(
            "acme",
            SESSION,
            stubbed_client.factory,
            opener=lambda url: None,
            sleeper=lambda seconds: None,
            echo=lambda *args, **kwargs: None,
            now=NOW,
        )

    assert "AccessDenied" in str(excinfo.value)


def test_login_wraps_a_start_device_authorization_error(memory_keyring, stubbed_client):
    _, stubber = stubbed_client("sso-oidc")
    stubber.add_response("register_client", {"clientId": "cid", "clientSecret": "csecret"}, None)
    stubber.add_client_error(
        "start_device_authorization", service_error_code="InvalidClientException"
    )

    with pytest.raises(SsoTokenError) as excinfo:
        sso_login.login(
            "acme",
            SESSION,
            stubbed_client.factory,
            opener=lambda url: None,
            sleeper=lambda seconds: None,
            echo=lambda *args, **kwargs: None,
            now=NOW,
        )

    assert "InvalidClient" in str(excinfo.value)
    assert "csecret" not in str(excinfo.value)
