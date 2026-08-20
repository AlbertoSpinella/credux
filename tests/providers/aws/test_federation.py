from urllib.parse import parse_qs, urlparse

import pytest

from credux.config import Config
from credux.model import Credential, Registry
from credux.providers import base
from credux.providers.aws import federation
from credux.providers.base import ResolvedCredentials

TEMP = ResolvedCredentials("ASIAIOSFODNN7EXAMPLE", "secret", session_token="token")
STATIC = ResolvedCredentials("AKIA", "secret")


def test_signin_url_carries_the_token_and_destination():
    url = federation.signin_url(
        TEMP,
        destination="https://console.aws.amazon.com/",
        fetch=lambda request_url: {"SigninToken": "signin-token"},
    )

    query = parse_qs(urlparse(url).query)
    assert query["Action"] == ["login"]
    assert query["SigninToken"] == ["signin-token"]
    assert query["Destination"] == ["https://console.aws.amazon.com/"]


def test_signin_url_sends_the_three_session_fields():
    captured = {}

    def fetch(request_url):
        captured["url"] = request_url
        return {"SigninToken": "signin-token"}

    federation.signin_url(TEMP, destination="https://console.aws.amazon.com/", fetch=fetch)

    query = parse_qs(urlparse(captured["url"]).query)
    assert query["Action"] == ["getSigninToken"]
    session = query["Session"][0]
    assert '"sessionId": "ASIAIOSFODNN7EXAMPLE"' in session or '"sessionId":"ASIAIOSFODNN7EXAMPLE"' in session
    assert "token" in session


def test_signin_url_omits_session_duration_unless_asked():
    """AWS documents SessionDuration as valid only for a sign-in token
    derived from GetFederationToken credentials, and as an error - not an
    ignored parameter - for AssumeRole-derived ones. It used to be sent on
    every request."""
    captured = {}

    def fetch(request_url):
        captured["url"] = request_url
        return {"SigninToken": "signin-token"}

    federation.signin_url(TEMP, destination="https://console.aws.amazon.com/", fetch=fetch)

    assert "SessionDuration" not in parse_qs(urlparse(captured["url"]).query)


def test_signin_url_sends_session_duration_when_asked():
    captured = {}

    def fetch(request_url):
        captured["url"] = request_url
        return {"SigninToken": "signin-token"}

    federation.signin_url(
        TEMP,
        destination="https://console.aws.amazon.com/",
        fetch=fetch,
        session_duration=federation.SESSION_DURATION_SECONDS,
    )

    query = parse_qs(urlparse(captured["url"]).query)
    assert query["SessionDuration"] == ["43200"]


def _capture_signin_request(monkeypatch) -> dict:
    """Intercept the federation call AwsProvider.console_url makes.

    console_url passes no fetch=, so this patches the module attribute the
    real path reads - which is also what stops the test reaching the network.
    """
    captured: dict = {}

    def fetch(request_url):
        captured["url"] = request_url
        return {"SigninToken": "signin-token"}

    monkeypatch.setattr(federation, "_fetch_json", fetch)
    return captured


def test_console_url_omits_session_duration_for_an_already_temporary_credential(monkeypatch):
    from credux.providers.aws import AwsProvider

    captured = _capture_signin_request(monkeypatch)
    config = Config()
    registry = Registry(config)
    cred = Credential(id="c1", kind="iam-role", profile="acme-dev-Role-Admin")
    registry.add(cred)
    ctx = base.ResolutionContext(config, registry)

    AwsProvider().console_url(cred, ctx, TEMP)

    assert "SessionDuration" not in parse_qs(urlparse(captured["url"]).query)


def test_console_url_sends_session_duration_for_federation_token_credentials(
    monkeypatch, stubbed_client
):
    from credux.providers.aws import AwsProvider

    captured = _capture_signin_request(monkeypatch)
    config = Config()
    registry = Registry(config)
    cred = Credential(id="c1", kind="iam-user", profile="acme-dev-User-ada")
    registry.add(cred)
    ctx = base.ResolutionContext(config, registry, client_factory=stubbed_client.factory)
    _, stubber = stubbed_client("sts")
    stubber.add_response(
        "get_federation_token",
        {
            "Credentials": {
                "AccessKeyId": "ASIAIOSFODNN7EXAMPL2",
                "SecretAccessKey": "s",
                "SessionToken": "t",
                "Expiration": "2026-01-01T12:00:00Z",
            },
            "FederatedUser": {
                "FederatedUserId": "1:credux",
                "Arn": "arn:aws:sts::1:federated-user/credux",
            },
        },
        None,
    )

    AwsProvider().console_url(cred, ctx, STATIC)

    query = parse_qs(urlparse(captured["url"]).query)
    assert query["SessionDuration"] == ["43200"]
    stubber.assert_no_pending_responses()


