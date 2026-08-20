from datetime import datetime, timedelta, timezone

import pytest

from credux import awsfiles
from credux.config import Config
from credux.errors import ResolutionError
from credux.model import Credential, Registry
from credux.providers import base
from credux.sessions import SessionManager
from credux.state import ActiveSession, State

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


class FakeProvider(base.Provider):
    name = "fake"
    kinds = ("fake-kind",)

    def __init__(self):
        self.calls = 0

    def resolve(self, cred, ctx):
        self.calls += 1
        return base.ResolvedCredentials(
            access_key_id=f"AKIA{self.calls}",
            secret_access_key="secret",
            session_token="token",
            expiration=NOW + timedelta(hours=1),
            region=cred.region,
        )


@pytest.fixture
def manager(credux_home, tmp_path, monkeypatch):
    monkeypatch.setattr(base, "_PROVIDERS", {})
    provider = FakeProvider()
    base.register(provider)

    config = Config.load()
    config.settings["aws_credentials_file"] = str(tmp_path / "credentials")
    config.settings["aws_config_file"] = str(tmp_path / "config")
    registry = Registry(config)
    registry.add(
        Credential(id="c1", kind="fake-kind", profile="acme-dev-Role-Admin",
                   provider="fake", region="eu-west-1")
    )
    registry.add(
        Credential(id="c2", kind="fake-kind", profile="beta-dev-Role-Read",
                   provider="fake", region="eu-north-1")
    )
    ctx = base.ResolutionContext(config, registry)
    instance = SessionManager(config, registry, State.load(), ctx)
    instance.provider = provider
    return instance


def test_start_writes_the_profile_and_the_fake_default(manager):
    manager.start(manager.registry.by_id("c1"))

    text = manager.credentials_path.read_text(encoding="utf-8")
    assert "[acme-dev-Role-Admin]" in text
    assert "aws_session_token = token" in text
    assert "aws_access_key_id = test" in text


def test_starting_the_same_profile_twice_replaces_rather_than_duplicates(manager):
    manager.start(manager.registry.by_id("c1"))
    calls_after_first_start = manager.provider.calls

    manager.start(manager.registry.by_id("c1"))

    text = manager.credentials_path.read_text(encoding="utf-8")
    assert text.count("[acme-dev-Role-Admin]") == 1
    # ResolutionContext caches per credential id, so the second start does
    # not re-resolve (and would not re-prompt for MFA) unless the cache is
    # cleared first, which only `refresh` does.
    assert manager.provider.calls == calls_after_first_start


def test_start_records_state_with_expiry(manager):
    session = manager.start(manager.registry.by_id("c1"))

    assert session.profile == "acme-dev-Role-Admin"
    assert session.expires_at == (NOW + timedelta(hours=1)).isoformat()
    assert State.load().get("acme-dev-Role-Admin") is not None


def test_start_all_writes_the_credentials_file_once(manager, monkeypatch):
    # The point of start_all over a loop of start(): one write for the whole
    # batch, not one full rewrite of ~/.aws/credentials per credential.
    writes = []
    real = awsfiles.write_credentials
    monkeypatch.setattr(
        awsfiles,
        "write_credentials",
        lambda path, entries, default: (writes.append(len(entries)), real(path, entries, default))[1],
    )

    outcome = manager.start_all([manager.registry.by_id("c1"), manager.registry.by_id("c2")])

    assert [item.profile for item in outcome.started] == [
        "acme-dev-Role-Admin",
        "beta-dev-Role-Read",
    ]
    assert outcome.failures == []
    assert writes == [2]
    text = manager.credentials_path.read_text(encoding="utf-8")
    assert "[acme-dev-Role-Admin]" in text
    assert "[beta-dev-Role-Read]" in text


def test_start_all_continues_past_a_failure_and_reports_it(manager, monkeypatch):
    real = manager.provider.resolve

    def resolve(cred, ctx):
        if cred.id == "c1":
            raise ResolutionError("c1 is broken")
        return real(cred, ctx)

    monkeypatch.setattr(manager.provider, "resolve", resolve)

    outcome = manager.start_all([manager.registry.by_id("c1"), manager.registry.by_id("c2")])

    assert [item.profile for item in outcome.started] == ["beta-dev-Role-Read"]
    assert [(cred.profile, str(exc)) for cred, exc in outcome.failures] == [
        ("acme-dev-Role-Admin", "c1 is broken")
    ]
    # The one that worked is materialized; the one that failed is in neither
    # the credentials file nor state.
    text = manager.credentials_path.read_text(encoding="utf-8")
    assert "[beta-dev-Role-Read]" in text
    assert "[acme-dev-Role-Admin]" not in text
    assert State.load().get("acme-dev-Role-Admin") is None


