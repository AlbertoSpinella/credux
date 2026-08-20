import pytest

from credux import integrations
from credux.config import Config
from credux.errors import IntegrationError
from credux.model import Credential

CREDENTIALS = [
    Credential(
        id="a",
        kind="sso",
        profile="acme-billing-SSO-ReadOnly",
        region="eu-west-1",
        params={"account_id": "123456789012", "account_name": "billing", "role_name": "ReadOnly"},
    ),
    Credential(id="b", kind="iam-user", profile="acme-dev-User-ada", params={"user": "ada"}),
]


def test_builtin_alias_template_lists_every_profile(tmp_path):
    spec = {"name": "aliases", "template": "builtin:aliases.sh.j2", "dest": str(tmp_path / "a.sh")}

    text = integrations.render_template(spec, CREDENTIALS)

    assert "acme-billing-SSO-ReadOnly" in text
    assert "acme-dev-User-ada" in text


def test_templates_never_receive_secret_material(tmp_path):
    spec = {
        "name": "leak-check",
        "template": str(tmp_path / "t.j2"),
        "dest": str(tmp_path / "out"),
    }
    (tmp_path / "t.j2").write_text("{{ credentials | tojson }}", encoding="utf-8")

    text = integrations.render_template(spec, CREDENTIALS)

    for forbidden in ("secret", "aws_secret_access_key", "session_token", "access_key"):
        assert forbidden not in text


def test_secret_material_does_not_escape_by_any_key(tmp_path, credux_home):
    """CREDENTIALS above carries no secret-shaped values, so it can't tell an
    allowlisted context apart from one that forwards `params` wholesale. This
    credential carries values that must never appear in a render or a hook
    payload no matter which key they might be smuggled under.
    """
    forbidden_values = (
        "SECRETVALUE-ACCESS-KEY",
        "SECRETVALUE-SESSION-TOKEN",
        "SECRETVALUE-ACCESS-KEY-ID",
        "SECRETVALUE-UNLISTED-FIELD",
    )
    forbidden_keys = (
        "aws_secret_access_key",
        "session_token",
        "aws_access_key_id",
        "totally_unlisted_field",
    )
    secret_cred = Credential(
        id="s",
        kind="iam-user",
        profile="acme-secret-holder",
        params={
            "user": "ada",
            "aws_secret_access_key": "SECRETVALUE-ACCESS-KEY",
            "session_token": "SECRETVALUE-SESSION-TOKEN",
            "aws_access_key_id": "SECRETVALUE-ACCESS-KEY-ID",
            "totally_unlisted_field": "SECRETVALUE-UNLISTED-FIELD",
        },
    )

    spec = {
        "name": "leak-check",
        "template": str(tmp_path / "t.j2"),
        "dest": str(tmp_path / "out"),
    }
    (tmp_path / "t.j2").write_text("{{ credentials | tojson }}", encoding="utf-8")
    rendered = integrations.render_template(spec, [secret_cred])

    for forbidden in forbidden_values + forbidden_keys:
        assert forbidden not in rendered

    config = Config.load()
    config.post_sync = ["true"]
    seen = {}

    def runner(command, **kwargs):
        seen["input"] = kwargs.get("input")

        class Result:
            returncode = 0

        return Result()

    integrations.run_all(config, [secret_cred], runner=runner)

    for forbidden in forbidden_values + forbidden_keys:
        assert forbidden not in seen["input"]


def test_context_exposes_both_role_keys(tmp_path):
    """`add iam-role` stores the short role name under `role`; `sync` stores
    an Identity Center permission set under `role_name`. The allowlist listed
    only the latter, so an IAM-role credential rendered role_name: null and
    had no way at all to name its role in a template."""
    iam_role = Credential(
        id="r",
        kind="iam-role",
        profile="acme-platform-Role-Admin",
        params={"role": "Admin", "role_arn": "arn:aws:iam::111111111111:role/Admin"},
    )

    context = integrations._context([iam_role, CREDENTIALS[0]])

    assert context["credentials"][0]["role"] == "Admin"
    assert context["credentials"][0]["role_name"] is None
    assert context["credentials"][1]["role_name"] == "ReadOnly"
    assert context["credentials"][1]["role"] is None


def test_region_mode_preserves_surrounding_content(tmp_path):
    dest = tmp_path / "out.sh"
    dest.write_text("# mine\nkeep me\n", encoding="utf-8")
    template = tmp_path / "t.j2"
    template.write_text("generated\n", encoding="utf-8")
    spec = {"name": "x", "template": str(template), "dest": str(dest), "mode": "region"}

    integrations.apply(spec, CREDENTIALS)

    text = dest.read_text(encoding="utf-8")
    assert "keep me" in text
    assert "generated" in text
    assert integrations.BEGIN_MARKER in text


