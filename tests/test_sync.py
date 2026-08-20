import pytest

from credux import sync
from credux.config import Config
from credux.errors import ConfigError
from credux.model import Credential, Registry, new_id
from credux.providers.aws.sso import DiscoveredRole

SESSION = {"start_url": "https://acme.awsapps.com/start", "sso_region": "eu-west-1"}


def config_with_session(**extra):
    config = Config()
    config.sso_sessions["acme"] = {**SESSION, **extra}
    return config


def existing(profile, account_id="111111111111", role="ReadOnly"):
    return Credential(
        id=new_id(),
        kind="sso",
        profile=profile,
        region="eu-west-1",
        params={
            "sso_session": "acme",
            "account_id": account_id,
            "account_name": "billing",
            "role_name": role,
        },
    )


def test_new_roles_are_created_with_template_names():
    config = config_with_session()
    registry = Registry(config)

    plan = sync.plan_sso_sync(
        config,
        registry,
        "acme",
        [DiscoveredRole("111111111111", "billing", "ReadOnly")],
    )

    assert [cred.profile for cred in plan.created] == ["acme-billing-SSO-ReadOnly"]
    assert plan.created[0].params["account_id"] == "111111111111"
    assert plan.created[0].region == "eu-west-1"


def test_management_account_gets_the_prefix():
    config = config_with_session(management_account_id="111111111111")
    registry = Registry(config)

    plan = sync.plan_sso_sync(
        config,
        registry,
        "acme",
        [DiscoveredRole("111111111111", "platform", "Admin")],
    )

    assert plan.created[0].profile == "acme-management_platform-SSO-Admin"


def test_an_explicit_management_account_id_is_used_when_config_has_none():
    """`credux sync` discovers the management account at run time (asking, then
    verifying against Organizations), so the planner has to accept an id that
    appears nowhere in config.json."""
    config = config_with_session()
    registry = Registry(config)

    plan = sync.plan_sso_sync(
        config,
        registry,
        "acme",
        [DiscoveredRole("111111111111", "platform", "Admin")],
        management_account_id="111111111111",
    )

    assert plan.created[0].profile == "acme-management_platform-SSO-Admin"


def test_an_explicit_management_account_id_overrides_the_configured_one():
    config = config_with_session(management_account_id="222222222222")
    registry = Registry(config)

    plan = sync.plan_sso_sync(
        config,
        registry,
        "acme",
        [
            DiscoveredRole("111111111111", "platform", "Admin"),
            DiscoveredRole("222222222222", "billing", "Admin"),
        ],
        management_account_id="111111111111",
    )

    profiles = sorted(cred.profile for cred in plan.created)
    assert profiles == ["acme-billing-SSO-Admin", "acme-management_platform-SSO-Admin"]


def test_no_explicit_management_account_id_still_falls_back_to_config():
    """Callers other than the CLI (doctor's fixtures, a script) pass nothing and
    must keep getting the configured behaviour."""
    config = config_with_session(management_account_id="111111111111")
    registry = Registry(config)

    plan = sync.plan_sso_sync(
        config,
        registry,
        "acme",
        [DiscoveredRole("111111111111", "platform", "Admin")],
        management_account_id=None,
    )

    assert plan.created[0].profile == "acme-management_platform-SSO-Admin"


def test_unchanged_roles_are_left_alone():
    config = config_with_session()
    registry = Registry(config)
    registry.add(existing("acme-billing-SSO-ReadOnly"))

    plan = sync.plan_sso_sync(
        config, registry, "acme", [DiscoveredRole("111111111111", "billing", "ReadOnly")]
    )

    assert plan.created == [] and plan.renamed == [] and len(plan.unchanged) == 1


def test_a_template_change_renames_instead_of_duplicating():
    config = config_with_session()
    config.naming["iam_sso"] = "{org}_{account_name}_{role}"
    registry = Registry(config)
    registry.add(existing("acme-billing-SSO-ReadOnly"))

    plan = sync.plan_sso_sync(
        config, registry, "acme", [DiscoveredRole("111111111111", "billing", "ReadOnly")]
    )

    assert plan.created == []
    assert plan.renamed[0][1] == "acme-billing-SSO-ReadOnly"
    assert plan.renamed[0][0].profile == "acme_billing_ReadOnly"


def test_roles_that_vanished_are_listed_for_removal():
    config = config_with_session()
    registry = Registry(config)
    registry.add(existing("acme-billing-SSO-ReadOnly"))

    plan = sync.plan_sso_sync(config, registry, "acme", [])

    assert [cred.profile for cred in plan.removed] == ["acme-billing-SSO-ReadOnly"]


def test_credentials_from_other_sessions_are_untouched():
    config = config_with_session()
    config.sso_sessions["beta"] = {
        "start_url": "https://beta.awsapps.com/start",
        "sso_region": "eu-west-1",
    }
    registry = Registry(config)
    other = existing("beta-billing-SSO-ReadOnly")
    other.params["sso_session"] = "beta"
    registry.add(other)

    plan = sync.plan_sso_sync(config, registry, "acme", [])

    assert plan.removed == []


def test_apply_creates_and_renames_but_prunes_only_on_request():
    config = config_with_session()
    registry = Registry(config)
    registry.add(existing("acme-billing-SSO-ReadOnly"))
    plan = sync.plan_sso_sync(
        config, registry, "acme", [DiscoveredRole("222222222222", "platform", "Admin")]
    )

    sync.apply_plan(registry, plan, prune=False)
    assert sorted(cred.profile for cred in registry.all()) == [
        "acme-billing-SSO-ReadOnly",
        "acme-platform-SSO-Admin",
    ]

    sync.apply_plan(registry, plan, prune=True)
    assert [cred.profile for cred in registry.all()] == ["acme-platform-SSO-Admin"]


