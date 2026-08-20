import pytest

from credux import awsfiles
from credux.awsfiles import ProfileEntry
from credux.errors import ConfigError

FAKE_DEFAULT = {"enabled": True, "access_key_id": "test", "secret_access_key": "test"}


def test_default_profile_is_always_written(tmp_path):
    path = tmp_path / "credentials"

    awsfiles.write_credentials(path, [], FAKE_DEFAULT)

    text = path.read_text(encoding="utf-8")
    assert "[default]" in text
    assert "aws_access_key_id = test" in text
    assert "aws_secret_access_key = test" in text


def test_default_profile_can_be_disabled(tmp_path):
    path = tmp_path / "credentials"

    awsfiles.write_credentials(path, [], {"enabled": False})

    assert "[default]" not in path.read_text(encoding="utf-8")


def test_session_token_written_only_when_present(tmp_path):
    path = tmp_path / "credentials"
    entries = [
        ProfileEntry("static", "AKIA1", "s1"),
        ProfileEntry("temporary", "ASIA1", "s2", session_token="tok"),
    ]

    awsfiles.write_credentials(path, entries, FAKE_DEFAULT)

    text = path.read_text(encoding="utf-8")
    static_block = text.split("[temporary]")[0]
    assert "aws_session_token" not in static_block
    assert "aws_session_token = tok" in text


def test_content_outside_the_managed_region_is_preserved(tmp_path):
    path = tmp_path / "credentials"
    path.write_text(
        "[handwritten]\naws_access_key_id = keep\n\n"
        f"{awsfiles.BEGIN_MARKER}\nold junk\n{awsfiles.END_MARKER}\n"
        "[after]\naws_access_key_id = keep-too\n",
        encoding="utf-8",
    )

    awsfiles.write_credentials(path, [ProfileEntry("new", "AKIA2", "s")], FAKE_DEFAULT)

    text = path.read_text(encoding="utf-8")
    assert "[handwritten]" in text
    assert "[after]" in text
    assert "old junk" not in text
    assert "[new]" in text


def test_managed_region_is_appended_when_absent(tmp_path):
    path = tmp_path / "credentials"
    path.write_text("[handwritten]\naws_access_key_id = keep\n", encoding="utf-8")

    awsfiles.write_credentials(path, [], FAKE_DEFAULT)

    text = path.read_text(encoding="utf-8")
    assert text.index("[handwritten]") < text.index(awsfiles.BEGIN_MARKER)


def test_first_write_takes_a_backup(tmp_path):
    path = tmp_path / "credentials"
    path.write_text("[handwritten]\n", encoding="utf-8")

    awsfiles.write_credentials(path, [], FAKE_DEFAULT)

    assert (tmp_path / "credentials.credux.bak").read_text(encoding="utf-8") == "[handwritten]\n"


def test_the_first_write_reports_the_backup_it_took(tmp_path):
    path = tmp_path / "credentials"
    path.write_text("[handwritten]\naws_access_key_id = real\n", encoding="utf-8")

    report = awsfiles.write_credentials(path, [], FAKE_DEFAULT)

    assert report.backup == tmp_path / "credentials.credux.bak"


def test_a_later_write_reports_no_further_backup(tmp_path):
    path = tmp_path / "credentials"
    path.write_text("[handwritten]\naws_access_key_id = real\n", encoding="utf-8")

    awsfiles.write_credentials(path, [], FAKE_DEFAULT)
    report = awsfiles.write_credentials(path, [], FAKE_DEFAULT)

    assert report.backup is None


def test_a_write_to_a_new_file_reports_no_backup(tmp_path):
    path = tmp_path / "credentials"

    report = awsfiles.write_credentials(path, [], FAKE_DEFAULT)

    assert report.backup is None


def test_a_handwritten_default_profile_is_reported_as_shadowed(tmp_path):
    path = tmp_path / "credentials"
    path.write_text("[default]\naws_access_key_id = real\n", encoding="utf-8")

    report = awsfiles.write_credentials(path, [], FAKE_DEFAULT)

    assert report.shadowed_default is True


