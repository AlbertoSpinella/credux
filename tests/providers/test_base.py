from datetime import datetime, timezone

import pytest

from credux.config import Config
from credux.errors import ResolutionError
from credux.model import Credential, Registry
from credux.providers import base


class CountingProvider(base.Provider):
    name = "fake"
    kinds = ("fake-kind", "chain")

    def __init__(self):
        self.calls = 0

    def resolve(self, cred, ctx):
        self.calls += 1
        if cred.kind == "chain":
            ctx.resolve_id(cred.params["source"])
        return base.ResolvedCredentials("AKIA", "secret")


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(base, "_PROVIDERS", {})
    instance = CountingProvider()
    base.register(instance)
    return instance


def context_with(credentials):
    config = Config()
    registry = Registry(config)
    for cred in credentials:
        registry.add(cred)
    return base.ResolutionContext(config, registry), registry


def test_register_then_get(provider):
    assert base.get_provider("fake") is provider


def test_get_unknown_provider_fails(provider):
    with pytest.raises(ResolutionError):
        base.get_provider("nope")


def test_provider_for_uses_the_credential_provider_field(provider):
    cred = Credential(id="a", kind="fake-kind", profile="p", provider="fake")

    assert base.provider_for(cred) is provider


def test_resolution_is_cached_per_credential(provider):
    cred = Credential(id="a", kind="fake-kind", profile="p", provider="fake")
    ctx, _ = context_with([cred])

    ctx.resolve(cred)
    ctx.resolve(cred)

    assert provider.calls == 1


def test_resolution_detects_cycles(provider):
    first = Credential(id="a", kind="chain", profile="a", provider="fake", params={"source": "b"})
    second = Credential(id="b", kind="chain", profile="b", provider="fake", params={"source": "a"})
    ctx, _ = context_with([first, second])

    with pytest.raises(ResolutionError) as excinfo:
        ctx.resolve(first)

    assert "cycle" in str(excinfo.value).lower()


def test_as_boto_kwargs_omits_absent_token():
    resolved = base.ResolvedCredentials("AKIA", "secret")

    assert resolved.as_boto_kwargs() == {
        "aws_access_key_id": "AKIA",
        "aws_secret_access_key": "secret",
    }


def test_from_sts_payload_maps_fields():
    expires = datetime(2026, 1, 1, tzinfo=timezone.utc)
    resolved = base.from_sts_payload(
        {
            "AccessKeyId": "ASIA",
            "SecretAccessKey": "s",
            "SessionToken": "t",
            "Expiration": expires,
        },
        region="eu-west-1",
    )

    assert (resolved.access_key_id, resolved.session_token, resolved.expiration) == (
        "ASIA",
        "t",
        expires,
    )
    assert resolved.region == "eu-west-1"


def test_mfa_prompt_missing_is_an_actionable_error(provider):
    ctx, _ = context_with([])

    with pytest.raises(ResolutionError) as excinfo:
        ctx.mfa_code("arn:aws:iam::123456789012:mfa/device")

    assert "--mfa-code" in str(excinfo.value)


def test_default_client_factory_is_read_from_the_module_at_call_time(monkeypatch):
    """No client_factory= is passed to ResolutionContext() here: this is the
    call shape a caller uses when it has no fake to inject. A module-level
    patch of base.default_client_factory must be enough to intercept it - if
    `client_factory` were still a parameter default bound at import time,
    this patch would have no effect and a real boto3.client(...) call would
    run instead."""
    calls = []
    monkeypatch.setattr(
        base, "default_client_factory",
        lambda *args, **kwargs: calls.append((args, kwargs)) or "fake-client",
    )
    config = Config()

    ctx = base.ResolutionContext(config, Registry(config))
    result = ctx.client_factory("sts", region="eu-west-1")

    assert result == "fake-client"
    assert calls == [(("sts",), {"region": "eu-west-1"})]
