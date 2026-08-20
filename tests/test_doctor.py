import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from credux import awsfiles, doctor
from credux.config import Config
from credux.model import Credential, Registry
from credux.providers.aws.sso import DiscoveredRole
from credux.state import ActiveSession, State
from credux.sync import apply_plan, plan_sso_sync

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


WORKING_BACKEND = "keyring.backends.SecretService.Keyring"


def checks_for(
    config, registry, state, backend=WORKING_BACKEND, which=lambda name: "/bin/firefox"
):
    return {
        check.name: check
        for check in doctor.run_checks(
            config, registry, state, backend=lambda: backend, which=which, now=NOW
        )
    }


def test_reports_the_keychain_backend(credux_home, tmp_path):
    config = Config.load()
    result = checks_for(config, Registry(config), State.load())

    assert result["secret store"].status == "ok"
    assert "SecretService" in result["secret store"].detail


@pytest.mark.parametrize(
    "backend",
    [
        # keyring's own no-backend classes. Both are called `Keyring`; only
        # the module distinguishes them from a real backend, so a check that
        # substring-matches "fail" against the class name passes them as ok.
        "keyring.backends.fail.Keyring",
        "keyring.backends.null.Keyring",
        # store.backend_name's own failure fallback.
        "unavailable (no backend)",
        "",
    ],
)
def test_flags_a_missing_keychain(credux_home, backend):
    config = Config.load()
    result = checks_for(config, Registry(config), State.load(), backend=backend)

    assert result["secret store"].status == "fail"


def test_a_backend_named_keyring_in_a_real_module_is_not_flagged(credux_home):
    """The class name alone says nothing: a working backend can also be
    called `Keyring`, so the check must not reject one on that basis."""
    config = Config.load()
    result = checks_for(
        config, Registry(config), State.load(), backend="keyring.backends.macOS.Keyring"
    )

    assert result["secret store"].status == "ok"


def test_flags_a_missing_browser(credux_home):
    config = Config.load()
    result = checks_for(config, Registry(config), State.load(), which=lambda name: None)

    assert result["browser"].status == "warn"
    assert "firefox" in result["browser"].detail


def test_flags_a_credentials_file_without_the_fake_default(credux_home, tmp_path):
    config = Config.load()
    credentials = tmp_path / "credentials"
    credentials.write_text("[acme]\naws_access_key_id = real\n", encoding="utf-8")
    config.settings["aws_credentials_file"] = str(credentials)

    result = checks_for(config, Registry(config), State.load())

    assert result["default profile"].status == "warn"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits only")
def test_flags_a_world_readable_credentials_backup(credux_home, tmp_path):
    """doctor used to look only at the live file, so it reported permissions
    'ok' while the one-time backup beside it - holding the same real keys -
    was still 0644."""
    config = Config.load()
    credentials = tmp_path / "credentials"
    credentials.write_text("[acme]\n", encoding="utf-8")
    os.chmod(credentials, 0o600)
    backup = tmp_path / "credentials.credux.bak"
    backup.write_text("[acme]\naws_access_key_id = AKIAREALKEY\n", encoding="utf-8")
    os.chmod(backup, 0o644)
    config.settings["aws_credentials_file"] = str(credentials)

    result = checks_for(config, Registry(config), State.load())

    assert result["credentials permissions"].status == "ok"
    assert result["credentials backup"].status == "warn"
    assert str(backup) in result["credentials backup"].detail


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits only")
def test_accepts_an_owner_only_credentials_backup(credux_home, tmp_path):
    config = Config.load()
    credentials = tmp_path / "credentials"
    credentials.write_text("[acme]\n", encoding="utf-8")
    os.chmod(credentials, 0o600)
    backup = tmp_path / "credentials.credux.bak"
    backup.write_text("[acme]\n", encoding="utf-8")
    os.chmod(backup, 0o600)
    config.settings["aws_credentials_file"] = str(credentials)

    result = checks_for(config, Registry(config), State.load())

    assert result["credentials backup"].status == "ok"


def test_reports_no_backup_when_none_was_taken(credux_home, tmp_path):
    config = Config.load()
    config.settings["aws_credentials_file"] = str(tmp_path / "credentials")

    result = checks_for(config, Registry(config), State.load())

    assert result["credentials backup"].status == "ok"
    assert "no backup" in result["credentials backup"].detail