def test_start_all_of_nothing_writes_nothing(manager):
    manager.start_all([])

    assert not manager.credentials_path.exists()


def test_start_all_keeps_already_materialized_profiles_outside_the_batch(manager):
    manager.start(manager.registry.by_id("c1"))

    manager.start_all([manager.registry.by_id("c2")])

    text = manager.credentials_path.read_text(encoding="utf-8")
    assert "[acme-dev-Role-Admin]" in text
    assert "[beta-dev-Role-Read]" in text


def test_starting_a_second_profile_keeps_the_first(manager):
    manager.start(manager.registry.by_id("c1"))
    manager.start(manager.registry.by_id("c2"))

    text = manager.credentials_path.read_text(encoding="utf-8")
    assert "[acme-dev-Role-Admin]" in text
    assert "[beta-dev-Role-Read]" in text


def test_starting_a_second_profile_preserves_the_first_despite_a_stray_end_marker(manager):
    # Regression for a bug where `read_managed_entries` searched for
    # END_MARKER from the start of the file instead of from BEGIN_MARKER's
    # index. A human-written line that happens to match END_MARKER, placed
    # before credux's real managed block, made the real block invisible to
    # `_materialized()`; `start()` for a second profile then upserted into
    # an empty list and silently dropped the first profile's credentials
    # even though state still listed it as active.
    manager.credentials_path.write_text(
        f"[handwritten]\n{awsfiles.END_MARKER}\nsome text\n", encoding="utf-8"
    )

    manager.start(manager.registry.by_id("c1"))
    manager.start(manager.registry.by_id("c2"))

    text = manager.credentials_path.read_text(encoding="utf-8")
    assert "[acme-dev-Role-Admin]" in text
    assert "[beta-dev-Role-Read]" in text


def test_start_writes_the_region_into_the_config_file(manager):
    manager.start(manager.registry.by_id("c1"))

    text = manager.config_path.read_text(encoding="utf-8")
    assert "[profile acme-dev-Role-Admin]" in text
    assert "region = eu-west-1" in text


def test_stop_removes_only_that_profile(manager):
    manager.start(manager.registry.by_id("c1"))
    manager.start(manager.registry.by_id("c2"))

    assert manager.stop("acme-dev-Role-Admin") is True

    text = manager.credentials_path.read_text(encoding="utf-8")
    assert "[acme-dev-Role-Admin]" not in text
    assert "[beta-dev-Role-Read]" in text
    assert State.load().get("acme-dev-Role-Admin") is None


def test_stop_of_an_inactive_profile_reports_false(manager):
    assert manager.stop("acme-dev-Role-Admin") is False


def test_stopping_reconciles_when_the_file_has_it_but_state_does_not(manager):
    # Divergence direction 1: the credentials file still materializes the
    # profile but state has already forgotten it (e.g. state.json was hand
    # edited, or lost independently of the file). `stop` must still clean
    # up the file rather than trusting state alone and doing nothing.
    manager.start(manager.registry.by_id("c1"))
    manager.state.clear("acme-dev-Role-Admin")

    assert manager.stop("acme-dev-Role-Admin") is True

    text = manager.credentials_path.read_text(encoding="utf-8")
    assert "[acme-dev-Role-Admin]" not in text
    assert State.load().get("acme-dev-Role-Admin") is None


def test_stopping_reconciles_when_state_has_it_but_the_file_does_not(manager):
    # Divergence direction 2: state still lists the profile as active but
    # the credentials file no longer materializes it (e.g. a hand edit, or
    # a crash between writing the file and saving state). `stop` must still
    # clear state rather than reporting false just because the file agrees
    # there is nothing to remove.
    manager.start(manager.registry.by_id("c1"))
    awsfiles.write_credentials(
        manager.credentials_path, [], manager.config.setting("default_profile")
    )

    assert manager.stop("acme-dev-Role-Admin") is True
    assert State.load().get("acme-dev-Role-Admin") is None


def test_stop_all_keeps_the_fake_default(manager):
    manager.start(manager.registry.by_id("c1"))

    assert manager.stop_all() == 1
    assert "aws_access_key_id = test" in manager.credentials_path.read_text(encoding="utf-8")