def test_console_url_signs_an_sso_credential_in_directly(monkeypatch):
    """An `sso` credential gets a federation sign-in URL like every other kind.

    It used to get an Identity Center portal deep link
    (`https://<subdomain>.awsapps.com/start/#/console?...`) instead, which only
    completes if the portal's own session cookie already exists in whatever
    browser container the link is opened in - so `credux console` depended on a
    cookie it had just deliberately isolated away, and on a browser extension
    to finish the hand-off. The sign-in URL carries its own one-shot token and
    needs nothing already present in the container.
    """
    from credux.providers.aws import AwsProvider

    captured = _capture_signin_request(monkeypatch)
    config = Config()
    registry = Registry(config)
    cred = Credential(
        id="s1",
        kind="sso",
        profile="acme-billing-SSO-ReadOnly",
        region="eu-west-1",
        params={
            "sso_session": "acme",
            "account_id": "123456789012",
            "account_name": "billing",
            "role_name": "ReadOnly",
        },
    )
    registry.add(cred)
    ctx = base.ResolutionContext(config, registry)

    url = AwsProvider().console_url(cred, ctx, TEMP)

    assert url.startswith(federation.FEDERATION_ENDPOINT + "?")
    assert "awsapps.com" not in url
    query = parse_qs(urlparse(url).query)
    assert query["Action"] == ["login"]
    assert query["SigninToken"] == ["signin-token"]
    assert query["Destination"] == ["https://console.aws.amazon.com/"]
    # Identity Center role credentials arrive with a session token already, so
    # ensure_temporary short-circuits and SessionDuration must stay off.
    assert "SessionDuration" not in parse_qs(urlparse(captured["url"]).query)


def test_signin_url_requires_temporary_credentials():
    from credux.errors import ResolutionError

    with pytest.raises(ResolutionError):
        federation.signin_url(STATIC, destination="https://console.aws.amazon.com/")


def test_signin_url_wraps_fetch_failures_in_a_resolution_error():
    from credux.errors import ResolutionError

    def failing_fetch(request_url):
        raise OSError("network is unreachable")

    with pytest.raises(ResolutionError):
        federation.signin_url(
            TEMP, destination="https://console.aws.amazon.com/", fetch=failing_fetch
        )


def test_ensure_temporary_passes_through_session_credentials():
    config = Config()
    ctx = base.ResolutionContext(config, Registry(config))

    resolved, minted = federation.ensure_temporary("p", TEMP, ctx, "eu-west-1")

    assert resolved is TEMP
    # It short-circuited: nothing was obtained from GetFederationToken, which
    # is what decides whether SessionDuration may be sent.
    assert minted is False


def test_ensure_temporary_calls_get_federation_token(stubbed_client):
    config = Config()
    ctx = base.ResolutionContext(config, Registry(config), client_factory=stubbed_client.factory)
    _, stubber = stubbed_client("sts")
    stubber.add_response(
        "get_federation_token",
        {
            "Credentials": {
                "AccessKeyId": "ASIAIOSFODNN7EXAMPL2",
                "SecretAccessKey": "s",
                "SessionToken": "t",
                "Expiration": "2026-01-01T12:00:00Z",
            },
            "FederatedUser": {"FederatedUserId": "1:credux", "Arn": "arn:aws:sts::1:federated-user/credux"},
        },
        None,
    )

    resolved, minted = federation.ensure_temporary(
        "acme-dev-User-ada", STATIC, ctx, "eu-west-1"
    )

    assert resolved.session_token == "t"
    assert minted is True
    stubber.assert_no_pending_responses()


def test_federation_name_is_trimmed_to_the_api_limit():
    long_profile = "acme-averylongaccountname-Role-AdministratorAccess"

    name = federation.federation_name(long_profile)

    assert len(name) <= 32
    assert all(
        (char.isascii() and char.isalnum()) or char in "=,.@-_" for char in name
    )


def test_federation_name_replaces_non_ascii_letters():
    """str.isalnum() is Unicode-aware, so an accented account name used to
    pass through untouched even though the AWS Name pattern is ASCII-only."""
    name = federation.federation_name("acme-Zürich-Role-Admin")

    assert name == "acme-Z-rich-Role-Admin"
    assert name.isascii()


def test_federation_name_falls_back_when_truncation_leaves_too_short_a_name():
    # GetFederationToken's Name has a 2-character minimum. A profile that is
    # mostly separator characters can truncate down to a single leftover
    # character once the leading separators are stripped.
    name = federation.federation_name("-" * 100 + "x")

    assert name == "credux"