def test_credux_own_default_is_not_reported_as_shadowed(tmp_path):
    path = tmp_path / "credentials"

    awsfiles.write_credentials(path, [], FAKE_DEFAULT)
    report = awsfiles.write_credentials(path, [], FAKE_DEFAULT)

    assert report.shadowed_default is False


def test_a_handwritten_default_is_not_shadowed_when_the_fake_one_is_disabled(tmp_path):
    path = tmp_path / "credentials"
    path.write_text("[default]\naws_access_key_id = real\n", encoding="utf-8")

    report = awsfiles.write_credentials(path, [], {"enabled": False})

    assert report.shadowed_default is False


def test_no_backup_is_taken_of_a_file_credux_itself_created(tmp_path):
    path = tmp_path / "credentials"

    awsfiles.write_credentials(path, [], FAKE_DEFAULT)
    awsfiles.write_credentials(path, [], FAKE_DEFAULT)

    assert not (tmp_path / "credentials.credux.bak").exists()


def test_a_deleted_backup_is_not_recreated_from_a_managed_file(tmp_path):
    path = tmp_path / "credentials"
    path.write_text("[handwritten]\naws_access_key_id = real\n", encoding="utf-8")
    awsfiles.write_credentials(path, [], FAKE_DEFAULT)
    (tmp_path / "credentials.credux.bak").unlink()

    awsfiles.write_credentials(path, [], FAKE_DEFAULT)

    assert not (tmp_path / "credentials.credux.bak").exists()


def test_config_file_writes_region_per_profile(tmp_path):
    path = tmp_path / "config"

    awsfiles.write_config(path, [ProfileEntry("acme", "k", "s", region="eu-west-1")])

    text = path.read_text(encoding="utf-8")
    assert "[profile acme]" in text
    assert "region = eu-west-1" in text


def test_the_config_file_write_reports_its_own_backup(tmp_path):
    path = tmp_path / "config"
    path.write_text("[profile handwritten]\nregion = eu-west-1\n", encoding="utf-8")

    report = awsfiles.write_config(path, [])

    assert report.backup == tmp_path / "config.credux.bak"


def test_rewrite_is_idempotent(tmp_path):
    path = tmp_path / "credentials"
    entries = [ProfileEntry("acme", "AKIA", "s")]

    awsfiles.write_credentials(path, entries, FAKE_DEFAULT)
    first = path.read_text(encoding="utf-8")
    awsfiles.write_credentials(path, entries, FAKE_DEFAULT)

    assert path.read_text(encoding="utf-8") == first


def test_stray_end_marker_text_before_any_real_marker_converges(tmp_path):
    path = tmp_path / "credentials"
    path.write_text(
        f"[handwritten]\n{awsfiles.END_MARKER}\nsome text\n",
        encoding="utf-8",
    )

    awsfiles.write_credentials(path, [], FAKE_DEFAULT)
    first = path.read_text(encoding="utf-8")
    awsfiles.write_credentials(path, [], FAKE_DEFAULT)
    second = path.read_text(encoding="utf-8")
    awsfiles.write_credentials(path, [], FAKE_DEFAULT)
    third = path.read_text(encoding="utf-8")

    assert first == second == third
    assert third.count(awsfiles.BEGIN_MARKER) == 1
    assert third.count(awsfiles.END_MARKER) == 2  # one real, one from stray text


def test_supplied_default_profile_does_not_displace_the_fake_default(tmp_path):
    path = tmp_path / "credentials"
    entries = [ProfileEntry("default", "AKIAEXAMPLE", "notthefakeone")]

    awsfiles.write_credentials(path, entries, FAKE_DEFAULT)

    text = path.read_text(encoding="utf-8")
    assert text.count("[default]") == 1
    assert "aws_access_key_id = test" in text
    assert "aws_secret_access_key = test" in text
    assert "AKIAEXAMPLE" not in text