def test_refresh_renews_only_sessions_inside_the_threshold(manager):
    manager.start(manager.registry.by_id("c1"))
    manager.start(manager.registry.by_id("c2"))
    manager.state.set(
        ActiveSession(
            profile="acme-dev-Role-Admin",
            credential_id="c1",
            kind="fake-kind",
            expires_at=(NOW + timedelta(minutes=2)).isoformat(),
        )
    )
    before = manager.provider.calls

    renewed = manager.refresh(threshold_minutes=10, now=NOW)

    assert [item.profile for item in renewed] == ["acme-dev-Role-Admin"]
    assert manager.provider.calls == before + 1


def test_rename_moves_the_profile_in_both_the_file_and_state(manager):
    manager.start(manager.registry.by_id("c1"))

    assert manager.rename("acme-dev-Role-Admin", "acme-dev-Role-Admin-v2") is True

    text = manager.credentials_path.read_text(encoding="utf-8")
    assert "[acme-dev-Role-Admin]" not in text
    assert "[acme-dev-Role-Admin-v2]" in text
    assert State.load().get("acme-dev-Role-Admin") is None
    renamed = State.load().get("acme-dev-Role-Admin-v2")
    assert renamed is not None
    assert renamed.credential_id == "c1"


def test_rename_keeps_the_material_and_the_expiry(manager):
    started = manager.start(manager.registry.by_id("c1"))

    manager.rename("acme-dev-Role-Admin", "acme-dev-Role-Admin-v2")

    entries = {entry.profile: entry for entry in awsfiles.read_managed_entries(
        manager.credentials_path
    )}
    assert entries["acme-dev-Role-Admin-v2"].access_key_id == "AKIA1"
    assert entries["acme-dev-Role-Admin-v2"].session_token == "token"
    assert State.load().get("acme-dev-Role-Admin-v2").expires_at == started.expires_at


def test_rename_moves_the_region_in_the_aws_config_file(manager):
    manager.start(manager.registry.by_id("c1"))

    manager.rename("acme-dev-Role-Admin", "acme-dev-Role-Admin-v2")

    text = manager.config_path.read_text(encoding="utf-8")
    assert "[profile acme-dev-Role-Admin]" not in text
    assert "[profile acme-dev-Role-Admin-v2]" in text
    assert "region = eu-west-1" in text


def test_rename_leaves_other_sessions_alone(manager):
    manager.start(manager.registry.by_id("c1"))
    manager.start(manager.registry.by_id("c2"))

    manager.rename("acme-dev-Role-Admin", "acme-dev-Role-Admin-v2")

    text = manager.credentials_path.read_text(encoding="utf-8")
    assert "[beta-dev-Role-Read]" in text
    assert "aws_access_key_id = test" in text


def test_rename_of_an_inactive_profile_writes_nothing(manager):
    assert manager.rename("acme-dev-Role-Admin", "acme-dev-Role-Admin-v2") is False
    assert not manager.credentials_path.exists()


def test_rename_all_swaps_two_profiles_without_either_losing_its_material(manager):
    """The hazard `rename_all` exists for.

    Applied one pair at a time, the first rename writes c1's material onto
    the name the second pair is about to move, and the second carries it
    away: c2's live material is destroyed and c1's is served under both
    names' worth of bookkeeping.
    """
    manager.start(manager.registry.by_id("c1"))  # AKIA1
    manager.start(manager.registry.by_id("c2"))  # AKIA2

    moved = manager.rename_all(
        [
            ("acme-dev-Role-Admin", "beta-dev-Role-Read"),
            ("beta-dev-Role-Read", "acme-dev-Role-Admin"),
        ]
    )

    assert moved == 2
    entries = {
        entry.profile: entry
        for entry in awsfiles.read_managed_entries(manager.credentials_path)
    }
    assert set(entries) == {"acme-dev-Role-Admin", "beta-dev-Role-Read"}
    assert entries["beta-dev-Role-Read"].access_key_id == "AKIA1"
    assert entries["acme-dev-Role-Admin"].access_key_id == "AKIA2"
    state = State.load()
    assert state.get("beta-dev-Role-Read").credential_id == "c1"
    assert state.get("acme-dev-Role-Admin").credential_id == "c2"


