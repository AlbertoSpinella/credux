from credux.app import App
from credux.providers import base


def test_default_client_factory_is_read_from_the_module_at_call_time(credux_home, monkeypatch):
    """No client_factory= is passed to App() here: this is the call shape
    cli.py's `cli` group uses for every real invocation. A module-level
    patch of base.default_client_factory must be enough to intercept it -
    if App's client_factory parameter were still resolved through a name
    bound in app.py's own namespace at import time (rather than looked up
    fresh on the providers.base module), this patch would have no effect
    and a real boto3.client(...) call would run instead."""
    calls = []
    monkeypatch.setattr(
        base, "default_client_factory",
        lambda *args, **kwargs: calls.append((args, kwargs)) or "fake-client",
    )

    application = App()
    result = application.client_factory("sts", region="eu-west-1")

    assert result == "fake-client"
    assert calls == [(("sts",), {"region": "eu-west-1"})]
