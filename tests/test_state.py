import json
from datetime import datetime, timedelta, timezone

import pytest

from credux.errors import ConfigError
from credux.state import ActiveSession, State

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def session(profile="acme-dev-Role-Admin", minutes=60):
    expires = (NOW + timedelta(minutes=minutes)).isoformat()
    return ActiveSession(profile=profile, credential_id="abc", kind="iam-role", expires_at=expires)


def test_state_round_trips_through_disk(credux_home):
    state = State.load()
    state.set(session())
    state.save()

    reloaded = State.load()
    assert reloaded.get("acme-dev-Role-Admin").credential_id == "abc"


def test_missing_state_file_is_empty(credux_home):
    assert State.load().all() == []


def test_clear_removes_one_profile(credux_home):
    state = State.load()
    state.set(session("a"))
    state.set(session("b"))

    state.clear("a")

    assert [item.profile for item in state.all()] == ["b"]


def test_expires_in_is_relative_to_now():
    assert session(minutes=30).expires_in(NOW) == timedelta(minutes=30)


def test_is_expiring_uses_the_threshold():
    assert session(minutes=5).is_expiring(10, NOW) is True
    assert session(minutes=30).is_expiring(10, NOW) is False


def test_sessions_without_expiry_never_expire():
    never = ActiveSession(profile="p", credential_id="a", kind="iam-user")

    assert never.expires_in(NOW) is None
    assert never.is_expiring(10, NOW) is False


def test_unparseable_expiry_raises_config_error():
    broken = ActiveSession(profile="p", credential_id="a", kind="iam-user", expires_at="not-a-date")

    with pytest.raises(ConfigError):
        broken.expires_in(NOW)


def test_corrupt_state_file_raises_config_error(credux_home):
    (credux_home / "state.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(ConfigError):
        State.load()


def test_active_not_a_list_raises_config_error(credux_home):
    (credux_home / "state.json").write_text(json.dumps({"active": "oops"}), encoding="utf-8")

    with pytest.raises(ConfigError):
        State.load()


def test_active_entry_missing_field_raises_config_error(credux_home):
    (credux_home / "state.json").write_text(json.dumps({"active": [{}]}), encoding="utf-8")

    with pytest.raises(ConfigError):
        State.load()


def test_top_level_array_raises_config_error(credux_home):
    (credux_home / "state.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    with pytest.raises(ConfigError):
        State.load()
