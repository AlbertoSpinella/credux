from credux.providers.aws import org
from credux.providers.base import ResolvedCredentials

CREDS = ResolvedCredentials("AKIA", "secret")
SESSION = {"start_url": "https://acme.awsapps.com/start", "sso_region": "eu-west-1"}


def organization(master_id):
    return {
        "Organization": {
            "Id": "o-example",
            "Arn": "arn:aws:organizations::111111111111:organization/o-example",
            "FeatureSet": "ALL",
            "MasterAccountArn": f"arn:aws:organizations::{master_id}:account/o-example/{master_id}",
            "MasterAccountId": master_id,
            "MasterAccountEmail": "owner@example.com",
        }
    }


def test_management_account_is_detected(stubbed_client):
    _, stubber = stubbed_client("organizations")
    stubber.add_response("describe_organization", organization("111111111111"), {})

    assert org.detect_management("111111111111", CREDS, stubbed_client.factory) is True


def test_member_account_is_detected(stubbed_client):
    _, stubber = stubbed_client("organizations")
    stubber.add_response("describe_organization", organization("111111111111"), {})

    assert org.detect_management("222222222222", CREDS, stubbed_client.factory) is False


def test_access_denied_is_inconclusive(stubbed_client):
    _, stubber = stubbed_client("organizations")
    stubber.add_client_error("describe_organization", service_error_code="AccessDeniedException")

    assert org.detect_management("111111111111", CREDS, stubbed_client.factory) is None


def test_organizations_not_in_use_is_inconclusive(stubbed_client):
    _, stubber = stubbed_client("organizations")
    stubber.add_client_error(
        "describe_organization", service_error_code="AWSOrganizationsNotInUseException"
    )

    assert org.detect_management("111111111111", CREDS, stubbed_client.factory) is None


def test_int_account_id_matches_string_master_account_id(stubbed_client):
    """account_id may arrive as an int from config; comparison must not be type-sensitive."""
    _, stubber = stubbed_client("organizations")
    stubber.add_response("describe_organization", organization("111111111111"), {})

    assert org.detect_management(111111111111, CREDS, stubbed_client.factory) is True


def test_non_string_master_account_id_matches_string_account_id():
    """The Organizations output shape declares MasterAccountId as a string, so Stubber
    rejects a non-string value (botocore.exceptions.ParamValidationError) and this
    direction of the coercion cannot be exercised through Stubber. Exercised here
    instead with a plain fake client that bypasses botocore's response validation.
    """

    class FakeClient:
        def describe_organization(self):
            return {"Organization": {"MasterAccountId": 111111111111}}

    def fake_factory(service, region=None, credentials=None):
        assert service == "organizations"
        return FakeClient()

    assert org.detect_management("111111111111", CREDS, fake_factory) is True


def test_management_account_id_reports_the_id_itself(stubbed_client):
    """detect_management answers "is this the one?"; verification needs to be able
    to say *which* one AWS named, so it can report a contradiction."""
    _, stubber = stubbed_client("organizations")
    stubber.add_response("describe_organization", organization("111111111111"), {})

    assert org.management_account_id(CREDS, stubbed_client.factory) == "111111111111"


def test_management_account_id_is_none_when_the_api_will_not_say(stubbed_client):
    _, stubber = stubbed_client("organizations")
    stubber.add_client_error("describe_organization", service_error_code="AccessDeniedException")

    assert org.management_account_id(CREDS, stubbed_client.factory) is None


def test_management_account_id_coerces_a_non_string_id(stubbed_client):
    """Same coercion detect_management does; Stubber cannot produce a non-string
    MasterAccountId (the output shape declares it a string), so use a plain fake."""

    class FakeClient:
        def describe_organization(self):
            return {"Organization": {"MasterAccountId": 111111111111}}

    assert org.management_account_id(CREDS, lambda *a, **k: FakeClient()) == "111111111111"


def test_management_account_id_via_sso_probes_the_named_role(stubbed_client):
    """The probe fetches credentials for one Identity Center role and asks
    Organizations from there: a member account may call DescribeOrganization, so
    any role with the permission can name the management account."""
    _, sso_stubber = stubbed_client("sso")
    sso_stubber.add_response(
        "get_role_credentials",
        {
            "roleCredentials": {
                "accessKeyId": "ASIAEXAMPLE",
                "secretAccessKey": "secret",
                "sessionToken": "token",
            }
        },
        {"roleName": "ReadOnly", "accountId": "111111111111", "accessToken": "tok"},
    )
    _, org_stubber = stubbed_client("organizations")
    org_stubber.add_response("describe_organization", organization("111111111111"), {})

    observed = org.management_account_id_via_sso(
        SESSION, "tok", "111111111111", "ReadOnly", stubbed_client.factory
    )

    assert observed == "111111111111"
    # The probe must reach Organizations with the role's own credentials, not
    # whatever the ambient environment would have supplied.
    assert ("organizations", "eu-west-1", CREDS) not in stubbed_client.calls
    probe = [call for call in stubbed_client.calls if call[0] == "organizations"][0]
    assert probe[2] is not None and probe[2].access_key_id == "ASIAEXAMPLE"
    sso_stubber.assert_no_pending_responses()
    org_stubber.assert_no_pending_responses()


def test_management_account_id_via_sso_is_none_when_the_role_is_denied(stubbed_client):
    """A scoped-down role cannot answer, and that is not fatal: unknown, so the
    caller keeps whatever the user said rather than contradicting it."""
    _, sso_stubber = stubbed_client("sso")
    sso_stubber.add_response(
        "get_role_credentials",
        {"roleCredentials": {"accessKeyId": "ASIAEXAMPLE", "secretAccessKey": "secret"}},
        {"roleName": "Scoped", "accountId": "111111111111", "accessToken": "tok"},
    )
    _, org_stubber = stubbed_client("organizations")
    org_stubber.add_client_error(
        "describe_organization", service_error_code="AccessDeniedException"
    )

    observed = org.management_account_id_via_sso(
        SESSION, "tok", "111111111111", "Scoped", stubbed_client.factory
    )

    assert observed is None


def test_management_account_id_via_sso_is_none_when_the_token_is_refused(stubbed_client):
    """An expired or rejected Identity Center token makes the probe impossible,
    which is still "unknown" rather than an error that aborts a sync."""
    _, sso_stubber = stubbed_client("sso")
    sso_stubber.add_client_error(
        "get_role_credentials", service_error_code="UnauthorizedException"
    )

    observed = org.management_account_id_via_sso(
        SESSION, "tok", "111111111111", "ReadOnly", stubbed_client.factory
    )

    assert observed is None


def test_missing_master_account_id_is_inconclusive(stubbed_client):
    _, stubber = stubbed_client("organizations")
    stubber.add_response(
        "describe_organization",
        {
            "Organization": {
                "Id": "o-example",
                "Arn": "arn:aws:organizations::111111111111:organization/o-example",
                "FeatureSet": "ALL",
            }
        },
        {},
    )

    assert org.detect_management("111111111111", CREDS, stubbed_client.factory) is None