def test_a_direct_name_swap_between_two_credentials_applies_correctly():
    config = config_with_session()
    registry = Registry(config)
    alpha = existing("acme-alpha-SSO-Role", account_id="111111111111", role="Role")
    alpha.params["account_name"] = "alpha"
    beta = existing("acme-beta-SSO-Role", account_id="222222222222", role="Role")
    beta.params["account_name"] = "beta"
    registry.add(alpha)
    registry.add(beta)

    # The two accounts traded friendly names, so each credential now wants
    # the profile name the other one currently holds.
    plan = sync.plan_sso_sync(
        config,
        registry,
        "acme",
        [
            DiscoveredRole("111111111111", "beta", "Role"),
            DiscoveredRole("222222222222", "alpha", "Role"),
        ],
    )
    assert {profile for _cred, profile in plan.renamed} == {
        "acme-alpha-SSO-Role",
        "acme-beta-SSO-Role",
    }

    sync.apply_plan(registry, plan, prune=False)

    by_account = {cred.params["account_id"]: cred.profile for cred in registry.all()}
    assert by_account == {
        "111111111111": "acme-beta-SSO-Role",
        "222222222222": "acme-alpha-SSO-Role",
    }
    assert not any("credux-sync-tmp" in cred.profile for cred in registry.all())


def test_a_discovery_duplicate_produces_one_created_entry():
    config = config_with_session()
    registry = Registry(config)

    plan = sync.plan_sso_sync(
        config,
        registry,
        "acme",
        [
            DiscoveredRole("111111111111", "billing", "ReadOnly"),
            DiscoveredRole("111111111111", "billing", "ReadOnly"),
        ],
    )

    assert len(plan.created) == 1
    sync.apply_plan(registry, plan, prune=False)
    assert len(registry.all()) == 1


def test_a_rename_colliding_with_another_session_raises_at_plan_time():
    config = config_with_session()
    config.sso_sessions["beta"] = {
        "start_url": "https://beta.awsapps.com/start",
        "sso_region": "eu-west-1",
    }
    # A template change makes the acme role want "acme_billing_ReadOnly",
    # but a beta credential already sits on that exact name.
    config.naming["iam_sso"] = "{org}_{account_name}_{role}"
    registry = Registry(config)
    registry.add(existing("acme-billing-SSO-ReadOnly"))
    other = existing("acme_billing_ReadOnly", account_id="999999999999", role="ReadOnly")
    other.params["sso_session"] = "beta"
    registry.add(other)
    before = registry.all()

    with pytest.raises(ConfigError):
        sync.plan_sso_sync(
            config,
            registry,
            "acme",
            [DiscoveredRole("111111111111", "billing", "ReadOnly")],
        )

    assert registry.all() == before


def test_a_vanished_credential_holding_the_wanted_name_blocks_creation_without_prune():
    config = config_with_session()
    registry = Registry(config)
    # The default naming template has no {account_id}, so a different account
    # reusing the same friendly name and role collides with the profile a
    # vanished credential (not yet pruned) still holds.
    registry.add(existing("acme-billing-SSO-ReadOnly", account_id="111111111111"))
    before = registry.all()

    with pytest.raises(ConfigError):
        sync.plan_sso_sync(
            config,
            registry,
            "acme",
            [DiscoveredRole("999999999999", "billing", "ReadOnly")],
        )

    assert registry.all() == before


def test_two_discovered_roles_rendering_to_one_name_raise_at_plan_time():
    config = config_with_session()
    registry = Registry(config)

    with pytest.raises(ConfigError):
        sync.plan_sso_sync(
            config,
            registry,
            "acme",
            [
                DiscoveredRole("111111111111", "billing", "ReadOnly"),
                DiscoveredRole("999999999999", "billing", "ReadOnly"),
            ],
        )

    assert registry.all() == []


class _FlakyRegistry:
    """Wraps a real Registry, failing one specific `replace` call.

    Everything else passes straight through to the wrapped registry, so this
    stands in for a registry that starts misbehaving partway through a
    sequence of writes (for example because something external changed the
    underlying config concurrently).
    """

    def __init__(self, inner, fail_on_call):
        self._inner = inner
        self._fail_on_call = fail_on_call
        self._calls = 0

    def replace(self, cred):
        self._calls += 1
        if self._calls == self._fail_on_call:
            raise RuntimeError("simulated failure")
        return self._inner.replace(cred)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_a_phase_two_failure_leaves_no_placeholder_names_behind():
    config = config_with_session()
    real_registry = Registry(config)
    alpha = existing("acme-old-alpha", account_id="111111111111", role="Role")
    alpha.params["account_name"] = "old-alpha"
    beta = existing("acme-old-beta", account_id="222222222222", role="Role")
    beta.params["account_name"] = "old-beta"
    real_registry.add(alpha)
    real_registry.add(beta)

    plan = sync.plan_sso_sync(
        config,
        real_registry,
        "acme",
        [
            DiscoveredRole("111111111111", "new-alpha", "Role"),
            DiscoveredRole("222222222222", "new-beta", "Role"),
        ],
    )
    assert len(plan.renamed) == 2

    # 2 placeholder writes succeed, then fail on the second final-name write
    # so one credential has already reached its final name when it blows up.
    flaky = _FlakyRegistry(real_registry, fail_on_call=4)
    with pytest.raises(RuntimeError):
        sync.apply_plan(flaky, plan, prune=False)

    profiles = sorted(cred.profile for cred in real_registry.all())
    assert profiles == ["acme-old-alpha", "acme-old-beta"]
    assert not any("credux-sync-tmp" in profile for profile in profiles)