def test_rename_all_walks_a_three_way_chain_without_clobbering(manager):
    manager.registry.add(
        Credential(id="c3", kind="fake-kind", profile="gamma-dev-Role-Ops",
                   provider="fake", region="eu-south-1")
    )
    manager.start(manager.registry.by_id("c1"))  # AKIA1
    manager.start(manager.registry.by_id("c2"))  # AKIA2
    manager.start(manager.registry.by_id("c3"))  # AKIA3

    moved = manager.rename_all(
        [
            ("acme-dev-Role-Admin", "beta-dev-Role-Read"),
            ("beta-dev-Role-Read", "gamma-dev-Role-Ops"),
            ("gamma-dev-Role-Ops", "acme-dev-Role-Admin"),
        ]
    )

    assert moved == 3
    entries = {
        entry.profile: entry
        for entry in awsfiles.read_managed_entries(manager.credentials_path)
    }
    assert entries["beta-dev-Role-Read"].access_key_id == "AKIA1"
    assert entries["gamma-dev-Role-Ops"].access_key_id == "AKIA2"
    assert entries["acme-dev-Role-Admin"].access_key_id == "AKIA3"
    state = State.load()
    assert state.get("beta-dev-Role-Read").credential_id == "c1"
    assert state.get("gamma-dev-Role-Ops").credential_id == "c2"
    assert state.get("acme-dev-Role-Admin").credential_id == "c3"


def test_rename_all_re_resolves_nothing(manager):
    """A sync must never turn a rename into an AWS call or an MFA prompt."""
    manager.start(manager.registry.by_id("c1"))
    calls_before = manager.provider.calls

    manager.rename_all([("acme-dev-Role-Admin", "acme-dev-Role-Admin-v2")])

    assert manager.provider.calls == calls_before


def test_rename_all_skips_pairs_with_nothing_to_move(manager):
    manager.start(manager.registry.by_id("c1"))

    moved = manager.rename_all(
        [
            ("acme-dev-Role-Admin", "acme-dev-Role-Admin-v2"),
            ("never-started", "never-started-v2"),
        ]
    )

    assert moved == 1
    assert State.load().get("never-started-v2") is None


def test_rename_all_writes_nothing_when_no_pair_has_anything_to_move(manager):
    assert manager.rename_all([("a", "b"), ("c", "d")]) == 0
    assert not manager.credentials_path.exists()


def test_rename_reconciles_a_profile_materialized_without_a_state_entry(manager):
    """`_write` writes credentials before state on purpose, so a crash in
    between - or simply deleting state.json - leaves real material on disk
    with no state entry. The rename still has to move it, or the orphan
    survives under a name nothing points at."""
    manager.start(manager.registry.by_id("c1"))
    manager.state.clear("acme-dev-Role-Admin")

    assert manager.rename("acme-dev-Role-Admin", "acme-dev-Role-Admin-v2") is True

    text = manager.credentials_path.read_text(encoding="utf-8")
    assert "[acme-dev-Role-Admin]" not in text
    assert "[acme-dev-Role-Admin-v2]" in text


def test_the_first_write_surfaces_the_backup_it_took(manager):
    manager.credentials_path.write_text("[handwritten]\n", encoding="utf-8")

    manager.start(manager.registry.by_id("c1"))

    assert manager.notices.backup == manager.credentials_path.with_name(
        manager.credentials_path.name + ".credux.bak"
    )


def test_a_second_write_clears_the_backup_notice(manager):
    manager.credentials_path.write_text("[handwritten]\n", encoding="utf-8")

    manager.start(manager.registry.by_id("c1"))
    manager.start(manager.registry.by_id("c2"))

    assert manager.notices.backup is None


def test_a_handwritten_default_profile_is_surfaced(manager):
    manager.credentials_path.write_text("[default]\naws_access_key_id = real\n", encoding="utf-8")

    manager.start(manager.registry.by_id("c1"))

    assert manager.notices.shadowed_default is True


def test_nothing_is_surfaced_for_a_file_credux_already_owns(manager):
    manager.start(manager.registry.by_id("c1"))
    manager.start(manager.registry.by_id("c2"))

    assert manager.notices.backup is None
    assert manager.notices.shadowed_default is False


def test_the_aws_config_file_backup_is_surfaced_too(manager):
    manager.config_path.write_text("[profile handwritten]\nregion = eu-west-1\n", encoding="utf-8")

    manager.start(manager.registry.by_id("c1"))

    assert manager.config_notices.backup == manager.config_path.with_name(
        manager.config_path.name + ".credux.bak"
    )
