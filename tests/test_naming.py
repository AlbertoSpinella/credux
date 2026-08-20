import pytest

from credux import naming
from credux.config import DEFAULT_NAMING
from credux.errors import ConfigError


def test_required_fields_lists_template_variables():
    assert naming.required_fields("{org}-{account_name}-User-{user}") == {
        "org",
        "account_name",
        "user",
    }


def test_render_substitutes_values():
    assert naming.render("{org}-{role}", {"org": "acme", "role": "Admin"}) == "acme-Admin"


def test_render_rejects_unknown_variable():
    with pytest.raises(ConfigError) as excinfo:
        naming.render("{customer}-{role}", {"role": "Admin"})

    assert "customer" in str(excinfo.value)


def test_render_rejects_empty_value():
    with pytest.raises(ConfigError) as excinfo:
        naming.render("{org}-{role}", {"org": "", "role": "Admin"})

    assert "org" in str(excinfo.value)


def test_profile_name_uses_the_template_for_the_kind():
    name = naming.profile_name(
        "iam-user",
        DEFAULT_NAMING,
        {"org": "acme", "account_name": "billing", "user": "ada"},
    )

    assert name == "acme-billing-User-ada"


def test_profile_name_prefixes_management_accounts():
    name = naming.profile_name(
        "iam-role",
        DEFAULT_NAMING,
        {"org": "acme", "account_name": "platform", "role": "Admin"},
        management=True,
    )

    assert name == "acme-management_platform-Role-Admin"


def test_profile_name_rejects_unknown_kind():
    with pytest.raises(ConfigError):
        naming.profile_name("carrier-pigeon", DEFAULT_NAMING, {})


def test_subdomain_from_start_url():
    assert naming.subdomain_from_start_url("https://acme.awsapps.com/start") == "acme"
    assert naming.subdomain_from_start_url("https://acme.awsapps.com/start/#/") == "acme"


def test_subdomain_from_start_url_rejects_garbage():
    with pytest.raises(ConfigError):
        naming.subdomain_from_start_url("not-a-url")


def test_render_rejects_unmatched_brace():
    with pytest.raises(ConfigError) as excinfo:
        naming.render("{org-{role}", {"org": "acme", "role": "Admin"})

    assert "{org-{role}" in str(excinfo.value)


def test_render_rejects_empty_replacement_field():
    with pytest.raises(ConfigError) as excinfo:
        naming.render("{}-{role}", {"role": "Admin"})

    assert "{}-{role}" in str(excinfo.value)


def test_render_rejects_conversion_specifier():
    with pytest.raises(ConfigError) as excinfo:
        naming.render("{org!r}-{role}", {"org": "acme", "role": "Admin"})

    assert "org" in str(excinfo.value)


def test_render_rejects_format_spec():
    with pytest.raises(ConfigError) as excinfo:
        naming.render("{org:>10}-{role}", {"org": "acme", "role": "Admin"})

    assert "org" in str(excinfo.value)


def test_render_rejects_positional_field():
    with pytest.raises(ConfigError) as excinfo:
        naming.render("{0}-{role}", {"role": "Admin"})

    assert "0" in str(excinfo.value)


def test_validate_template_accepts_a_template_render_would_accept():
    naming.validate_template("{org}-{account_name}-SSO-{role}")


def test_validate_template_rejects_an_unknown_variable():
    with pytest.raises(ConfigError) as exc:
        naming.validate_template("{org}-{nonsense}")

    assert "nonsense" in str(exc.value)


def test_validate_template_rejects_an_empty_replacement_field():
    with pytest.raises(ConfigError):
        naming.validate_template("{org}-{}")


def test_validate_template_rejects_a_template_with_no_variables_at_all():
    """Every credential would render to the same profile name, and the second
    one added would collide with the first."""
    with pytest.raises(ConfigError):
        naming.validate_template("one-fixed-name")
