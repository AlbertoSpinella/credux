import pytest

from credux import browser
from credux.config import DEFAULT_SETTINGS
from credux.errors import UsageError

COMMAND = DEFAULT_SETTINGS["browser_command"]
CHROME_COMMAND = ["google-chrome", "--profile-directory={profile}", "{url}"]


def test_the_shipped_default_uses_the_encoded_placeholders():
    """The default embeds both values inside a container URL's query string,
    so it must ask for the encoded pair. Pinned here because test_browser's
    other cases read the default rather than restating it."""
    assert COMMAND == [
        "firefox",
        "ext+container:name={profile_encoded}&url={url_encoded}&color={color}&icon={icon}",
    ]


def test_argv_is_rendered_with_profile_and_encoded_url():
    calls = []

    argv = browser.open_container(
        "acme-dev-Role-Admin",
        "https://signin.aws.amazon.com/federation?Action=login&x=1",
        COMMAND,
        runner=calls.append,
    )

    assert argv[0] == "firefox"
    assert argv[1].startswith("ext+container:name=acme-dev-Role-Admin&url=https%3A%2F%2F")
    assert "&x%3D1" in argv[1] or "%26x%3D1" in argv[1]
    assert calls == [argv]


def test_profile_with_special_characters_is_percent_encoded():
    # Profile names are rendered from naming templates over IAM principal and
    # role names, so they can legitimately contain '+' and '=', which would
    # otherwise corrupt the container URL's query string just like an
    # unencoded url does.
    argv = browser.open_container(
        "acme+prod=Role",
        "https://example.com",
        COMMAND,
        runner=lambda argv: None,
    )

    assert argv[1].startswith("ext+container:name=acme%2Bprod%3DRole&url=")


def test_a_standalone_argv_command_receives_the_url_unencoded():
    """A command that passes the URL as its own argv element must get a real
    URL. Encoding was unconditional, so Chrome received
    'https%3A%2F%2Fsignin...' and treated it as a search string - which is
    exactly the invocation the README documents for Chrome."""
    url = "https://signin.aws.amazon.com/federation?Action=login&SigninToken=abc"

    argv = browser.open_container(
        "acme-dev-Role-Admin", url, CHROME_COMMAND, runner=lambda argv: None
    )

    assert argv == ["google-chrome", "--profile-directory=acme-dev-Role-Admin", url]


def test_both_placeholder_pairs_can_appear_in_one_command():
    argv = browser.open_container(
        "acme+prod",
        "https://example.com/?a=1",
        ["browser", "{profile}|{profile_encoded}", "{url}|{url_encoded}"],
        runner=lambda argv: None,
    )

    assert argv[1] == "acme+prod|acme%2Bprod"
    assert argv[2] == "https://example.com/?a=1|https%3A%2F%2Fexample.com%2F%3Fa%3D1"


def test_the_shipped_default_asks_for_a_color_and_icon():
    """Without them the container extension applies its own fallbacks, which
    are a single fixed pair - so every container credux creates comes out the
    same color and the visual cue that tells two accounts apart is lost."""
    assert "color={color}" in COMMAND[1]
    assert "icon={icon}" in COMMAND[1]


def test_color_and_icon_are_substituted_into_the_command():
    argv = browser.open_container(
        "acme-dev-Role-Admin",
        "https://example.com",
        ["browser", "{color}|{icon}"],
        runner=lambda argv: None,
    )

    color, icon = argv[1].split("|")
    assert color in browser.CONTAINER_COLORS
    assert icon in browser.CONTAINER_ICONS


def test_color_and_icon_only_depend_on_the_profile_name():
    """Stability is the whole point: an account keeps its color across runs,
    across machines, and across interpreter restarts. hash() would satisfy the
    first only - it is salted per process unless PYTHONHASHSEED is pinned."""
    first = (browser.container_color("acme-prod"), browser.container_icon("acme-prod"))
    second = (browser.container_color("acme-prod"), browser.container_icon("acme-prod"))

    assert first == second
    # Pinned literally: a change to the derivation would silently repaint every
    # container a user has already learned to recognize.
    assert first == ("blue", "dollar")


def test_different_profiles_do_not_all_land_on_one_color():
    profiles = [f"acme-account{n}-SSO-ReadOnly" for n in range(40)]

    colors = {browser.container_color(profile) for profile in profiles}
    icons = {browser.container_icon(profile) for profile in profiles}

    # A derivation that collapsed to one value would still pass the stability
    # test above, so assert the spread separately.
    assert len(colors) >= 6
    assert len(icons) >= 8


def test_every_derived_color_and_icon_is_one_the_extension_accepts():
    """The extension validates both against a fixed list and refuses the whole
    link on anything else, so an out-of-list value is a hard failure, not a
    cosmetic one."""
    for n in range(200):
        profile = f"profile-{n}"
        assert browser.container_color(profile) in browser.CONTAINER_COLORS
        assert browser.container_icon(profile) in browser.CONTAINER_ICONS


def test_empty_command_is_a_usage_error():
    with pytest.raises(UsageError):
        browser.open_container("p", "https://example.com", [], runner=lambda argv: None)


def test_missing_browser_binary_is_actionable():
    def runner(argv):
        raise FileNotFoundError(argv[0])

    with pytest.raises(UsageError) as excinfo:
        browser.open_container("p", "https://example.com", COMMAND, runner=runner)

    assert "firefox" in str(excinfo.value)


def test_default_runner_is_read_from_the_module_at_call_time(monkeypatch):
    """No runner= is passed here: this is the call shape every real caller
    uses (see cli.py's console command). A module-level patch of
    browser.subprocess.Popen must be enough to intercept it - if `runner`
    were still a parameter default bound at import time, this patch would
    have no effect and a real subprocess.Popen would run instead."""
    launched = []
    monkeypatch.setattr(browser.subprocess, "Popen", lambda argv: launched.append(argv))

    argv = browser.open_container("acme-dev-Role-Admin", "https://example.com", COMMAND)

    assert launched == [argv]