def test_config_ignores_an_entry_named_default(tmp_path):
    path = tmp_path / "config"
    entries = [ProfileEntry("default", "k", "s", region="us-east-1")]

    awsfiles.write_config(path, entries)

    text = path.read_text(encoding="utf-8")
    assert "[profile default]" not in text
    assert "us-east-1" not in text


def test_read_managed_entries_round_trips(tmp_path):
    path = tmp_path / "credentials"
    entries = [
        ProfileEntry("acme", "AKIA", "s"),
        ProfileEntry("beta", "ASIA", "s2", session_token="tok"),
    ]
    awsfiles.write_credentials(path, entries, FAKE_DEFAULT)

    read_back = awsfiles.read_managed_entries(path)

    assert [(e.profile, e.session_token) for e in read_back] == [
        ("acme", None),
        ("beta", "tok"),
    ]


def test_read_managed_entries_ignores_handwritten_profiles(tmp_path):
    path = tmp_path / "credentials"
    path.write_text("[handwritten]\naws_access_key_id = x\n", encoding="utf-8")
    awsfiles.write_credentials(path, [ProfileEntry("acme", "AKIA", "s")], FAKE_DEFAULT)

    assert [e.profile for e in awsfiles.read_managed_entries(path)] == ["acme"]


def test_read_managed_entries_survives_a_stray_end_marker_before_the_real_block(tmp_path):
    # Regression: a human-written line that happens to match END_MARKER,
    # placed before the real managed block, must not make the real block
    # invisible to the reader. `replace_managed_region` already gets this
    # right by searching for END_MARKER starting at BEGIN_MARKER's index;
    # `read_managed_entries` must use the same search.
    path = tmp_path / "credentials"
    path.write_text(
        f"[handwritten]\n{awsfiles.END_MARKER}\nsome text\n",
        encoding="utf-8",
    )
    awsfiles.write_credentials(path, [ProfileEntry("acme", "AKIA", "s")], FAKE_DEFAULT)

    assert [e.profile for e in awsfiles.read_managed_entries(path)] == ["acme"]


def test_read_managed_entries_raises_config_error_on_a_malformed_block(tmp_path):
    path = tmp_path / "credentials"
    path.write_text(
        f"{awsfiles.BEGIN_MARKER}\n\n[acme]\nx = 1\n\n[acme]\ny = 2\n\n{awsfiles.END_MARKER}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        awsfiles.read_managed_entries(path)


def test_a_malformed_block_never_quotes_the_credential_material(tmp_path):
    """configparser's parse errors embed the offending source line verbatim.

    In this file that line is a key line, so interpolating the exception into
    the ConfigError leaks a live secret into a message the CLI prints on
    stderr. Here the block has no section header at all, which makes
    configparser report the first key line it saw.
    """
    path = tmp_path / "credentials"
    secret = "ASIAREALKEYID12345"
    path.write_text(
        f"{awsfiles.BEGIN_MARKER}\n\naws_access_key_id = {secret}\n\n{awsfiles.END_MARKER}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as excinfo:
        awsfiles.read_managed_entries(path)

    message = str(excinfo.value)
    assert secret not in message
    assert "aws_access_key_id" not in message
    # The line number is content-free and worth keeping, unlike the line text.
    assert "MissingSectionHeaderError" in message


def test_a_value_containing_a_percent_sign_is_read_literally(tmp_path):
    """configparser's default BasicInterpolation would raise here.

    That exception is raised while *reading a value*, which used to happen
    outside the try block, so it escaped as a raw traceback carrying part of
    the secret rather than as a CreduxError.
    """
    path = tmp_path / "credentials"
    awsfiles.write_credentials(
        path, [ProfileEntry("acme", "AKIAEXAMPLE", "se%defcret")], FAKE_DEFAULT
    )

    entries = awsfiles.read_managed_entries(path)

    assert [(e.profile, e.secret_access_key) for e in entries] == [("acme", "se%defcret")]
