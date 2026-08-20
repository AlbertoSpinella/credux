from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from credux import awsfiles, browser, cli as cli_module, integrations
from credux.cli import cli
from credux.config import Config
from credux.errors import ResolutionError, UsageError
from credux.model import Credential, new_id
from credux.providers import base
from credux.providers.aws.sso import DiscoveredRole
from credux.state import State
from tests.test_store import memory_keyring  # noqa: F401

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


class FakeProvider(base.Provider):
    name = "fake"
    kinds = ("fake-kind",)

    def resolve(self, cred, ctx):
        return base.ResolvedCredentials("AKIA", "secret", "token", NOW + timedelta(hours=1))

    def console_url(self, cred, ctx, resolved):
        return "https://signin.example/?token=abc"


class FakeAwsProvider(FakeProvider):
    """Stands in for the real aws provider so a sync-created 'sso' credential,
    which sync.py always stamps with provider='aws', can actually be started
    without any AWS call."""

    name = "aws"
    kinds = ("sso",)


def make_runner():
    """Keep stdout and stderr separate; click >= 8.2 does that unconditionally."""
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


@pytest.fixture
def runner(credux_home, memory_keyring, tmp_path, monkeypatch):
    monkeypatch.setattr(base, "_PROVIDERS", {})
    base.register(FakeProvider())
    monkeypatch.setenv("CREDUX_SHELL", "posix")

    config = Config.load()
    config.settings["aws_credentials_file"] = str(tmp_path / "credentials")
    config.settings["aws_config_file"] = str(tmp_path / "config")
    config.credentials.append(
        Credential(
            id="c1", kind="fake-kind", profile="acme-dev-Role-Admin",
            provider="fake", region="eu-west-1",
        ).to_dict()
    )
    config.sso_sessions["acme"] = {
        "start_url": "https://acme.awsapps.com/start",
        "sso_region": "eu-west-1",
    }
    config.save()
    return make_runner()


def test_start_materializes_the_profile(runner, tmp_path):
    result = runner.invoke(cli, ["start", "acme-dev-Role-Admin"])

    assert result.exit_code == 0, result.output
    assert "[acme-dev-Role-Admin]" in (tmp_path / "credentials").read_text(encoding="utf-8")


def test_start_accepts_a_prefix(runner, tmp_path):
    result = runner.invoke(cli, ["start", "acme-dev"])

    assert result.exit_code == 0, result.output


def _add_second_credential(provider="fake", kind="fake-kind"):
    config = Config.load()
    config.credentials.append(
        Credential(
            id="c2", kind=kind, profile="acme-test-Role-Read",
            provider=provider, region="eu-west-1",
        ).to_dict()
    )
    config.save()


def test_start_all_materializes_every_credential(runner, tmp_path):
    _add_second_credential()

    result = runner.invoke(cli, ["start", "--all"])

    assert result.exit_code == 0, result.output
    text = (tmp_path / "credentials").read_text(encoding="utf-8")
    assert "[acme-dev-Role-Admin]" in text
    assert "[acme-test-Role-Read]" in text


def test_start_all_continues_past_a_failure_and_exits_with_its_code(runner, tmp_path):
    class BrokenProvider(base.Provider):
        name = "broken"
        kinds = ("broken-kind",)

        def resolve(self, cred, ctx):
            raise ResolutionError("no material for you")

    base.register(BrokenProvider())
    _add_second_credential(provider="broken", kind="broken-kind")

    result = runner.invoke(cli, ["start", "--all"])

    assert result.exit_code == ResolutionError.exit_code, result.output
    assert "no material for you" in result.stderr
    # The healthy credential is started even though the other one failed.
    assert "[acme-dev-Role-Admin]" in (tmp_path / "credentials").read_text(encoding="utf-8")


def test_start_with_neither_a_profile_nor_all_is_a_usage_error(runner):
    result = runner.invoke(cli, ["start"])

    assert result.exit_code == UsageError.exit_code, result.output
    assert "Name at least one profile, or pass --all" in result.stderr


def test_start_rejects_a_profile_together_with_all(runner):
    result = runner.invoke(cli, ["start", "--all", "acme-dev-Role-Admin"])

    assert result.exit_code == UsageError.exit_code, result.output
    assert "--all" in result.stderr


