import pytest

from credux import shellenv
from credux.errors import UsageError


def test_detect_prefers_explicit_override():
    assert shellenv.detect_shell({"CREDUX_SHELL": "fish", "SHELL": "/bin/bash"}) == "fish"


def test_detect_reads_the_shell_variable():
    assert shellenv.detect_shell({"SHELL": "/usr/bin/zsh"}, platform="linux") == "posix"
    assert shellenv.detect_shell({"SHELL": "/usr/bin/fish"}, platform="linux") == "fish"


def test_detect_falls_back_to_powershell_on_windows():
    assert shellenv.detect_shell({"PSModulePath": "C:/x"}, platform="win32") == "powershell"
    assert shellenv.detect_shell({}, platform="win32") == "cmd"


def test_detect_falls_back_to_posix_elsewhere():
    assert shellenv.detect_shell({}, platform="linux") == "posix"


@pytest.mark.parametrize(
    "shell,expected",
    [
        ("posix", 'export AWS_PROFILE="acme-dev-User-ada"'),
        ("fish", 'set -gx AWS_PROFILE "acme-dev-User-ada"'),
        ("powershell", '$env:AWS_PROFILE = "acme-dev-User-ada"'),
        ("cmd", "set AWS_PROFILE=acme-dev-User-ada"),
    ],
)
def test_set_statements(shell, expected):
    assert shellenv.env_statement("acme-dev-User-ada", shell) == expected


@pytest.mark.parametrize(
    "shell,expected",
    [
        ("posix", "unset AWS_PROFILE"),
        ("fish", "set -e AWS_PROFILE"),
        ("powershell", "Remove-Item Env:\\AWS_PROFILE -ErrorAction SilentlyContinue"),
        ("cmd", "set AWS_PROFILE="),
    ],
)
def test_unset_statements(shell, expected):
    assert shellenv.env_statement(None, shell, unset=True) == expected


def test_unknown_shell_is_a_usage_error():
    with pytest.raises(UsageError):
        shellenv.env_statement("p", "tcsh")


def test_profile_names_that_could_break_out_are_rejected():
    with pytest.raises(UsageError):
        shellenv.env_statement('a"; rm -rf /; echo "', "posix")

    with pytest.raises(UsageError):
        shellenv.env_statement("a\nb", "posix")


@pytest.mark.parametrize("shell", shellenv.SHELLS)
@pytest.mark.parametrize("meta", ["&", "|", "%", "!", "^", "<", ">"])
def test_cmd_metacharacters_are_rejected_for_every_shell(shell, meta):
    # cmd's "set VAR=value" form is unquoted, so these are live cmd.exe
    # metacharacters (command chaining, ordinary "%" and delayed "!"
    # variable expansion). Validation is not shell-conditional: a name
    # unsafe on any one of the four shells is rejected for all of them, so
    # behaviour stays predictable across platforms regardless of which
    # shell a given name is rendered for.
    with pytest.raises(UsageError):
        shellenv.env_statement(f"a{meta}b", shell)


def test_profile_name_with_a_space_is_rejected():
    # Spaces break the unquoted cmd form and are not produced by any
    # shipped default naming template.
    with pytest.raises(UsageError):
        shellenv.env_statement("a b", "posix")


@pytest.mark.parametrize("shell", shellenv.SHELLS)
@pytest.mark.parametrize(
    "name",
    [
        "acme-billing-User-ada",  # {org}-{account_name}-User-{user}
        "acme-platform-Role-Admin",  # {org}-{account_name}-Role-{role}
        "acme-platform-SSO-AdministratorAccess",  # {org}-{account_name}-SSO-{role}
        "acme-management_platform-Role-Admin",  # management account, "management_" prefix
        "acme-platform-SSO-Billing.ReadOnly",  # permission set name with a period
    ],
)
def test_names_from_default_templates_are_accepted_for_every_shell(shell, name):
    # Must not raise: every name a shipped default naming template (or the
    # AWS identifiers that feed it, including account aliases and
    # Identity Center permission-set names containing digits or a period)
    # can legitimately produce has to keep working.
    assert shellenv.env_statement(name, shell)


@pytest.mark.parametrize("shell", shellenv.SHELLS)
@pytest.mark.parametrize("char", ["+", "=", ",", "@"])
def test_iam_characters_are_accepted_and_rendered_unchanged(shell, char):
    # AWS IAM user and role names permit "+=,.@_-", and a profile name
    # derived from a legitimate IAM principal must not be rejected by
    # Credux's own validator. Each of these is inert in every shell: the
    # first three shells interpolate inside double quotes, and cmd's
    # unquoted "set" treats everything after the first "=" as the literal
    # value, so the character must survive into the rendered statement
    # exactly as given, not be stripped or escaped.
    name = f"a{char}b"
    statement = shellenv.env_statement(name, shell)
    assert name in statement


@pytest.mark.parametrize("shell", shellenv.SHELLS)
def test_realistic_iam_principal_name_renders_for_every_shell(shell):
    # A user segment shaped like a real IAM user name, e.g. one derived
    # from an email-style login with a "+ops" suffix.
    name = "acme-platform-User-first.last+ops@example"
    statement = shellenv.env_statement(name, shell)
    assert name in statement