def test_flags_a_broken_source_chain(credux_home):
    config = Config.load()
    registry = Registry(config)
    registry.add(
        Credential(id="r1", kind="iam-role", profile="p", params={"role_arn": "a", "source": "ghost"})
    )

    result = checks_for(config, registry, State.load())

    assert result["credential chains"].status == "fail"
    assert "ghost" in result["credential chains"].detail


def test_naming_accepts_a_credential_shaped_exactly_as_sync_creates_it(credux_home):
    """`sync` computed org/subdomain at plan time and threw them away, keeping
    only sso_session/account_id/account_name/role_name in params. _naming
    rebuilds its variables from params, so the default {org}-... template had
    no org to render and every single Identity Center credential came back as
    'naming templates: warn ... needs a value for: org'.

    Built through plan_sso_sync rather than by hand so it cannot pass on a
    credential shape sync does not actually produce.
    """
    config = Config.load()
    config.sso_sessions["acme"] = {
        "start_url": "https://acme.awsapps.com/start",
        "sso_region": "eu-west-1",
    }
    registry = Registry(config)
    plan = plan_sso_sync(
        config, registry, "acme", [DiscoveredRole("111111111111", "billing", "ReadOnly")]
    )
    apply_plan(registry, plan)
    assert [cred.profile for cred in registry.all()] == ["acme-billing-SSO-ReadOnly"]

    result = checks_for(config, registry, State.load())

    assert result["naming templates"].status == "ok", result["naming templates"].detail


def test_naming_accepts_a_credential_shaped_as_add_iam_role_creates_it(credux_home):
    config = Config.load()
    registry = Registry(config)
    registry.add(
        Credential(
            id="r1",
            kind="iam-role",
            profile="acme-platform-Role-Admin",
            params={
                "org": "acme",
                "account_name": "platform",
                "account_id": "111111111111",
                "role": "Admin",
                "role_arn": "arn:aws:iam::111111111111:role/Admin",
                "source": "r1",
            },
        )
    )

    result = checks_for(config, registry, State.load())

    assert result["naming templates"].status == "ok", result["naming templates"].detail


def test_flags_expired_sessions(credux_home):
    config = Config.load()
    state = State.load()
    state.set(
        ActiveSession(
            profile="p",
            credential_id="c",
            kind="sso",
            expires_at=(NOW - timedelta(minutes=5)).isoformat(),
        )
    )

    result = checks_for(config, Registry(config), state)

    assert result["active sessions"].status == "warn"


def test_worst_status_picks_the_most_severe():
    checks = [
        doctor.Check("a", "ok", ""),
        doctor.Check("b", "warn", ""),
        doctor.Check("c", "fail", ""),
    ]

    assert doctor.worst_status(checks) == "fail"
    assert doctor.worst_status(checks[:2]) == "warn"


def test_survives_a_credentials_file_permission_error(credux_home, tmp_path, monkeypatch):
    """A check that cannot read the credentials file must not take doctor down with it."""
    config = Config.load()
    credentials = tmp_path / "credentials"
    credentials.write_text("[acme]\naws_access_key_id = real\n", encoding="utf-8")
    config.settings["aws_credentials_file"] = str(credentials)

    original_read_text = Path.read_text

    def boom(self, *args, **kwargs):
        # Scoped to the credentials path so this test still exercises what
        # its name claims even if another file read is added to doctor.py.
        if self == credentials:
            raise PermissionError("denied")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", boom)

    result = checks_for(config, Registry(config), State.load())

    assert result["default profile"].status == "warn"
    assert "denied" not in result["default profile"].detail
    # Every other check still ran despite the failure above.
    assert result["secret store"].status == "ok"
    assert result["browser"].status == "ok"
    assert result["active sessions"].status == "ok"


def test_survives_a_credentials_file_with_invalid_utf8(credux_home, tmp_path):
    """A UnicodeDecodeError's own message embeds file bytes; it must not leak either."""
    config = Config.load()
    credentials = tmp_path / "credentials"
    credentials.write_bytes(b"[acme]\naws_access_key_id = \xff\xfeReal\n")
    config.settings["aws_credentials_file"] = str(credentials)

    result = checks_for(config, Registry(config), State.load())

    detail = result["default profile"].detail
    assert result["default profile"].status == "warn"
    assert "0xff" not in detail
    assert "position" not in detail
    assert "codec" not in detail
    assert str(credentials) in detail
    # The rest of the run still completed.
    assert result["secret store"].status == "ok"
    assert result["browser"].status == "ok"
    assert result["active sessions"].status == "ok"


