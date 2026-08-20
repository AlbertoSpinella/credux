import pytest

from credux.config import Config
from credux.errors import UsageError
from credux.model import Credential, Registry, new_id


def make_registry(credux_home, profiles):
    config = Config.load()
    registry = Registry(config)
    for profile in profiles:
        registry.add(
            Credential(id=new_id(), kind="iam-user", profile=profile, params={"user": "ada"})
        )
    return registry


def test_new_id_is_unique_and_short():
    ids = {new_id() for _ in range(100)}

    assert len(ids) == 100
    assert all(len(value) == 12 for value in ids)


def test_credential_round_trips_through_dict():
    cred = Credential(id="abc", kind="sso", profile="p", region="eu-west-1", params={"a": 1})

    assert Credential.from_dict(cred.to_dict()) == cred


def test_add_writes_through_to_config(credux_home):
    registry = make_registry(credux_home, ["acme-billing-User-ada"])

    assert registry.config.credentials[0]["profile"] == "acme-billing-User-ada"


def test_resolve_matches_exact_name(credux_home):
    registry = make_registry(credux_home, ["acme-dev-User-ada", "acme-dev-User-adam"])

    assert registry.resolve("acme-dev-User-ada").profile == "acme-dev-User-ada"


def test_resolve_accepts_unique_prefix(credux_home):
    registry = make_registry(credux_home, ["acme-dev-User-ada", "beta-dev-User-bob"])

    assert registry.resolve("beta").profile == "beta-dev-User-bob"


def test_resolve_accepts_a_unique_substring_from_the_middle(credux_home):
    registry = make_registry(credux_home, ["acme-dev-User-ada", "beta-billing-User-bob"])

    assert registry.resolve("billing").profile == "beta-billing-User-bob"


def test_search_finds_every_substring_match_sorted(credux_home):
    registry = make_registry(
        credux_home, ["beta-svinet-User-bob", "acme-svinet-User-ada", "acme-dev-User-ada"]
    )

    assert [cred.profile for cred in registry.search("svi")] == [
        "acme-svinet-User-ada",
        "beta-svinet-User-bob",
    ]


def test_search_ignores_case(credux_home):
    registry = make_registry(credux_home, ["acme-dev-SSO-Admin"])

    assert [cred.profile for cred in registry.search("sso-admin")] == ["acme-dev-SSO-Admin"]


def test_search_prefers_an_exact_match_over_its_own_substring_matches(credux_home):
    """'acme-dev' must not be reported as ambiguous just because a longer
    profile contains it: an exactly matching name is never a search term."""
    registry = make_registry(credux_home, ["acme-dev", "acme-dev-User-ada"])

    assert [cred.profile for cred in registry.search("acme-dev")] == ["acme-dev"]


def test_search_returns_nothing_when_no_profile_contains_the_term(credux_home):
    registry = make_registry(credux_home, ["acme-dev-User-ada"])

    assert registry.search("nothing-like-this") == []


def test_resolve_rejects_ambiguous_prefix(credux_home):
    registry = make_registry(credux_home, ["acme-dev-User-ada", "acme-prod-User-ada"])

    with pytest.raises(UsageError) as excinfo:
        registry.resolve("acme")

    assert "acme-dev-User-ada" in str(excinfo.value)
    assert "acme-prod-User-ada" in str(excinfo.value)


def test_resolve_reports_unknown_name(credux_home):
    registry = make_registry(credux_home, ["acme-dev-User-ada"])

    with pytest.raises(UsageError):
        registry.resolve("nothing-like-this")


def test_add_rejects_duplicate_profile(credux_home):
    registry = make_registry(credux_home, ["acme-dev-User-ada"])

    with pytest.raises(UsageError):
        registry.add(Credential(id=new_id(), kind="iam-user", profile="acme-dev-User-ada"))


def test_remove_then_save_persists(credux_home):
    registry = make_registry(credux_home, ["acme-dev-User-ada"])
    cred_id = registry.all()[0].id

    registry.remove(cred_id)
    registry.save()

    assert Registry(Config.load()).all() == []


def test_add_rejects_duplicate_id(credux_home):
    registry = make_registry(credux_home, ["acme-dev-User-ada"])
    cred_id = registry.all()[0].id

    with pytest.raises(UsageError):
        registry.add(Credential(id=cred_id, kind="iam-user", profile="acme-dev-User-other"))


def test_by_id_returns_credential(credux_home):
    registry = make_registry(credux_home, ["acme-dev-User-ada"])
    cred_id = registry.all()[0].id

    assert registry.by_id(cred_id).profile == "acme-dev-User-ada"


def test_by_id_reports_unknown_id(credux_home):
    registry = make_registry(credux_home, ["acme-dev-User-ada"])

    with pytest.raises(UsageError):
        registry.by_id("does-not-exist")


def test_replace_updates_in_place_and_survives_save(credux_home):
    registry = make_registry(credux_home, ["acme-dev-User-ada"])
    cred = registry.all()[0]
    cred.region = "eu-west-1"

    registry.replace(cred)
    registry.save()

    reloaded = Registry(Config.load()).by_id(cred.id)
    assert reloaded.region == "eu-west-1"


def test_replace_unknown_id_raises(credux_home):
    registry = make_registry(credux_home, ["acme-dev-User-ada"])

    # A novel profile name, so the collision branch cannot fire first and let
    # this pass without ever reaching the unknown-id branch it is named for.
    with pytest.raises(UsageError) as excinfo:
        registry.replace(
            Credential(id="no-such-id", kind="iam-user", profile="acme-dev-User-novel")
        )

    assert "no-such-id" in str(excinfo.value)


def test_replace_rejects_profile_collision_with_different_credential(credux_home):
    registry = make_registry(
        credux_home, ["acme-dev-User-ada", "acme-dev-User-adam"]
    )
    cred = registry.by_profile("acme-dev-User-adam")
    cred.profile = "acme-dev-User-ada"

    with pytest.raises(UsageError):
        registry.replace(cred)


def test_replace_allows_unchanged_profile(credux_home):
    registry = make_registry(credux_home, ["acme-dev-User-ada"])
    cred = registry.all()[0]
    cred.params["user"] = "adam"

    registry.replace(cred)

    assert registry.by_id(cred.id).params["user"] == "adam"


def test_mutating_returned_credential_params_does_not_persist_without_replace(credux_home):
    registry = make_registry(credux_home, ["acme-dev-User-ada"])
    cred = registry.by_id(registry.all()[0].id)

    cred.params["role_arn"] = "evil"
    registry.save()

    reloaded = Registry(Config.load()).by_id(cred.id)
    assert "role_arn" not in reloaded.params
