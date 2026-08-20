import boto3
import pytest
from botocore.stub import Stubber


@pytest.fixture
def stubbed_client():
    """Build a stubbed boto3 client plus a client_factory that returns it."""
    created = {}
    calls = []

    def build(service, region="eu-west-1"):
        client = boto3.client(
            service,
            region_name=region,
            aws_access_key_id="stub",
            aws_secret_access_key="stub",
            aws_session_token="stub",
        )
        stubber = Stubber(client)
        stubber.activate()
        created[service] = (client, stubber)
        return client, stubber

    def factory(service, region=None, credentials=None):
        calls.append((service, region, credentials))
        if service not in created:
            build(service, region or "eu-west-1")
        return created[service][0]

    build.factory = factory
    build.calls = calls
    yield build
    for _, stubber in created.values():
        stubber.deactivate()