def test_a_crashing_check_fails_without_taking_down_the_others(credux_home, monkeypatch):
    config = Config.load()

    def explode(registry):
        raise RuntimeError("boom")

    monkeypatch.setattr(doctor, "_chains", explode)

    result = checks_for(config, Registry(config), State.load())

    assert result["credential chains"].status == "fail"
    assert result["secret store"].status == "ok"
    assert result["active sessions"].status == "ok"


def test_which_default_is_read_from_the_module_at_call_time(credux_home, monkeypatch):
    """No which= is passed to run_checks here: this is the call shape the
    real doctor command uses. A module-level patch of doctor.shutil.which
    must be enough to intercept it - if `which` were still a parameter
    default bound at import time, this patch would have no effect and the
    check would fall back to the real shutil.which."""
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    config = Config.load()

    checks = {
        check.name: check
        for check in doctor.run_checks(
            # backend is passed explicitly (as a harmless stand-in) so this
            # test, which is only about the `which` default, never touches a
            # real keychain backend.
            config, Registry(config), State.load(), backend=lambda: "SecretService", now=NOW
        )
    }

    assert checks["browser"].status == "warn"
    assert "firefox" in checks["browser"].detail


def test_credentials_file_content_never_reaches_a_check_detail(credux_home, tmp_path):
    config = Config.load()
    credentials = tmp_path / "credentials"
    secret_marker = "AKIA_SUPER_SECRET_VALUE"
    credentials.write_text(f"[acme]\naws_access_key_id = {secret_marker}\n", encoding="utf-8")
    config.settings["aws_credentials_file"] = str(credentials)

    result = checks_for(config, Registry(config), State.load())

    for check in result.values():
        assert secret_marker not in check.detail


def test_flags_a_handwritten_default_beside_the_managed_one(credux_home, tmp_path):
    config = Config.load()
    credentials = tmp_path / "credentials"
    credentials.write_text(
        "[default]\naws_access_key_id = real\n\n"
        f"{awsfiles.BEGIN_MARKER}\n\n[default]\naws_access_key_id = test\n"
        f"aws_secret_access_key = test\n{awsfiles.END_MARKER}\n",
        encoding="utf-8",
    )
    config.settings["aws_credentials_file"] = str(credentials)

    result = checks_for(config, Registry(config), State.load())

    assert result["shadowed default profile"].status == "warn"


def test_credux_own_default_alone_is_not_flagged_as_shadowed(credux_home, tmp_path):
    config = Config.load()
    credentials = tmp_path / "credentials"
    credentials.write_text(
        f"{awsfiles.BEGIN_MARKER}\n\n[default]\naws_access_key_id = test\n"
        f"aws_secret_access_key = test\n{awsfiles.END_MARKER}\n",
        encoding="utf-8",
    )
    config.settings["aws_credentials_file"] = str(credentials)

    result = checks_for(config, Registry(config), State.load())

    assert result["shadowed default profile"].status == "ok"


def test_a_handwritten_default_is_not_flagged_when_credux_writes_no_default(
    credux_home, tmp_path
):
    config = Config.load()
    credentials = tmp_path / "credentials"
    credentials.write_text("[default]\naws_access_key_id = real\n", encoding="utf-8")
    config.settings["aws_credentials_file"] = str(credentials)
    config.settings["default_profile"]["enabled"] = False

    result = checks_for(config, Registry(config), State.load())

    assert result["shadowed default profile"].status == "ok"


def test_the_shadowed_default_check_never_quotes_the_file(credux_home, tmp_path):
    config = Config.load()
    credentials = tmp_path / "credentials"
    credentials.write_text(
        "[default]\naws_secret_access_key = SUPERSECRETVALUE\n", encoding="utf-8"
    )
    config.settings["aws_credentials_file"] = str(credentials)

    result = checks_for(config, Registry(config), State.load())

    assert "SUPERSECRETVALUE" not in result["shadowed default profile"].detail
