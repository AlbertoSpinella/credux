from datetime import datetime, timezone

import pytest

from credux.config import Config
from credux.errors import ResolutionError
from credux.model import Credential, Registry
from credux.providers import base
from credux.providers.aws import iam_role, iam_user
from tests.test_store import memory_keyring  # noqa: F401

EXPIRES = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
ROLE_ARN = "arn:aws:iam::123456789012:role/Admin"


def context(credentials, client_factory=None, mfa=None):
    config = Config()
    registry = Registry(config)
    for cred in credentials:
        registry.add(cred)
    return base.ResolutionContext(
        config, registry, client_factory=client_factory, prompt_mfa=mfa
    )


def source_credential():
    iam_user.save_keys("src", "AKIAEXAMPLE", "secret")
    return Credential(id="src", kind="iam-user", profile="acme-dev-User-ada")


def role_credential(**params):
    base_params = {"role_arn": ROLE_ARN, "source": "src", "session_name": "credux"}
    base_params.update(params)
    return Credential(
        id="role1",
        kind="iam-role",
        profile="acme-prod-Role-Admin",
        region="eu-west-1",
        params=base_params,
    )


def assume_response(access_key_id="ASIAIOSFODNN7EXAMPLE"):
    return {
        "Credentials": {
            "AccessKeyId": access_key_id,
            "SecretAccessKey": "temp",
            "SessionToken": "token",
            "Expiration": EXPIRES,
        },
        "AssumedRoleUser": {"AssumedRoleId": "AROA:credux", "Arn": f"{ROLE_ARN}/credux"},
    }


def test_assume_role_uses_the_source_credentials(memory_keyring, stubbed_client):
    src = source_credential()
    role = role_credential()
    _, stubber = stubbed_client("sts")
    stubber.add_response(
        "assume_role",
        assume_response(),
        {"RoleArn": ROLE_ARN, "RoleSessionName": "credux", "DurationSeconds": 3600},
    )

    resolved = iam_role.resolve(role, context([src, role], stubbed_client.factory))

    assert resolved.access_key_id == "ASIAIOSFODNN7EXAMPLE"
    assert resolved.expiration == EXPIRES
    stubber.assert_no_pending_responses()


def test_external_id_and_mfa_are_forwarded(memory_keyring, stubbed_client):
    src = source_credential()
    role = role_credential(
        external_id="xyz", mfa_serial="arn:aws:iam::123456789012:mfa/ada", duration_seconds=900
    )
    _, stubber = stubbed_client("sts")
    stubber.add_response(
        "assume_role",
        assume_response(),
        {
            "RoleArn": ROLE_ARN,
            "RoleSessionName": "credux",
            "DurationSeconds": 900,
            "ExternalId": "xyz",
            "SerialNumber": "arn:aws:iam::123456789012:mfa/ada",
            "TokenCode": "654321",
        },
    )
    ctx = context([src, role], stubbed_client.factory, mfa=lambda serial: "654321")

    iam_role.resolve(role, ctx)

    stubber.assert_no_pending_responses()


def test_missing_role_arn_is_a_config_error(memory_keyring):
    role = Credential(id="role1", kind="iam-role", profile="p", params={"source": "src"})

    with pytest.raises(ResolutionError) as excinfo:
        iam_role.resolve(role, context([source_credential(), role]))

    assert "role_arn" in str(excinfo.value)


def test_missing_source_is_a_config_error(memory_keyring):
    role = Credential(id="role1", kind="iam-role", profile="p", params={"role_arn": ROLE_ARN})

    with pytest.raises(ResolutionError) as excinfo:
        iam_role.resolve(role, context([role]))

    assert "source" in str(excinfo.value)


def test_role_chained_on_a_role_resolves_recursively(memory_keyring, stubbed_client):
    src = source_credential()
    first = role_credential()
    second_role_arn = "arn:aws:iam::210987654321:role/Deploy"
    second = Credential(
        id="role2",
        kind="iam-role",
        profile="acme-prod-Role-Deploy",
        region="eu-west-1",
        params={
            "role_arn": second_role_arn,
            "source": "role1",
            "session_name": "credux",
        },
    )
    first_level_key = "ASIAROLE1EXAMPLE1234"
    second_level_key = "ASIAROLE2EXAMPLE5678"
    _, stubber = stubbed_client("sts")
    # The chain resolves innermost-first: role2's resolution triggers role1's
    # resolution (against the iam-user source) before role2 makes its own call.
    stubber.add_response(
        "assume_role",
        assume_response(access_key_id=first_level_key),
        {"RoleArn": ROLE_ARN, "RoleSessionName": "credux", "DurationSeconds": 3600},
    )
    stubber.add_response(
        "assume_role",
        assume_response(access_key_id=second_level_key),
        {"RoleArn": second_role_arn, "RoleSessionName": "credux", "DurationSeconds": 3600},
    )

    resolved = iam_role.resolve(second, context([src, first, second], stubbed_client.factory))

    # The final result must be role2's (outer) credentials, not role1's (inner).
    assert resolved.access_key_id == second_level_key
    stubber.assert_no_pending_responses()

    # Exactly two "sts" clients were built: one to assume role1 (using the
    # iam-user's static keys) and one to assume role2 (using role1's assumed
    # credentials). The second must be built with role1's actual output.
    assert len(stubbed_client.calls) == 2
    second_call_credentials = stubbed_client.calls[1][2]
    assert second_call_credentials.access_key_id == first_level_key


def test_missing_session_name_defaults_to_credux(memory_keyring, stubbed_client):
    src = source_credential()
    role = Credential(
        id="role1",
        kind="iam-role",
        profile="acme-prod-Role-Admin",
        region="eu-west-1",
        params={"role_arn": ROLE_ARN, "source": "src"},
    )
    _, stubber = stubbed_client("sts")
    stubber.add_response(
        "assume_role",
        assume_response(),
        {
            "RoleArn": ROLE_ARN,
            "RoleSessionName": iam_role.DEFAULT_SESSION_NAME,
            "DurationSeconds": 3600,
        },
    )

    iam_role.resolve(role, context([src, role], stubbed_client.factory))

    stubber.assert_no_pending_responses()


def test_invalid_duration_seconds_is_a_config_error(memory_keyring):
    role = role_credential(duration_seconds="1h")

    with pytest.raises(ResolutionError) as excinfo:
        iam_role.resolve(role, context([source_credential(), role]))

    assert "duration_seconds" in str(excinfo.value)
