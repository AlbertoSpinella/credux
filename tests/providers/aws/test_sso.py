from datetime import datetime, timedelta, timezone

import pytest

from credux.config import Config
from credux.errors import ResolutionError, SsoTokenError
from credux.model import Credential, Registry
from credux.providers import base
from credux.providers.aws import sso, sso_login
from tests.test_store import memory_keyring  # noqa: F401

# sso.resolve() has no way to inject a fixed clock into sso_login.token_or_raise
# (its interface is resolve(cred, ctx), with no `now`), so the token this test saves
# is checked against the real wall clock. NOW is computed once here from the real
# clock (not hardcoded) so this test cannot rot at any date boundary. It is truncated
# to millisecond precision because the SSO API returns `expiration` as epoch
# milliseconds - keeping NOW itself millisecond-precise means the ms round trip below
# is exact, so the assertion can compare directly against NOW instead of approximating.
_now = datetime.now(timezone.utc)
NOW = _now.replace(microsecond=(_now.microsecond // 1000) * 1000)
SESSION = {"start_url": "https://acme.awsapps.com/start", "sso_region": "eu-west-1"}
EXPIRY_MS = int((NOW + timedelta(hours=1)).timestamp() * 1000)


def sso_credential():
    return Credential(
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


def context(cred, client_factory=None):
    config = Config()
    config.sso_sessions["acme"] = SESSION
    registry = Registry(config)
    registry.add(cred)
    return base.ResolutionContext(config, registry, client_factory=client_factory)


def test_resolve_returns_role_credentials(memory_keyring, stubbed_client):
    sso_login.save_token("acme", "tok", NOW + timedelta(hours=8))
    cred = sso_credential()
    _, stubber = stubbed_client("sso")
    stubber.add_response(
        "get_role_credentials",
        {
            "roleCredentials": {
                "accessKeyId": "ASIAEXAMPLE",
                "secretAccessKey": "secret",
                "sessionToken": "token",
                "expiration": EXPIRY_MS,
            }
        },
        {"roleName": "ReadOnly", "accountId": "123456789012", "accessToken": "tok"},
    )

    resolved = sso.resolve(cred, context(cred, stubbed_client.factory))

    assert resolved.access_key_id == "ASIAEXAMPLE"
    assert resolved.session_token == "token"
    assert resolved.expiration == NOW + timedelta(hours=1)
    stubber.assert_no_pending_responses()


def test_resolve_without_a_token_tells_you_to_log_in(memory_keyring):
    cred = sso_credential()

    with pytest.raises(SsoTokenError) as excinfo:
        sso.resolve(cred, context(cred))

    assert "credux login acme" in str(excinfo.value)


def test_resolve_with_an_unknown_session_is_a_config_error(memory_keyring):
    cred = sso_credential()
    cred.params["sso_session"] = "missing"

    with pytest.raises(ResolutionError) as excinfo:
        sso.resolve(cred, context(cred))

    assert "missing" in str(excinfo.value)


def test_resolve_with_a_missing_account_id_is_a_config_error(memory_keyring):
    cred = sso_credential()
    del cred.params["account_id"]

    with pytest.raises(ResolutionError) as excinfo:
        sso.resolve(cred, context(cred))

    assert "account_id" in str(excinfo.value)


def test_role_credentials_fetches_a_pair_no_credential_is_configured_for(stubbed_client):
    """The management-account probe needs credentials for an (account, role) pair
    that has no Credential record: discovery has only just produced it, and
    `resolve` takes a Credential. Hence the split - `resolve` is a thin wrapper
    over this, and this takes the pair directly."""
    _, stubber = stubbed_client("sso")
    stubber.add_response(
        "get_role_credentials",
        {
            "roleCredentials": {
                "accessKeyId": "ASIAPROBE",
                "secretAccessKey": "secret",
                "sessionToken": "token",
                "expiration": EXPIRY_MS,
            }
        },
        {"roleName": "Admin", "accountId": "999999999999", "accessToken": "tok"},
    )

    resolved = sso.role_credentials(
        SESSION, "tok", "999999999999", "Admin", stubbed_client.factory
    )

    assert resolved.access_key_id == "ASIAPROBE"
    assert resolved.session_token == "token"
    assert resolved.expiration == NOW + timedelta(hours=1)
    stubber.assert_no_pending_responses()


def test_role_credentials_wraps_a_refusal_as_an_sso_token_error(stubbed_client):
    _, stubber = stubbed_client("sso")
    stubber.add_client_error("get_role_credentials", service_error_code="UnauthorizedException")

    with pytest.raises(SsoTokenError) as excinfo:
        sso.role_credentials(SESSION, "tok", "999999999999", "Admin", stubbed_client.factory)

    assert "UnauthorizedException" in str(excinfo.value)


def test_discover_pairs_every_account_with_every_role(stubbed_client):
    _, stubber = stubbed_client("sso")
    stubber.add_response(
        "list_accounts",
        {
            "accountList": [
                {"accountId": "111111111111", "accountName": "billing"},
                {"accountId": "222222222222", "accountName": "platform"},
            ]
        },
        {"accessToken": "tok", "maxResults": 100},
    )
    stubber.add_response(
        "list_account_roles",
        {"roleList": [{"roleName": "ReadOnly"}, {"roleName": "Admin"}]},
        {"accessToken": "tok", "accountId": "111111111111", "maxResults": 100},
    )
    stubber.add_response(
        "list_account_roles",
        {"roleList": [{"roleName": "Admin"}]},
        {"accessToken": "tok", "accountId": "222222222222", "maxResults": 100},
    )

    found = sso.discover(SESSION, "tok", stubbed_client.factory)

    assert [(item.account_name, item.role_name) for item in found] == [
        ("billing", "ReadOnly"),
        ("billing", "Admin"),
        ("platform", "Admin"),
    ]
    stubber.assert_no_pending_responses()