def test_stop_removes_the_profile(runner, tmp_path):
    runner.invoke(cli, ["start", "acme-dev-Role-Admin"])

    result = runner.invoke(cli, ["stop", "acme-dev-Role-Admin"])

    assert result.exit_code == 0, result.output
    text = (tmp_path / "credentials").read_text(encoding="utf-8")
    assert "[acme-dev-Role-Admin]" not in text
    assert "aws_access_key_id = test" in text


def test_stop_all_clears_everything(runner):
    runner.invoke(cli, ["start", "acme-dev-Role-Admin"])

    result = runner.invoke(cli, ["stop", "--all"])

    assert result.exit_code == 0, result.output


def test_env_prints_only_the_shell_statement(runner):
    result = runner.invoke(cli, ["env", "acme-dev-Role-Admin"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == 'export AWS_PROFILE="acme-dev-Role-Admin"'


def test_env_unset_needs_no_profile(runner):
    result = runner.invoke(cli, ["env", "--unset"])

    assert result.stdout.strip() == "unset AWS_PROFILE"


def test_env_honours_an_explicit_shell(runner):
    result = runner.invoke(cli, ["env", "acme-dev-Role-Admin", "--shell", "powershell"])

    assert result.stdout.strip() == '$env:AWS_PROFILE = "acme-dev-Role-Admin"'


def test_console_launches_the_browser_with_the_container_url(runner, monkeypatch):
    launched = []
    monkeypatch.setattr(browser.subprocess, "Popen", lambda argv: launched.append(argv))

    result = runner.invoke(cli, ["console", "acme-dev-Role-Admin"])

    assert result.exit_code == 0, result.output
    assert launched[0][0] == "firefox"
    assert "ext+container:name=acme-dev-Role-Admin" in launched[0][1]


def test_console_never_prints_the_signin_url(runner, monkeypatch):
    monkeypatch.setattr(browser.subprocess, "Popen", lambda argv: None)

    result = runner.invoke(cli, ["console", "acme-dev-Role-Admin"])

    assert "signin.example" not in result.output


def test_login_runs_the_device_flow(runner, monkeypatch):
    called = {}

    def fake_login(session_name, session_cfg, client_factory, **kwargs):
        called["session"] = session_name
        return "tok"

    monkeypatch.setattr(cli_module.sso_login, "login", fake_login)

    result = runner.invoke(cli, ["login", "acme"])

    assert result.exit_code == 0, result.output
    assert called["session"] == "acme"


def test_sync_creates_credentials_and_runs_integrations(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module.sso_login, "token_or_raise", lambda name: "tok")
    monkeypatch.setattr(
        cli_module.sso,
        "discover",
        lambda cfg, token, factory: [DiscoveredRole("111111111111", "billing", "ReadOnly")],
    )
    config = Config.load()
    config.integrations = [
        {
            "name": "aliases",
            "template": "builtin:aliases.sh.j2",
            "dest": str(tmp_path / "aliases.sh"),
        }
    ]
    config.save()

    result = runner.invoke(cli, ["sync", "acme"])

    assert result.exit_code == 0, result.output
    profiles = [entry["profile"] for entry in Config.load().credentials]
    assert "acme-billing-SSO-ReadOnly" in profiles
    assert "acme-billing-SSO-ReadOnly" in (tmp_path / "aliases.sh").read_text(encoding="utf-8")


TWO_ACCOUNTS = [
    DiscoveredRole("111111111111", "platform", "Admin"),
    DiscoveredRole("222222222222", "billing", "ReadOnly"),
]


def stub_discovery(monkeypatch, roles=TWO_ACCOUNTS):
    monkeypatch.setattr(cli_module.sso_login, "token_or_raise", lambda name: "tok")
    monkeypatch.setattr(cli_module.sso, "discover", lambda cfg, token, factory: list(roles))


def stub_verification(monkeypatch, observed):
    """Patch the one provider-level seam the probe goes through.

    The probe itself (sso:GetRoleCredentials followed by
    organizations:DescribeOrganization) is covered against Stubber in
    tests/providers/aws/test_org.py; what these tests exercise is what the CLI
    does with each possible answer.
    """
    calls = []

    def fake(session_cfg, token, account_id, role_name, client_factory):
        calls.append((token, account_id, role_name))
        return observed

    monkeypatch.setattr(cli_module.org_detection, "management_account_id_via_sso", fake)
    return calls


def synced_profiles():
    return sorted(
        entry["profile"] for entry in Config.load().credentials if entry["kind"] == "sso"
    )


def test_sync_asks_which_account_is_the_management_account_and_verifies_it(runner, monkeypatch):
    """Discovery cannot tell which account is the management account, and guessing
    wrong renames every profile in it. So sync asks, then checks the answer against
    Organizations before using it."""
    stub_discovery(monkeypatch)
    calls = stub_verification(monkeypatch, "111111111111")

    result = runner.invoke(cli, ["sync", "acme"], input="111111111111\n")

    assert result.exit_code == 0, result.output + result.stderr
    assert synced_profiles() == ["acme-billing-SSO-ReadOnly", "acme-management_platform-SSO-Admin"]
    # The probe runs from a role in the account being verified: that account is
    # where the permission is most likely to exist, and it needs no second guess
    # about which role to borrow.
    assert calls == [("tok", "111111111111", "Admin")]


def test_sync_stops_when_organizations_contradicts_the_answer(runner, monkeypatch):
    """A wrong answer is worse than no answer: it prefixes the wrong account and,
    on the next sync with the right one, renames everything back. Report and stop."""
    stub_discovery(monkeypatch)
    stub_verification(monkeypatch, "222222222222")

    result = runner.invoke(cli, ["sync", "acme"], input="111111111111\n")

    assert result.exit_code == 3, result.output + result.stderr
    output = result.output + result.stderr
    assert "222222222222" in output and "111111111111" in output
    # Nothing may be created under a name derived from the rejected answer.
    assert synced_profiles() == []


def test_sync_trusts_the_answer_when_verification_is_impossible(runner, monkeypatch):
    """No organizations:DescribeOrganization anywhere reachable is "unknown", not
    "wrong" - org.py's contract is that a missing permission is never fatal."""
    stub_discovery(monkeypatch)
    stub_verification(monkeypatch, None)

    result = runner.invoke(cli, ["sync", "acme"], input="111111111111\n")

    assert result.exit_code == 0, result.output + result.stderr
    assert "acme-management_platform-SSO-Admin" in synced_profiles()
    assert "could not" in (result.output + result.stderr).lower()


def test_sync_skips_the_prefix_when_the_question_cannot_be_answered(runner, monkeypatch):
    """A scripted or unattended sync has nobody to ask, so it must fall back to
    today's behaviour - no prefix, a warning, exit 0 - rather than block."""
    stub_discovery(monkeypatch)
    calls = stub_verification(monkeypatch, "111111111111")

    result = runner.invoke(cli, ["sync", "acme"], input="")

    assert result.exit_code == 0, result.output + result.stderr
    assert synced_profiles() == ["acme-billing-SSO-ReadOnly", "acme-platform-SSO-Admin"]
    # Nothing to verify, so nothing is probed: no credential fetch, no API call.
    assert calls == []


def test_sync_does_not_ask_when_the_id_is_already_configured(runner, monkeypatch):
    """Asking every sync would make an unattended sync impossible to keep quiet.
    A configured id is still verified, just not re-asked."""
    stub_discovery(monkeypatch)
    calls = stub_verification(monkeypatch, "111111111111")
    config = Config.load()
    config.sso_sessions["acme"]["management_account_id"] = "111111111111"
    config.save()

    result = runner.invoke(cli, ["sync", "acme"], input="")

    assert result.exit_code == 0, result.output + result.stderr
    assert "acme-management_platform-SSO-Admin" in synced_profiles()
    assert calls == [("tok", "111111111111", "Admin")]
    assert "which account" not in (result.output + result.stderr).lower()


def test_sync_stops_when_organizations_contradicts_the_configured_id(runner, monkeypatch):
    """The same contradiction, from config rather than from an answer: config can
    rot when an organization is restructured, and silently renaming everything is
    not the way to find out."""
    stub_discovery(monkeypatch)
    stub_verification(monkeypatch, "222222222222")
    config = Config.load()
    config.sso_sessions["acme"]["management_account_id"] = "111111111111"
    config.save()

    result = runner.invoke(cli, ["sync", "acme"], input="")

    assert result.exit_code == 3, result.output + result.stderr
    assert synced_profiles() == []


def test_sync_rejects_an_answer_that_is_not_one_of_the_discovered_accounts(runner, monkeypatch):
    """An id nobody discovered cannot be probed and cannot match any account being
    named, so it can only be a typo. Say so instead of quietly accepting it."""
    stub_discovery(monkeypatch)
    calls = stub_verification(monkeypatch, "999999999999")

    result = runner.invoke(cli, ["sync", "acme"], input="999999999999\n")

    assert result.exit_code == 0, result.output + result.stderr
    assert synced_profiles() == ["acme-billing-SSO-ReadOnly", "acme-platform-SSO-Admin"]
    assert calls == []
    assert "999999999999" in (result.output + result.stderr)


def test_sync_does_not_ask_when_discovery_found_nothing(runner, monkeypatch):
    """No accounts means no account could take the prefix, so the question has no
    answer worth asking for."""
    stub_discovery(monkeypatch, roles=[])
    calls = stub_verification(monkeypatch, "111111111111")

    result = runner.invoke(cli, ["sync", "acme"], input="")

    assert result.exit_code == 0, result.output + result.stderr
    assert calls == []
    assert "which account" not in (result.output + result.stderr).lower()


def test_sync_says_so_when_the_configured_id_was_not_discovered(runner, monkeypatch):
    """A configured id outside this session's discovery cannot be probed at all, and
    that is a different reason from "the role lacks the permission". Reporting the
    permission as the cause would send the reader to fix the wrong thing."""
    stub_discovery(monkeypatch)
    calls = stub_verification(monkeypatch, "111111111111")
    config = Config.load()
    config.sso_sessions["acme"]["management_account_id"] = "999999999999"
    config.save()

    result = runner.invoke(cli, ["sync", "acme"], input="")

    assert result.exit_code == 0, result.output + result.stderr
    assert synced_profiles() == ["acme-billing-SSO-ReadOnly", "acme-platform-SSO-Admin"]
    assert calls == []
    output = (result.output + result.stderr).lower()
    assert "999999999999" in output
    assert "not discovered" in output
    assert "describeorganization" not in output


MANY_ACCOUNTS = [
    DiscoveredRole(f"{n:012d}", f"account{n:02d}", "Admin") for n in range(1, 26)
]


def test_sync_lists_every_account_on_request(runner, monkeypatch):
    """The list is summarized past ACCOUNT_LIST_LIMIT, and the management account
    can be past the cut - which left the reader with an id they could not read.
    Asking for the full list must print it and re-ask."""
    stub_discovery(monkeypatch, roles=MANY_ACCOUNTS)
    stub_verification(monkeypatch, "000000000025")

    result = runner.invoke(cli, ["sync", "acme"], input="?\n000000000025\n")

    assert result.exit_code == 0, result.output + result.stderr
    output = result.output + result.stderr
    assert "000000000025" in output
    assert "?" in output  # the summary line says how to see the rest
    assert "acme-management_account25-SSO-Admin" in synced_profiles()


def test_sync_lists_the_discovered_accounts_with_the_question(runner, monkeypatch):
    """The answer is a 12-digit id; nobody types one from memory. The list goes to
    stderr for the same reason cli._find's does."""
    stub_discovery(monkeypatch)
    stub_verification(monkeypatch, "111111111111")

    result = runner.invoke(cli, ["sync", "acme"], input="111111111111\n")

    assert "111111111111" in result.stderr
    assert "platform" in result.stderr
    assert "222222222222" in result.stderr


def test_sync_prunes_only_when_asked(runner, monkeypatch):
    monkeypatch.setattr(cli_module.sso_login, "token_or_raise", lambda name: "tok")
    monkeypatch.setattr(cli_module.sso, "discover", lambda cfg, token, factory: [])
    config = Config.load()
    config.credentials.append(
        Credential(
            id=new_id(), kind="sso", profile="acme-old-SSO-ReadOnly",
            params={"sso_session": "acme", "account_id": "9", "role_name": "ReadOnly"},
        ).to_dict()
    )
    config.save()

    runner.invoke(cli, ["sync", "acme"])
    assert any(c["profile"] == "acme-old-SSO-ReadOnly" for c in Config.load().credentials)

    runner.invoke(cli, ["sync", "acme", "--prune"])
    assert not any(c["profile"] == "acme-old-SSO-ReadOnly" for c in Config.load().credentials)


def test_sync_runs_post_sync_hooks_via_a_module_level_patch(runner, monkeypatch):
    """`sync_command` calls integrations.run_all with no runner=: this is the
    real call shape 'credux sync' uses. Patching integrations.subprocess.run
    at the module level must be enough to intercept the post_sync hook - if
    run_all's runner parameter were still a default bound at import time,
    this patch would miss it and a real shell command would run."""
    monkeypatch.setattr(cli_module.sso_login, "token_or_raise", lambda name: "tok")
    monkeypatch.setattr(cli_module.sso, "discover", lambda cfg, token, factory: [])
    calls = []

    class FakeResult:
        returncode = 0

    monkeypatch.setattr(
        integrations.subprocess, "run",
        lambda command, **kwargs: (calls.append(command), FakeResult())[1],
    )
    config = Config.load()
    config.post_sync = ["echo should-never-really-run"]
    config.save()

    result = runner.invoke(cli, ["sync", "acme"])

    assert result.exit_code == 0, result.output
    assert calls == ["echo should-never-really-run"]


def _discovering(monkeypatch, roles):
    monkeypatch.setattr(cli_module.sso_login, "token_or_raise", lambda name: "tok")
    monkeypatch.setattr(cli_module.sso, "discover", lambda cfg, token, factory: roles)


def test_sync_moves_an_active_session_when_it_renames_a_credential(
    runner, monkeypatch, tmp_path
):
    """A rename used to touch the registry only.

    `sync` identifies an Identity Center role by (session, account, role) and
    re-derives its profile name, so a naming-template edit renames the
    credential in place. State and the credentials file are keyed by profile
    name, so the old name kept real, usable material that nothing in the
    registry pointed at any more - unreachable from 'credux list' and 'credux
    stop <name>', and only cleared by 'credux stop --all'.
    """
    base.register(FakeAwsProvider())
    _discovering(monkeypatch, [DiscoveredRole("111111111111", "billing", "ReadOnly")])

    assert runner.invoke(cli, ["sync", "acme"]).exit_code == 0
    assert runner.invoke(cli, ["start", "acme-billing-SSO-ReadOnly"]).exit_code == 0
    assert "[acme-billing-SSO-ReadOnly]" in (tmp_path / "credentials").read_text(
        encoding="utf-8"
    )

    config = Config.load()
    config.naming["iam_sso"] = "{org}-{account_name}-Portal-{role}"
    config.save()

    result = runner.invoke(cli, ["sync", "acme"])

    assert result.exit_code == 0, result.output
    text = (tmp_path / "credentials").read_text(encoding="utf-8")
    assert "[acme-billing-SSO-ReadOnly]" not in text
    assert "[acme-billing-Portal-ReadOnly]" in text
    state = State.load()
    assert state.get("acme-billing-SSO-ReadOnly") is None
    assert state.get("acme-billing-Portal-ReadOnly") is not None
    # And the renamed profile is reachable through the registry again.
    assert runner.invoke(cli, ["stop", "acme-billing-Portal-ReadOnly"]).exit_code == 0


class PerAccountAwsProvider(FakeAwsProvider):
    """Material a test can trace back to the credential it came from.

    `FakeAwsProvider` hands every credential the same access key, which makes
    a rename that moves the *wrong* credential's material indistinguishable
    from one that moves the right one.
    """

    def resolve(self, cred, ctx):
        return base.ResolvedCredentials(
            f"AKIA-{cred.params['account_id']}", "secret", "token", NOW + timedelta(hours=1)
        )


def _materialized(tmp_path):
    return {
        entry.profile: entry
        for entry in awsfiles.read_managed_entries(tmp_path / "credentials")
    }


def _profiles_in_registry():
    return {entry["profile"] for entry in Config.load().credentials}


def test_sync_renaming_two_accounts_at_once_keeps_each_ones_own_credentials(
    runner, monkeypatch, tmp_path
):
    """Two AWS Organizations account renames landing in the same sync.

    Account 111 is renamed dev -> test at the same time as account 222 is
    renamed test -> staging, so the derived profile names form a chain: the
    name 111 moves onto is the name 222 is moving off. Every target is
    released within this one plan, so `plan_sso_sync`'s collision guards
    correctly stay quiet - they only reject names held from outside the plan.

    Renaming one session at a time made the second rename pick up the
    material the first had just written: `AWS_PROFILE=acme-staging-SSO-Admin`
    served account 111 while every listing said it was account 222, account
    222's live material was destroyed, and state.json pointed the new name at
    the wrong credential id, so the next `credux refresh` would make the
    mismatch permanent. It was order-dependent - the reverse discovery order
    happened to work - so it was a coin flip.
    """
    base.register(PerAccountAwsProvider())
    _discovering(monkeypatch, [
        DiscoveredRole("111111111111", "dev", "Admin"),
        DiscoveredRole("222222222222", "test", "Admin"),
    ])
    assert runner.invoke(cli, ["sync", "acme"]).exit_code == 0
    assert runner.invoke(cli, ["start", "acme-dev-SSO-Admin"]).exit_code == 0
    assert runner.invoke(cli, ["start", "acme-test-SSO-Admin"]).exit_code == 0

    _discovering(monkeypatch, [
        DiscoveredRole("111111111111", "test", "Admin"),
        DiscoveredRole("222222222222", "staging", "Admin"),
    ])

    result = runner.invoke(cli, ["sync", "acme"])

    assert result.exit_code == 0, result.output
    entries = _materialized(tmp_path)
    assert set(entries) == {"acme-test-SSO-Admin", "acme-staging-SSO-Admin"}
    assert entries["acme-test-SSO-Admin"].access_key_id == "AKIA-111111111111"
    assert entries["acme-staging-SSO-Admin"].access_key_id == "AKIA-222222222222"
    assert "acme-dev-SSO-Admin" not in entries

    # State and the registry must agree about which credential holds which
    # name; if they drift, `credux refresh` re-resolves the wrong one and
    # bakes the mismatch in.
    state = State.load()
    assert state.get("acme-dev-SSO-Admin") is None
    by_id = {entry["id"]: entry["profile"] for entry in Config.load().credentials}
    for profile in ("acme-test-SSO-Admin", "acme-staging-SSO-Admin"):
        session = state.get(profile)
        assert session is not None, profile
        assert by_id[session.credential_id] == profile


def test_sync_survives_two_accounts_trading_profile_names(runner, monkeypatch, tmp_path):
    """The same hazard as a straight swap: 111 dev -> test and 222 test -> dev."""
    base.register(PerAccountAwsProvider())
    _discovering(monkeypatch, [
        DiscoveredRole("111111111111", "dev", "Admin"),
        DiscoveredRole("222222222222", "test", "Admin"),
    ])
    assert runner.invoke(cli, ["sync", "acme"]).exit_code == 0
    runner.invoke(cli, ["start", "acme-dev-SSO-Admin"])
    runner.invoke(cli, ["start", "acme-test-SSO-Admin"])

    _discovering(monkeypatch, [
        DiscoveredRole("111111111111", "test", "Admin"),
        DiscoveredRole("222222222222", "dev", "Admin"),
    ])

    result = runner.invoke(cli, ["sync", "acme"])

    assert result.exit_code == 0, result.output
    entries = _materialized(tmp_path)
    assert set(entries) == {"acme-dev-SSO-Admin", "acme-test-SSO-Admin"}
    assert entries["acme-test-SSO-Admin"].access_key_id == "AKIA-111111111111"
    assert entries["acme-dev-SSO-Admin"].access_key_id == "AKIA-222222222222"
    state = State.load()
    by_id = {entry["id"]: entry["profile"] for entry in Config.load().credentials}
    for profile in ("acme-dev-SSO-Admin", "acme-test-SSO-Admin"):
        assert by_id[state.get(profile).credential_id] == profile


def test_sync_survives_a_three_way_rename_chain(runner, monkeypatch, tmp_path):
    """A -> B, B -> C, C -> A, all in one sync."""
    base.register(PerAccountAwsProvider())
    _discovering(monkeypatch, [
        DiscoveredRole("111111111111", "a", "Admin"),
        DiscoveredRole("222222222222", "b", "Admin"),
        DiscoveredRole("333333333333", "c", "Admin"),
    ])
    assert runner.invoke(cli, ["sync", "acme"]).exit_code == 0
    for name in ("a", "b", "c"):
        runner.invoke(cli, ["start", f"acme-{name}-SSO-Admin"])

    _discovering(monkeypatch, [
        DiscoveredRole("111111111111", "b", "Admin"),
        DiscoveredRole("222222222222", "c", "Admin"),
        DiscoveredRole("333333333333", "a", "Admin"),
    ])

    result = runner.invoke(cli, ["sync", "acme"])

    assert result.exit_code == 0, result.output
    entries = _materialized(tmp_path)
    assert entries["acme-b-SSO-Admin"].access_key_id == "AKIA-111111111111"
    assert entries["acme-c-SSO-Admin"].access_key_id == "AKIA-222222222222"
    assert entries["acme-a-SSO-Admin"].access_key_id == "AKIA-333333333333"
    state = State.load()
    by_id = {entry["id"]: entry["profile"] for entry in Config.load().credentials}
    for profile in ("acme-a-SSO-Admin", "acme-b-SSO-Admin", "acme-c-SSO-Admin"):
        assert by_id[state.get(profile).credential_id] == profile


def test_sync_handles_a_rename_and_a_prune_in_the_same_run(runner, monkeypatch, tmp_path):
    base.register(PerAccountAwsProvider())
    _discovering(monkeypatch, [
        DiscoveredRole("111111111111", "dev", "Admin"),
        DiscoveredRole("222222222222", "test", "Admin"),
    ])
    assert runner.invoke(cli, ["sync", "acme"]).exit_code == 0
    runner.invoke(cli, ["start", "acme-dev-SSO-Admin"])
    runner.invoke(cli, ["start", "acme-test-SSO-Admin"])

    # 111 is renamed dev -> prod; 222 vanishes from discovery entirely.
    _discovering(monkeypatch, [DiscoveredRole("111111111111", "prod", "Admin")])

    result = runner.invoke(cli, ["sync", "acme", "--prune"])

    assert result.exit_code == 0, result.output
    entries = _materialized(tmp_path)
    assert set(entries) == {"acme-prod-SSO-Admin"}
    assert entries["acme-prod-SSO-Admin"].access_key_id == "AKIA-111111111111"
    state = State.load()
    assert state.get("acme-dev-SSO-Admin") is None
    assert state.get("acme-test-SSO-Admin") is None
    assert _profiles_in_registry() == {"acme-dev-Role-Admin", "acme-prod-SSO-Admin"}
    by_id = {entry["id"]: entry["profile"] for entry in Config.load().credentials}
    assert by_id[state.get("acme-prod-SSO-Admin").credential_id] == "acme-prod-SSO-Admin"


def test_sync_reconciles_a_renamed_profile_that_is_materialized_but_not_in_state(
    runner, monkeypatch, tmp_path
):
    """`_write` writes the credentials file before state.json, deliberately,
    so a crash in between - or a user deleting state.json - leaves real,
    usable material on disk with no state entry. The old call site guarded on
    state alone and skipped exactly that case, leaving the orphan behind under
    a name the registry no longer knows."""
    base.register(PerAccountAwsProvider())
    _discovering(monkeypatch, [DiscoveredRole("111111111111", "billing", "ReadOnly")])
    assert runner.invoke(cli, ["sync", "acme"]).exit_code == 0
    assert runner.invoke(cli, ["start", "acme-billing-SSO-ReadOnly"]).exit_code == 0
    State.load().path.unlink()
    assert "acme-billing-SSO-ReadOnly" in _materialized(tmp_path)

    config = Config.load()
    config.naming["iam_sso"] = "{org}-{account_name}-Portal-{role}"
    config.save()

    result = runner.invoke(cli, ["sync", "acme"])

    assert result.exit_code == 0, result.output
    entries = _materialized(tmp_path)
    assert "acme-billing-SSO-ReadOnly" not in entries
    assert entries["acme-billing-Portal-ReadOnly"].access_key_id == "AKIA-111111111111"


def test_sync_leaves_the_aws_files_alone_when_nothing_changes(runner, monkeypatch, tmp_path):
    base.register(FakeAwsProvider())
    _discovering(monkeypatch, [DiscoveredRole("111111111111", "billing", "ReadOnly")])
    runner.invoke(cli, ["sync", "acme"])

    result = runner.invoke(cli, ["sync", "acme"])

    assert result.exit_code == 0, result.output
    assert "already up to date" in result.output
    assert not (tmp_path / "credentials").exists()


def test_sync_prune_stops_the_session_it_removes(runner, monkeypatch, tmp_path):
    base.register(FakeAwsProvider())
    _discovering(monkeypatch, [DiscoveredRole("111111111111", "billing", "ReadOnly")])
    runner.invoke(cli, ["sync", "acme"])
    runner.invoke(cli, ["start", "acme-billing-SSO-ReadOnly"])
    _discovering(monkeypatch, [])

    result = runner.invoke(cli, ["sync", "acme", "--prune"])

    assert result.exit_code == 0, result.output
    assert "[acme-billing-SSO-ReadOnly]" not in (tmp_path / "credentials").read_text(
        encoding="utf-8"
    )
    assert State.load().get("acme-billing-SSO-ReadOnly") is None


def test_sync_reports_an_unknown_session_name(runner):
    """`login` already did this; `sync` indexed the dict directly and died
    with a bare KeyError and no output at all."""
    result = runner.invoke(cli, ["sync", "nope"])

    assert result.exit_code == 2
    assert "nope" in result.output + result.stderr
    assert "acme" in result.output + result.stderr


def test_login_reports_an_unknown_session_name(runner):
    result = runner.invoke(cli, ["login", "nope"])

    assert result.exit_code == 2
    assert "nope" in result.output + result.stderr


def test_refresh_reports_when_nothing_needs_renewing(runner):
    result = runner.invoke(cli, ["refresh"])

    assert result.exit_code == 0, result.output
    assert "nothing" in result.output.lower()


def test_doctor_exits_nonzero_on_a_failing_check(runner, monkeypatch):
    monkeypatch.setattr(
        cli_module.store, "backend_name", lambda: "keyring.backends.fail.Keyring"
    )

    result = runner.invoke(cli, ["doctor"])

    assert result.exit_code == 1
    assert "secret store" in result.output


def test_start_tells_the_user_about_the_backup_it_took(runner, tmp_path):
    (tmp_path / "credentials").write_text("[handwritten]\naws_access_key_id = real\n", "utf-8")

    result = runner.invoke(cli, ["start", "acme-dev-Role-Admin"])

    assert result.exit_code == 0, result.output
    assert "credentials.credux.bak" in result.stderr
    assert "managed block" in result.stderr


def test_start_says_nothing_about_a_backup_when_none_was_taken(runner, tmp_path):
    result = runner.invoke(cli, ["start", "acme-dev-Role-Admin"])

    assert result.exit_code == 0, result.output
    assert "credux.bak" not in result.stderr


def test_start_warns_that_a_handwritten_default_profile_is_shadowed(runner, tmp_path):
    (tmp_path / "credentials").write_text("[default]\naws_access_key_id = real\n", "utf-8")

    result = runner.invoke(cli, ["start", "acme-dev-Role-Admin"])

    assert result.exit_code == 0, result.output
    assert "[default]" in result.stderr
    assert "default_profile.enabled" in result.stderr


def test_the_backup_notice_never_quotes_the_file(runner, tmp_path):
    (tmp_path / "credentials").write_text(
        "[handwritten]\naws_secret_access_key = SUPERSECRETVALUE\n", "utf-8"
    )

    result = runner.invoke(cli, ["start", "acme-dev-Role-Admin"])

    assert "SUPERSECRETVALUE" not in result.stderr
    assert "SUPERSECRETVALUE" not in result.stdout


def test_stop_also_reports_the_backup(runner, tmp_path):
    (tmp_path / "credentials").write_text("[handwritten]\naws_access_key_id = real\n", "utf-8")

    result = runner.invoke(cli, ["stop", "--all"])

    assert result.exit_code == 0, result.output
    assert "credentials.credux.bak" in result.stderr


def test_sync_says_the_names_came_from_a_default_template(runner, monkeypatch):
    stub_discovery(monkeypatch, [DiscoveredRole("111111111111", "billing", "ReadOnly")])

    result = runner.invoke(cli, ["sync", "acme"])

    assert result.exit_code == 0, result.output
    assert "naming.iam_sso" in result.stderr
    assert result.stderr.count("credux config set naming.iam_sso") == 1


def test_sync_says_it_once_however_many_roles_it_creates(runner, monkeypatch):
    stub_discovery(monkeypatch)

    result = runner.invoke(cli, ["sync", "acme"])

    assert result.exit_code == 0, result.output
    assert result.stderr.count("credux config set naming.iam_sso") == 1


def test_sync_says_nothing_about_naming_once_the_template_is_the_user_s_own(runner, monkeypatch):
    stub_discovery(monkeypatch)
    runner.invoke(cli, ["config", "set", "naming.iam_sso", "{account_name}_{role}"])

    result = runner.invoke(cli, ["sync", "acme"])

    assert result.exit_code == 0, result.output
    assert "naming.iam_sso" not in result.stderr
