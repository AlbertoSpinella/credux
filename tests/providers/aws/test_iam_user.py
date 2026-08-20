from datetime import datetime, timezone

import pytest

from credux.config import Config
from credux.errors import ResolutionError
from credux.model import Credential, Registry
from credux.providers import base
from credux.providers.aws import iam_user
from tests.test_store import memory_keyring  # noqa: F401  (fixture reuse)

EXPIRES = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def make_context(cred, client_factory=None, mfa=None):
    config = Config()
    registry = Registry(config)
    registry.add(cred)
    return base.ResolutionContext(
        config,
        registry,
        client_factory=client_factory or base.default_client_factory,
        prompt_mfa=mfa,
    )


def test_resolve_returns_the_stored_long_term_keys(memory_keyring):
    cred = Credential(id="c1", kind="iam-user", profile="p", region="eu-west-1")
    iam_user.save_keys("c1", "AKIAEXAMPLE", "secret")

    resolved = iam_user.resolve(cred, make_context(cred))

    assert resolved.access_key_id == "AKIAEXAMPLE"
    assert resolved.session_token is None
    assert resolved.expiration is None


def test_resolve_without_stored_keys_is_actionable(memory_keyring):
    cred = Credential(id="c1", kind="iam-user", profile="acme-dev-User-ada")

    with pytest.raises(ResolutionError) as excinfo:
        iam_user.resolve(cred, make_context(cred))

    assert "acme-dev-User-ada" in str(excinfo.value)


@pytest.mark.parametrize(
    "stored_field", ["aws_access_key_id", "aws_secret_access_key"]
)
def test_resolve_with_only_half_the_key_pair_is_actionable(memory_keyring, stored_field):
    """Half a key pair is unusable, and either half can be the one present -
    a keychain that was edited by hand, or a save that failed between its two
    writes. Neither may fall through to an AWS call with an empty field."""
    cred = Credential(id="c1", kind="iam-user", profile="acme-dev-User-ada")
    from credux import store

    store.set_secret("c1", stored_field, "half-a-pair")

    with pytest.raises(ResolutionError) as excinfo:
        iam_user.resolve(cred, make_context(cred))

    assert "acme-dev-User-ada" in str(excinfo.value)
    assert excinfo.value.exit_code == 4


def test_save_keys_stores_both_halves_or_neither(memory_keyring, monkeypatch):
    """cli.py's rollback around this call only undoes the registry entry, so
    an access key id left behind by a failed second write would be orphaned
    under an id nothing references."""
    from credux import store
    from credux.errors import SecretStoreError

    real_set = store.set_secret

    def fail_on_the_secret(owner, field, value):
        if field == "aws_secret_access_key":
            raise SecretStoreError("keychain refused the write")
        return real_set(owner, field, value)

    monkeypatch.setattr(iam_user.store, "set_secret", fail_on_the_secret)

    with pytest.raises(SecretStoreError):
        iam_user.save_keys("c1", "AKIAEXAMPLE", "secret")

    assert store.get_secret("c1", "aws_access_key_id") is None
    assert store.get_secret("c1", "aws_secret_access_key") is None


def test_resolve_with_mfa_exchanges_for_a_session_token(memory_keyring, stubbed_client):
    cred = Credential(
        id="c1",
        kind="iam-user",
        profile="p",
        region="eu-west-1",
        params={"mfa_serial": "arn:aws:iam::123456789012:mfa/ada", "duration_seconds": 7200},
    )
    iam_user.save_keys("c1", "AKIAEXAMPLE", "secret")
    _, stubber = stubbed_client("sts")
    stubber.add_response(
        "get_session_token",
        {
            "Credentials": {
                "AccessKeyId": "ASIAIOSFODNN7EXAMPLE",
                "SecretAccessKey": "temp-secret",
                "SessionToken": "temp-token",
                "Expiration": EXPIRES,
            }
        },
        {
            "DurationSeconds": 7200,
            "SerialNumber": "arn:aws:iam::123456789012:mfa/ada",
            "TokenCode": "123456",
        },
    )
    ctx = make_context(cred, stubbed_client.factory, mfa=lambda serial: "123456")

    resolved = iam_user.resolve(cred, ctx)

    assert resolved.access_key_id == "ASIAIOSFODNN7EXAMPLE"
    assert resolved.session_token == "temp-token"
    assert resolved.expiration == EXPIRES
    stubber.assert_no_pending_responses()


def test_provider_dispatches_iam_user(memory_keyring):
    from credux.providers.aws import AwsProvider

    cred = Credential(id="c1", kind="iam-user", profile="p")
    iam_user.save_keys("c1", "AKIAEXAMPLE", "secret")

    resolved = AwsProvider().resolve(cred, make_context(cred))

    assert resolved.access_key_id == "AKIAEXAMPLE"


def test_provider_rejects_unknown_kind():
    from credux.providers.aws import AwsProvider

    cred = Credential(id="c1", kind="carrier-pigeon", profile="p")

    with pytest.raises(ResolutionError):
        AwsProvider().resolve(cred, make_context(cred))