def test_region_mode_converges_despite_stray_end_marker_before_begin_marker(tmp_path):
    """Historical landmine (see awsfiles.replace_managed_region): if the end
    marker is searched for from index 0 instead of from the begin marker's
    index, a stray end-marker-like line sitting before any real begin marker
    is mistaken for the close of the region on every subsequent write, and a
    fresh block gets appended forever instead of the existing one being
    replaced. This pins the fix in place for the parallel mechanism here.
    """
    dest = tmp_path / "out.sh"
    dest.write_text(f"# {integrations.END_MARKER}\nnot actually managed\n", encoding="utf-8")
    template = tmp_path / "t.j2"
    template.write_text("generated\n", encoding="utf-8")
    spec = {"name": "x", "template": str(template), "dest": str(dest), "mode": "region"}

    integrations.apply(spec, CREDENTIALS)
    after_first = dest.read_text(encoding="utf-8")

    integrations.apply(spec, CREDENTIALS)
    after_second = dest.read_text(encoding="utf-8")

    integrations.apply(spec, CREDENTIALS)
    after_third = dest.read_text(encoding="utf-8")

    assert after_second == after_third
    assert after_second.count(f"# {integrations.BEGIN_MARKER}") == 1
    assert "not actually managed" in after_second


def test_replace_mode_overwrites_the_whole_file(tmp_path):
    dest = tmp_path / "out.json"
    dest.write_text("old", encoding="utf-8")
    template = tmp_path / "t.j2"
    template.write_text("new", encoding="utf-8")
    spec = {"name": "x", "template": str(template), "dest": str(dest), "mode": "replace"}

    integrations.apply(spec, CREDENTIALS)

    assert dest.read_text(encoding="utf-8") == "new"


def test_invalid_mode_is_an_integration_error(tmp_path):
    template = tmp_path / "t.j2"
    template.write_text("hi\n", encoding="utf-8")
    spec = {"name": "x", "template": str(template), "dest": str(tmp_path / "o"), "mode": "bogus"}

    with pytest.raises(IntegrationError) as excinfo:
        integrations.apply(spec, CREDENTIALS)

    assert "bogus" in str(excinfo.value)
    assert "x" in str(excinfo.value)


def test_disabled_integrations_are_skipped(tmp_path, credux_home):
    config = Config.load()
    config.integrations = [
        {"name": "off", "enabled": False, "template": "builtin:aliases.sh.j2", "dest": str(tmp_path / "no.sh")}
    ]

    outcome = integrations.run_all(config, CREDENTIALS, runner=lambda *a, **k: None)

    assert not (tmp_path / "no.sh").exists()
    assert outcome.lines == []
    assert outcome.failed is False


def test_missing_template_is_an_integration_error(tmp_path):
    spec = {"name": "x", "template": str(tmp_path / "absent.j2"), "dest": str(tmp_path / "o")}

    with pytest.raises(IntegrationError) as excinfo:
        integrations.apply(spec, CREDENTIALS)

    assert "absent.j2" in str(excinfo.value)


def test_one_failing_integration_does_not_block_another(tmp_path, credux_home):
    config = Config.load()
    good_dest = tmp_path / "good.sh"
    config.integrations = [
        {"name": "bad", "template": str(tmp_path / "absent.j2"), "dest": str(tmp_path / "bad.sh")},
        {"name": "good", "template": "builtin:aliases.sh.j2", "dest": str(good_dest)},
    ]

    outcome = integrations.run_all(config, CREDENTIALS, runner=lambda *a, **k: None)

    assert good_dest.exists()
    assert outcome.failed is True
    assert any("bad" in line and "failed" in line for line in outcome.lines)
    assert any("good" in line for line in outcome.lines)


def test_failing_integration_still_runs_post_sync_hooks(tmp_path, credux_home):
    config = Config.load()
    config.integrations = [
        {"name": "bad", "template": str(tmp_path / "absent.j2"), "dest": str(tmp_path / "bad.sh")},
    ]
    config.post_sync = ["true"]
    called = []

    def runner(command, **kwargs):
        called.append(command)

        class Result:
            returncode = 0

        return Result()

    outcome = integrations.run_all(config, CREDENTIALS, runner=runner)

    assert called == ["true"]
    assert outcome.failed is True


def test_failed_is_false_when_everything_succeeds(tmp_path, credux_home):
    config = Config.load()
    config.integrations = [
        {"name": "good", "template": "builtin:aliases.sh.j2", "dest": str(tmp_path / "good.sh")},
    ]
    config.post_sync = ["true"]

    def runner(command, **kwargs):
        class Result:
            returncode = 0

        return Result()

    outcome = integrations.run_all(config, CREDENTIALS, runner=runner)

    assert outcome.failed is False
    assert (tmp_path / "good.sh").exists()


def test_post_sync_hooks_receive_credentials_as_json(tmp_path, credux_home):
    config = Config.load()
    config.post_sync = ["true"]
    seen = {}

    def runner(command, **kwargs):
        seen["command"] = command
        seen["input"] = kwargs.get("input")

        class Result:
            returncode = 0

        return Result()

    integrations.run_all(config, CREDENTIALS, runner=runner)

    assert seen["command"] == "true"
    assert "acme-dev-User-ada" in seen["input"]
    assert "secret" not in seen["input"]


def test_failing_hook_is_reported_but_not_fatal(tmp_path, credux_home):
    config = Config.load()
    config.post_sync = ["false"]

    def runner(command, **kwargs):
        class Result:
            returncode = 1

        return Result()

    outcome = integrations.run_all(config, CREDENTIALS, runner=runner)

    assert any("exit code 1" in line for line in outcome.lines)
    assert outcome.failed is True
