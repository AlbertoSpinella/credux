"""The credux command line."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from . import (
    completion,
    doctor as doctor_module,
    integrations,
    shellenv,
    store,
    sync as sync_module,
    upgrade as upgrade_module,
)
from .app import App
from .browser import open_container
from .config import DEFAULT_NAMING, TOP_LEVEL_CONFIG_KEYS
from .errors import ConfigError, IntegrationError, CreduxError, UsageError
from .naming import KNOWN_VARIABLES, TEMPLATE_KEYS, profile_name, validate_template
from .model import Credential, new_id
from .providers.aws import iam_user as iam_user_provider
from .providers.aws import org as org_detection
from .providers.aws import sso, sso_login
from .providers.base import ResolvedCredentials, provider_for
from .state import ActiveSession, utcnow

console = Console()
err_console = Console(stderr=True)

SHELL_FILES = {"posix": "credux.sh", "fish": "credux.fish", "powershell": "credux.ps1"}


def _app(ctx: click.Context) -> App:
    return ctx.obj


def _find(app: App, name: str) -> Credential:
    """Turn a profile argument into one credential, asking when it is ambiguous.

    `Registry.search` does the matching; the only thing added here is the
    question. Everything it prints goes to stderr: `credux env` is run as
    eval "$(credux env NAME)", so a numbered list on stdout would be handed
    to the shell to execute.

    Declining to answer (no stdin at all, a pipe, Ctrl-C) is not an abort but
    the same ambiguity error `Registry.resolve` would have raised, so a script
    that passes a term matching several profiles gets a message naming them
    and the usual usage exit code rather than a bare "Aborted!".
    """
    matches = app.registry.search(name)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise UsageError(f"No credential matches {name!r}. Run 'credux list' to see what exists.")

    err_console.print(f"{name!r} matches {len(matches)} credentials:")
    for index, cred in enumerate(matches, start=1):
        err_console.print(f"  {index}) {cred.profile}")
    try:
        choice = click.prompt(
            "Which one", type=click.IntRange(1, len(matches)), err=True, show_choices=False
        )
    except (click.Abort, EOFError):
        names = ", ".join(cred.profile for cred in matches)
        raise UsageError(f"{name!r} is ambiguous; it matches: {names}") from None
    return matches[choice - 1]


def _sso_session(app: App, name: str) -> dict:
    """Look up a configured Identity Center session, or fail cleanly.

    `login` and `sync` both take a session name straight from the command
    line. Sharing one lookup keeps them from drifting apart on what an
    unknown name does.
    """
    session_cfg = app.config.sso_sessions.get(name)
    if not session_cfg:
        known = ", ".join(sorted(app.config.sso_sessions)) or "none"
        raise UsageError(f"Unknown session {name!r}; configured sessions: {known}")
    return session_cfg


def _detect_management(
    app: App, account_id: str, resolved: ResolvedCredentials, region: str | None
) -> bool:
    """Ask Organizations, but never let the answer block adding a credential."""
    detected = org_detection.detect_management(
        account_id, resolved, app.client_factory, region=region
    )
    if detected is None:
        console.print(
            "[yellow]Could not determine whether this is the organization management "
            "account (no organizations:DescribeOrganization access). Assuming it is not; "
            "pass --management to override.[/yellow]"
        )
    return bool(detected)


# How many accounts the management-account question lists before summarizing
# the rest. Long enough to cover an ordinary organization, short enough that
# the question stays visible above the list on one screen.
ACCOUNT_LIST_LIMIT = 20


def _discovered_accounts(discovered) -> list[tuple[str, str]]:
    """The distinct (account_id, account_name) pairs, in discovery order."""
    accounts: dict[str, str] = {}
    for role in discovered:
        accounts.setdefault(str(role.account_id), role.account_name)
    return list(accounts.items())


def _print_accounts(accounts: list[tuple[str, str]], limit: int | None) -> None:
    """List the discovered accounts on stderr, summarizing past `limit`."""
    shown = accounts if limit is None else accounts[:limit]
    for account_id, account_name in shown:
        err_console.print(f"  {account_id}  {account_name}")
    remaining = len(accounts) - len(shown)
    if remaining > 0:
        err_console.print(f"  ... and {remaining} more (answer '?' to list all)")


def _ask_management_account(session_name: str, accounts: list[tuple[str, str]]) -> str | None:
    """Ask which discovered account is the organization management account.

    On stderr, for `cli._find`'s reason: sync output is read by humans but its
    stdout is also what a caller redirects, and a question is not a result.

    Returns None for every way of not answering - an empty answer, no stdin at
    all, Ctrl-C, or an id that discovery never reported. None means no
    management prefix, which is what credux did before it asked at all, so an
    unattended sync keeps working instead of blocking on a prompt.

    `?` prints every account and asks again: the summary hides the very id the
    reader may be looking for, and there is nowhere else to read it from.
    """
    err_console.print(
        f"{session_name}: which account is the AWS Organizations management account?"
    )
    _print_accounts(accounts, limit=ACCOUNT_LIST_LIMIT)

    while True:
        try:
            answer = click.prompt(
                "Account id (empty for none, ? to list all)",
                default="",
                err=True,
                show_default=False,
            )
        except (click.Abort, EOFError):
            answer = ""
        answer = answer.strip()
        if answer != "?":
            break
        _print_accounts(accounts, limit=None)

    if not answer:
        err_console.print(
            f"[yellow]{session_name}: no management account named, so no management prefix "
            f"is applied. Set sso_sessions.{session_name}.management_account_id with "
            "'credux config edit' to answer this once and for all.[/yellow]"
        )
        return None
    if answer not in dict(accounts):
        err_console.print(
            f"[yellow]{session_name}: {answer} is not one of the accounts discovered for "
            "this session, so it cannot be verified or named; no management prefix is "
            "applied.[/yellow]"
        )
        return None
    return answer


def _management_account_for(
    app: App, session_name: str, session_cfg: dict, token: str, discovered
) -> str | None:
    """The management account id to name profiles with, asked for and verified.

    Configured once, the id is not asked for again - but it is still verified on
    every sync, because an organization can be restructured under a config file
    that then quietly renames every profile in the wrong account.

    Verification probes a role in the candidate account and asks Organizations
    which account is the management account:

    - it agrees: use it.
    - it will not say (no organizations:DescribeOrganization, an expired token):
      keep the candidate and warn. `org.py`'s contract is that a missing
      permission is unknown, never fatal.
    - it contradicts the candidate: raise. A wrong id is worse than no id, since
      it prefixes one account's profiles and un-prefixes another's, and the next
      sync with a corrected answer renames all of them back.
    """
    accounts = _discovered_accounts(discovered)
    if not accounts:
        return None

    configured = session_cfg.get("management_account_id")
    candidate = str(configured) if configured else _ask_management_account(session_name, accounts)
    if not candidate:
        return None

    role_name = next(
        (role.role_name for role in discovered if str(role.account_id) == candidate), None
    )
    if role_name is None:
        # Only reachable for a configured id: an answered one is checked against
        # discovery before it gets here. Nothing to borrow credentials from, so
        # nothing to ask Organizations with - and a different cause from a role
        # that exists but lacks the permission, so a different message.
        err_console.print(
            f"[yellow]{session_name}: {candidate} was not discovered for this session, so it "
            "could not be verified and no account here takes the management prefix.[/yellow]"
        )
        return candidate

    observed = org_detection.management_account_id_via_sso(
        session_cfg, token, candidate, role_name, app.client_factory
    )
    if observed is None:
        err_console.print(
            f"[yellow]{session_name}: could not verify that {candidate} is the management "
            "account (no organizations:DescribeOrganization reachable from it). Using it "
            "as given.[/yellow]"
        )
        return candidate
    if observed != candidate:
        raise ConfigError(
            f"{session_name}: AWS Organizations reports {observed} as the management "
            f"account, not {candidate}. Naming profiles from {candidate} would prefix the "
            f"wrong account, so nothing was changed; correct "
            f"sso_sessions.{session_name}.management_account_id (or answer {observed}) and "
            "run 'credux sync' again."
        )
    return candidate


class CreduxGroup(click.Group):
    """Maps CreduxError onto its exit code, in production and under CliRunner alike."""

    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except CreduxError as exc:
            click.echo(str(exc), err=True)
            ctx.exit(exc.exit_code)


@click.group(cls=CreduxGroup)
@click.version_option(package_name="credux")
@click.option("--mfa-code", default=None, help="Supply an MFA code non-interactively.")
@click.pass_context
def cli(ctx: click.Context, mfa_code: str | None) -> None:
    """Manage cloud credentials and the profiles that use them."""
    ctx.obj = App(mfa_code=mfa_code)


@cli.group("add", cls=CreduxGroup)
def add_group() -> None:
    """Add a credential or an Identity Center session."""


@add_group.command("iam-user")
@click.option("--org", prompt=True, help="Organization or customer token used in the name.")
@click.option("--account-name", prompt=True)
@click.option("--account-id", prompt=True)
@click.option("--user", prompt=True)
@click.option("--region", default=None)
@click.option("--mfa-serial", default=None, help="Enable GetSessionToken with this MFA device.")
@click.option("--access-key-id", prompt=True)
@click.option("--secret-access-key", prompt=True, hide_input=True)
@click.option("--management/--no-management", default=None, help="Override account detection.")
@click.pass_context
def add_iam_user(
    ctx, org, account_name, account_id, user, region, mfa_serial, access_key_id,
    secret_access_key, management,
):
    """Add an IAM user with long-term access keys."""
    app = _app(ctx)
    cred_id = new_id()

    # Management detection needs usable credentials, not stored ones: build
    # them in memory from the values already on hand so nothing here
    # depends on the keychain write below happening first.
    if management is None:
        management = _detect_management(
            app,
            account_id,
            ResolvedCredentials(access_key_id, secret_access_key, region=region),
            region,
        )

    profile = profile_name(
        "iam-user",
        app.config.naming,
        {
            "org": org,
            "account_name": account_name,
            "account_id": account_id,
            "user": user,
            "region": region,
        },
        management=management,
    )
    params = {"org": org, "account_name": account_name, "account_id": account_id, "user": user}
    if mfa_serial:
        params["mfa_serial"] = mfa_serial

    # Register before writing to the keychain: a duplicate profile name
    # makes registry.add() raise before any secret material is written, so
    # a retried or duplicate `add` never leaves orphaned keys nobody can
    # reach (nothing references cred_id until the registry entry exists).
    # If the keychain write then fails, undo the registration so config.json
    # and the keychain never disagree, and let the error surface.
    app.registry.add(
        Credential(id=cred_id, kind="iam-user", profile=profile, region=region, params=params)
    )
    try:
        iam_user_provider.save_keys(cred_id, access_key_id, secret_access_key)
    except Exception:
        app.registry.remove(cred_id)
        raise
    app.save()
    console.print(f"Added [bold]{profile}[/bold]")
    _report_default_naming(app, "iam-user", profile)


@add_group.command("iam-role")
@click.option("--org", prompt=True)
@click.option("--account-name", prompt=True)
@click.option("--account-id", prompt=True)
@click.option("--role", prompt=True, help="Role name used in the profile name.")
@click.option("--role-arn", prompt=True)
@click.option("--source", prompt=True, help="Profile name of the credential to chain from.")
@click.option("--session-name", default="credux")
@click.option("--external-id", default=None)
@click.option("--mfa-serial", default=None)
@click.option("--duration", type=int, default=3600)
@click.option("--region", default=None)
@click.option("--management/--no-management", default=None)
@click.pass_context
def add_iam_role(
    ctx, org, account_name, account_id, role, role_arn, source, session_name,
    external_id, mfa_serial, duration, region, management,
):
    """Add a role assumed from another Credux credential."""
    app = _app(ctx)
    parent = _find(app, source)

    if management is None:
        management = False

    profile = profile_name(
        "iam-role",
        app.config.naming,
        {
            "org": org,
            "account_name": account_name,
            "account_id": account_id,
            "role": role,
            "region": region,
        },
        management=management,
    )
    params = {
        "org": org,
        "account_name": account_name,
        "account_id": account_id,
        "role": role,
        "role_arn": role_arn,
        "source": parent.id,
        "session_name": session_name,
        "duration_seconds": duration,
    }
    if external_id:
        params["external_id"] = external_id
    if mfa_serial:
        params["mfa_serial"] = mfa_serial

    app.registry.add(
        Credential(id=new_id(), kind="iam-role", profile=profile, region=region, params=params)
    )
    app.save()
    console.print(f"Added [bold]{profile}[/bold] (chained from {parent.profile})")
    _report_default_naming(app, "iam-role", profile)


@add_group.command("sso-session")
@click.argument("name")
@click.option("--start-url", prompt=True)
@click.option("--sso-region", prompt=True)
@click.option("--management-account-id", default=None,
              help="Account id that should receive the management prefix.")
@click.pass_context
def add_sso_session(ctx, name, start_url, sso_region, management_account_id):
    """Register an Identity Center session. Then run 'credux login NAME' and 'credux sync NAME'."""
    app = _app(ctx)
    session = {"start_url": start_url, "sso_region": sso_region}
    if management_account_id:
        session["management_account_id"] = management_account_id
    app.config.sso_sessions[name] = session
    app.save()
    console.print(
        f"Registered session [bold]{name}[/bold]. "
        f"Next: credux login {name}, then credux sync {name}"
    )
    # Before the sync rather than after it: this is the last moment the user
    # can pick their own template without a rename happening first.
    _report_default_naming(app, "sso")


@cli.command("list")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
@click.pass_context
def list_credentials(ctx, as_json):
    """List every configured credential."""
    app = _app(ctx)
    now = utcnow()
    rows = []
    for cred in sorted(app.registry.all(), key=lambda item: item.profile):
        session = app.state.get(cred.profile)
        remaining = session.expires_in(now) if session else None
        if session is None:
            status, ttl = "", ""
        elif remaining is None:
            status, ttl = "active", "no expiry"
        elif remaining.total_seconds() <= 0:
            status, ttl = "active", "expired"
        else:
            status, ttl = "active", f"{int(remaining.total_seconds() // 60)}m"
        rows.append(
            {
                "profile": cred.profile,
                "kind": cred.kind,
                "region": cred.region or "",
                "status": status,
                "ttl": ttl,
            }
        )

    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return

    table = Table(box=None)
    for column in ("", "profile", "kind", "region", "ttl"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            "*" if row["status"] else " ",
            row["profile"],
            row["kind"],
            row["region"],
            row["ttl"],
        )
    console.print(table)


REMOVE_ALL_PHRASE = "permanently delete"


def _remove_one(app: App, cred: Credential, manager) -> None:
    """Drop one credential's session, secrets, and registry entry.

    Does not save: the caller decides when config.json is written, so
    `--all` produces one write rather than one per credential.
    """
    if app.state.get(cred.profile) is not None:
        manager.stop(cred.profile)
    if cred.kind == "iam-user":
        store.delete_owner(cred.id, iam_user_provider.SECRET_FIELDS)
    app.registry.remove(cred.id)


@cli.command("remove")
@click.argument("profile", required=False, shell_complete=completion.complete_profiles)
@click.option("--all", "remove_all", is_flag=True,
              help="Delete every credential and every Identity Center session.")
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
@click.pass_context
def remove_credential(ctx, profile, remove_all, yes):
    """Delete a credential, its stored secrets, and any active session."""
    app = _app(ctx)
    manager = app.sessions()

    if remove_all:
        if profile:
            raise UsageError("Pass --all or a profile name, not both")
        # --yes is a blanket "assume yes" that scripts and aliases carry
        # around for the single-profile form. Letting it also answer for
        # --all would turn one stray flag into the loss of every credential
        # credux knows about, so the whole-registry wipe has no
        # non-interactive path at all: a human types the phrase, or nothing
        # happens.
        if yes:
            raise UsageError("--yes cannot confirm --all; type the confirmation phrase instead")
        _remove_all(app, manager)
        return

    if not profile:
        raise UsageError("Name a profile, or pass --all")

    cred = _find(app, profile)
    if not yes:
        click.confirm(f"Remove {cred.profile}?", abort=True)

    _remove_one(app, cred, manager)
    _report_file_notices(manager)
    app.save()
    console.print(f"Removed [bold]{cred.profile}[/bold]")


def _remove_all(app: App, manager) -> None:
    """Wipe every credential and every registered Identity Center session.

    The sessions go too because leaving them would make the wipe look
    undone: the next `credux sync NAME` repopulates every SSO-derived
    credential the user just asked to be rid of.
    """
    creds = app.registry.all()
    sessions = sorted(app.config.sso_sessions)
    if not creds and not sessions:
        console.print("Nothing to remove")
        return

    console.print(
        f"This deletes {_count(len(creds), 'credential')}, their stored secrets, "
        f"and {_count(len(sessions), 'Identity Center session')}."
    )
    typed = click.prompt(f"Type {REMOVE_ALL_PHRASE!r} to confirm", default="", show_default=False)
    if typed.strip() != REMOVE_ALL_PHRASE:
        raise click.Abort()

    for cred in creds:
        _remove_one(app, cred, manager)
    app.config.sso_sessions.clear()
    _report_file_notices(manager)
    app.save()
    console.print(
        f"Removed {_count(len(creds), 'credential')} "
        f"and {_count(len(sessions), 'Identity Center session')}"
    )


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


@cli.group("config", cls=CreduxGroup)
def config_group() -> None:
    """Inspect and change configuration."""


@config_group.command("path")
@click.pass_context
def config_path(ctx):
    """Print the path of the config file."""
    click.echo(str(_app(ctx).config.path))


@config_group.command("show")
@click.pass_context
def config_show(ctx):
    """Print the effective configuration."""
    click.echo(json.dumps(_app(ctx).config.to_dict(), indent=2))


@config_group.command("get")
@click.argument("key")
@click.pass_context
def config_get(ctx, key):
    """Print one setting by dotted path."""
    click.echo(json.dumps(_app(ctx).config.setting(key), indent=2))


@config_group.command("edit")
@click.pass_context
def config_edit(ctx):
    """Open the config file in $EDITOR."""
    app = _app(ctx)
    if not app.config.path.exists():
        app.save()
    click.edit(filename=str(app.config.path))


def _set_naming(app: App, parts: list[str], value: object) -> None:
    """Write one `naming` key, refusing anything that would not be read.

    The naming block is flat and closed: every key it can hold is a key
    credux itself looks up, so an unrecognized one is a typo that would sit
    in config.json doing nothing. A template is validated here, at the
    moment it is set, rather than at the moment it would first have failed
    to render a name - and a template typed at the command line is a usage
    error, not a broken config file, so the ConfigError is re-raised as one.
    """
    if len(parts) > 1:
        raise UsageError(
            f"naming.{parts[0]} is a single value, not a section; "
            f"'{'.'.join(parts)}' cannot be set"
        )
    name = parts[0]
    if name not in DEFAULT_NAMING:
        raise UsageError(
            f"{name!r} is not a naming key. Known keys: {', '.join(sorted(DEFAULT_NAMING))}"
        )
    if not isinstance(value, str):
        raise UsageError(f"naming.{name} must be a string, not {type(value).__name__}")
    if name in TEMPLATE_KEYS.values():
        try:
            validate_template(value)
        except ConfigError as exc:
            raise UsageError(str(exc)) from exc

    app.config.naming[name] = value
    app.save()
    console.print(f"naming.{name} = {value!r}")


@config_group.command("set")
@click.argument("key")
@click.argument("value")
@click.pass_context
def config_set(ctx, key, value):
    """Set one setting. VALUE is parsed as JSON when possible."""
    app = _app(ctx)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value

    parts = key.split(".")
    # `naming` is the one top-level block this command does write, because
    # the profile-name templates are the part of config.json a user is most
    # likely to want to change and sending them to an editor for it is a
    # poor trade. Bare `naming` still falls through to the refusal below:
    # replacing the whole block wholesale is an editor's job.
    if parts[0] == "naming" and len(parts) > 1:
        _set_naming(app, parts[1:], parsed)
        return
    # This command only ever writes under `settings`. A config.json top-level
    # key looks like it should work here and silently would not: the write
    # would land on settings.<key>, which nothing reads, and the command
    # would still report success.
    if parts[0] in TOP_LEVEL_CONFIG_KEYS:
        raise UsageError(
            f"{parts[0]!r} is a top-level key in config.json, not a setting, and "
            "'credux config set' only writes under 'settings'. Edit it with "
            "'credux config edit' instead."
        )

    node = app.config.settings
    for index, part in enumerate(parts[:-1]):
        child = node.get(part)
        if child is None:
            child = {}
            node[part] = child
        elif not isinstance(child, dict):
            walked = ".".join(parts[: index + 1])
            raise UsageError(
                f"Cannot set {key!r}: settings.{walked} is already a value "
                f"({child!r}), not a section. Set settings.{walked} directly, or "
                "clear it first with 'credux config edit'."
            )
        node = child
    node[parts[-1]] = parsed
    app.save()
    console.print(f"settings.{key} = {parsed!r}")


@cli.command("start")
@click.argument("profiles", nargs=-1, shell_complete=completion.complete_profiles)
@click.option("--all", "start_all", is_flag=True, help="Start every configured credential.")
@click.pass_context
def start(ctx, profiles, start_all):
    """Write one or more credentials into the AWS credentials file."""
    app = _app(ctx)
    manager = app.sessions()

    if start_all and profiles:
        raise UsageError("Pass --all or profile names, not both")
    if not start_all:
        if not profiles:
            raise UsageError("Name at least one profile, or pass --all")
        for name in profiles:
            cred = _find(app, name)
            session = manager.start(cred)
            _report_started(cred.profile, session)
        _report_file_notices(manager)
        return

    creds = app.registry.all()
    if not creds:
        raise UsageError("No credentials are configured. Run 'credux add' or 'credux sync' first.")

    # Unlike the named form above, one credential failing does not abort the
    # rest: --all is routinely a whole Identity Center account set, where a
    # single role denying AssumeRole should not cost the other twenty their
    # session. The exit code is the first failure's own code so a script
    # still sees the usual meaning (see the table in docs/troubleshooting.md).
    outcome = manager.start_all(creds)
    for session in outcome.started:
        _report_started(session.profile, session)
    _report_file_notices(manager)
    for cred, exc in outcome.failures:
        err_console.print(f"Failed [bold]{cred.profile}[/bold]: {exc}")
    if outcome.failures:
        ctx.exit(outcome.failures[0][1].exit_code)


def _report_default_naming(app: App, kind: str, example: str | None = None) -> None:
    """Say once that the profile names just chosen came from a stock template.

    Credux's default templates encode one particular house style, and a user
    who has never opened config.json has been given it rather than chosen
    it. Printed at the moment names are created or about to be, because that
    is when changing the template is cheapest: an `sso` credential is renamed
    by the next `sync`, but an `iam-user` or `iam-role` one keeps the name it
    was added under.

    Goes quiet as soon as any template differs from the shipped default, so
    setting one - even to the same string - is how a user dismisses it.
    """
    if not app.config.uses_default_naming:
        return
    key = TEMPLATE_KEYS[kind]
    shown = f" ({example})" if example else ""
    err_console.print(
        f"Naming{shown} uses credux's default: naming.{key} = {app.config.naming[key]}\n"
        f"  Change it: credux config set naming.{key} '<template>'\n"
        f"  Variables: {', '.join(sorted(KNOWN_VARIABLES))}",
        markup=False,
        soft_wrap=True,
        style="dim",
    )


def _report_file_notices(manager) -> None:
    """Say what the last AWS-file write found worth reporting.

    Both notices describe a state the user cannot see for themselves after
    the fact and would otherwise never be told about: a copy of their old
    file appearing beside it, and their own `default` profile now competing
    with credux's. Printed on stderr so a command whose stdout is consumed
    by a script (`credux env`, `credux list --json`) stays parseable.

    `markup=False` throughout: `[default]` is valid Rich markup and would be
    swallowed as a style tag rather than printed. `soft_wrap=True` for the
    same class of reason: Rich's own word wrap breaks a long path across
    lines, and these messages exist to be copied - a path with a newline in
    the middle of it is not one. Nothing here interpolates file content,
    only paths, so nothing can leak a secret.
    """
    for report, path in (
        (manager.notices, manager.credentials_path),
        (manager.config_notices, manager.config_path),
    ):
        if report.backup is not None:
            err_console.print(
                f"Backed up {path} to {report.backup} before credux's first write to it "
                "(owner-only). Credux replaces only the text between its managed block "
                "markers; the rest of the file is preserved byte for byte.",
                markup=False,
                soft_wrap=True,
                style="dim",
            )
    if manager.notices.shadowed_default:
        err_console.print(
            f"Warning: {manager.credentials_path} already had a hand-written [default] "
            "profile, and credux writes its own placeholder [default] inside its managed "
            "block. Which one an AWS SDK picks up is decided by that SDK, not by credux. "
            "Rename yours, or turn credux's off with "
            "'credux config set default_profile.enabled false'.",
            markup=False,
            soft_wrap=True,
        )


def _report_started(profile: str, session: ActiveSession) -> None:
    ttl = "no expiry" if session.expires_at is None else f"until {session.expires_at}"
    console.print(f"Started [bold]{profile}[/bold] ({ttl})")


@cli.command("stop")
@click.argument("profiles", nargs=-1, shell_complete=completion.complete_profiles)
@click.option("--all", "stop_all", is_flag=True, help="Stop every active session.")
@click.pass_context
def stop(ctx, profiles, stop_all):
    """Remove credentials from the AWS credentials file."""
    app = _app(ctx)
    manager = app.sessions()
    if stop_all:
        count = manager.stop_all()
        console.print(f"Stopped {count} session(s)")
        _report_file_notices(manager)
        return
    if not profiles:
        raise UsageError("Name at least one profile, or pass --all")
    for name in profiles:
        cred = _find(app, name)
        if manager.stop(cred.profile):
            console.print(f"Stopped [bold]{cred.profile}[/bold]")
        else:
            console.print(f"{cred.profile} was not active")
    _report_file_notices(manager)


@cli.command("refresh")
@click.option("--all", "refresh_all", is_flag=True, help="Renew every active session.")
@click.option("--threshold", type=int, default=None, help="Minutes-to-expiry that triggers renewal.")
@click.pass_context
def refresh(ctx, refresh_all, threshold):
    """Renew active sessions that are close to expiry."""
    app = _app(ctx)
    manager = app.sessions()
    profiles = [session.profile for session in app.state.all()] if refresh_all else None
    renewed = manager.refresh(threshold_minutes=threshold, profiles=profiles)
    if not renewed:
        console.print("Nothing to refresh")
        return
    for session in renewed:
        console.print(f"Refreshed [bold]{session.profile}[/bold]")
    _report_file_notices(manager)


@cli.command("env")
@click.argument("profile", required=False, shell_complete=completion.complete_profiles)
@click.option("--shell", "shell_name", default=None, help="posix, fish, powershell, or cmd.")
@click.option("--unset", is_flag=True, help="Print the statement that clears AWS_PROFILE.")
@click.pass_context
def env(ctx, profile, shell_name, unset):
    """Print a statement that sets AWS_PROFILE. Use: eval "$(credux env NAME)"."""
    app = _app(ctx)
    shell = shell_name or shellenv.detect_shell()
    if unset:
        click.echo(shellenv.env_statement(None, shell, unset=True))
        return
    if not profile:
        raise UsageError("Name a profile, or pass --unset")

    cred = _find(app, profile)
    if app.state.get(cred.profile) is None:
        manager = app.sessions()
        manager.start(cred)
        err_console.print(f"Started {cred.profile}", style="dim")
        _report_file_notices(manager)
    click.echo(shellenv.env_statement(cred.profile, shell))


@cli.command("shell-init")
@click.option("--shell", "shell_name", default=None, help="posix, fish, or powershell.")
@click.pass_context
def shell_init(ctx, shell_name):
    """Print the shell wrapper that gives you the 'pu' command and tab completion.

    Add 'eval "$(credux shell-init)"' to your shell rc, then use 'pu <profile>'
    to switch the current shell's AWS_PROFILE and 'pu' with no argument to
    unset it. The same output registers tab completion for credux's
    subcommands, profile names, and Identity Center session names, and for
    'pu' itself.
    """
    shell = shell_name or shellenv.detect_shell()
    filename = SHELL_FILES.get(shell)
    if filename is None:
        raise UsageError(
            f"No shell wrapper for {shell!r}; supported: {', '.join(SHELL_FILES)}"
        )
    click.echo((Path(__file__).parent / "shell" / filename).read_text(encoding="utf-8"))

    # "posix" is one wrapper for bash and zsh, whose completion scripts are
    # not interchangeable, so it gets a snippet that chooses at shell startup.
    # Every other shell either has exactly one Click backend or none at all,
    # in which case source() returns "" and nothing extra is printed.
    if shell == "posix":
        click.echo(completion.posix_snippet(cli))
    else:
        script = completion.source(shell, cli)
        if script:
            click.echo(script)


@cli.group("completions", cls=CreduxGroup, hidden=True)
def completions_group() -> None:
    """Candidate lists for shell completers that Click's script cannot serve."""


@completions_group.command("profiles")
@click.argument("incomplete", required=False, default="")
def completions_profiles(incomplete: str) -> None:
    """Print the profile names matching INCOMPLETE, one per line.

    `pu` is a shell function, so Click's generated script - which registers
    itself against the `credux` command alone - never completes it. Its
    completer calls this instead, and it deliberately goes through the same
    completer `credux env <TAB>` uses so the two offer the same set.

    Only stdout is the contract: the group callback loads the config eagerly,
    so an unreadable one still fails here, and the shell completers discard
    both stderr and the exit status rather than offer an error message as a
    candidate.
    """
    for name in completion.complete_profiles(None, None, incomplete):
        click.echo(name)


@cli.command("console")
@click.argument("profile", shell_complete=completion.complete_profiles)
@click.pass_context
def console_command(ctx, profile):
    """Open the AWS console for a profile in its own browser container."""
    app = _app(ctx)
    cred = _find(app, profile)
    resolution = app.context()
    resolved = resolution.resolve(cred)
    url = provider_for(cred).console_url(cred, resolution, resolved)
    open_container(cred.profile, url, app.config.setting("browser_command", []))
    console.print(f"Opened a container for [bold]{cred.profile}[/bold]")


@cli.command("login")
@click.argument("session_name", shell_complete=completion.complete_sso_sessions)
@click.pass_context
def login(ctx, session_name):
    """Run the Identity Center device authorization flow for a session."""
    app = _app(ctx)
    session_cfg = _sso_session(app, session_name)
    sso_login.login(session_name, session_cfg, app.client_factory)
    console.print(f"Logged in to [bold]{session_name}[/bold]")


@cli.command("sync")
@click.argument("session_name", required=False, shell_complete=completion.complete_sso_sessions)
@click.option("--prune", is_flag=True, help="Remove credentials that no longer exist.")
@click.pass_context
def sync_command(ctx, session_name, prune):
    """Discover Identity Center accounts and roles, then run integrations."""
    app = _app(ctx)
    names = [session_name] if session_name else sorted(app.config.sso_sessions)
    if not names:
        raise UsageError("No Identity Center sessions configured; run 'credux add sso-session'")

    # Built on demand: constructing it is cheap, but every method on it
    # rewrites the AWS files, and a sync that changes nothing must not touch
    # them at all.
    manager = None
    # One notice for the whole command, not one per session or per role: a
    # sync across several sessions renders every name from the same template.
    named_anything = False

    for name in names:
        session_cfg = _sso_session(app, name)
        token = sso_login.token_or_raise(name)
        discovered = sso.discover(session_cfg, token, app.client_factory)
        # Worked out before planning, not after: the answer decides which
        # account's profiles carry the management prefix, so it is an input to
        # every name the plan renders.
        management_account_id = _management_account_for(
            app, name, session_cfg, token, discovered
        )
        plan = sync_module.plan_sso_sync(
            app.config, app.registry, name, discovered, management_account_id
        )
        sync_module.apply_plan(app.registry, plan, prune=prune)
        named_anything = named_anything or bool(plan.created or plan.renamed)

        # The registry is keyed by id, but the credentials file and state are
        # keyed by profile name, so a rename that only touches the registry
        # strands live credential material under the old name: still usable,
        # no longer reachable through 'credux list' or 'credux stop <name>'.
        #
        # One call, not a loop over `rename`: a single sync can contain a
        # rename chain or swap, and applying those one at a time makes the
        # second rename carry off the material the first just moved. See
        # `SessionManager.rename_all`.
        #
        # No `app.state.get(previous)` guard: a profile can be materialized in
        # the credentials file without a state entry - `_write` writes
        # credentials before state, so a crash in between leaves exactly that,
        # as does deleting state.json - and such an orphan needs reconciling
        # just as much. `rename_all` checks both and writes nothing when there
        # is genuinely nothing to move.
        if plan.renamed:
            manager = manager or app.sessions()
            manager.rename_all([(previous, cred.profile) for cred, previous in plan.renamed])

        # Likewise a pruned credential: removing the registry entry alone
        # would leave its session materialized with nothing pointing at it.
        if prune:
            for cred in plan.removed:
                if app.state.get(cred.profile) is None:
                    continue
                manager = manager or app.sessions()
                manager.stop(cred.profile)

        for cred in plan.created:
            console.print(f"[green]+[/green] {cred.profile}")
        for cred, previous in plan.renamed:
            console.print(f"[yellow]~[/yellow] {previous} -> {cred.profile}")
        for cred in plan.removed:
            marker = "[red]-[/red]" if prune else "[dim]stale[/dim]"
            console.print(f"{marker} {cred.profile}")
        if plan.is_empty:
            console.print(f"{name}: already up to date")

    if named_anything:
        _report_default_naming(app, "sso")
    if manager is not None:
        _report_file_notices(manager)

    app.save()
    outcome = integrations.run_all(app.config, app.registry.all())
    for line in outcome.lines:
        console.print(line)
    if outcome.failed:
        raise IntegrationError(
            "One or more integrations or post-sync hooks failed; see the lines above"
        )


@cli.command("doctor")
@click.pass_context
def doctor_command(ctx):
    """Check the environment and report anything that needs attention."""
    app = _app(ctx)
    checks = doctor_module.run_checks(app.config, app.registry, app.state)
    colours = {"ok": "green", "warn": "yellow", "fail": "red"}
    table = Table(box=None)
    for column in ("status", "check", "detail"):
        table.add_column(column)
    for check in checks:
        table.add_row(
            f"[{colours[check.status]}]{check.status}[/{colours[check.status]}]",
            check.name,
            check.detail,
        )
    console.print(table)
    if doctor_module.worst_status(checks) == "fail":
        ctx.exit(1)


@cli.command("upgrade")
@click.option("--version", default=None, help="Release to install, e.g. 0.2.0. Default: newest.")
@click.option(
    "--from",
    "source",
    default=None,
    help="Install from this local clone instead of from GitHub.",
)
def upgrade_command(version, source):
    """Replace this installation of credux with another release.

    Nothing runs after the installer returns: it has just replaced the
    virtual environment this process is running from, so any module credux
    has not imported yet may no longer be on disk. Everything this command
    prints is either printed before the install or built from values it
    already holds.
    """
    plan = upgrade_module.plan(version=version, source=source)

    if version is None and source is None:
        current = upgrade_module.current_version()
        if plan.target == current:
            console.print(f"credux {current} is already the newest release.")
            return

    console.print(f"Upgrading credux to {plan.target} with {plan.manager}")
    upgrade_module.apply(plan)
    console.print(f"credux is now {plan.target}")


def main() -> None:
    """Entry point: turn Credux errors into messages and exit codes.

    ``cli.main(standalone_mode=False)`` does not raise ``SystemExit`` on its
    own: CreduxGroup.invoke() maps a CreduxError to ``ctx.exit(exc.exit_code)``,
    and in non-standalone mode click turns that into a plain return value
    instead of a raised exception. That return value must be forwarded to
    ``sys.exit`` explicitly, or the process would always exit 0 no matter
    what exit code a command reported.

    Every exit code here is read off the exception itself
    (``exc.exit_code``) rather than hardcoded per exception type, and
    ``click.Abort`` exits 1 to match click's own hardcoded convention for it
    (see ``click.core.Command.main``'s ``except Abort`` branch) - under
    ``CliRunner`` (standalone mode), a declined confirmation always exits 1
    regardless of what this module does, so this handler must agree.
    """
    try:
        exit_code = cli.main(standalone_mode=False)
    except CreduxError as exc:
        click.echo(str(exc), err=True)
        sys.exit(exc.exit_code)
    except click.ClickException as exc:
        exc.show()
        sys.exit(exc.exit_code)
    except click.Abort:
        click.echo("Aborted!", err=True)
        sys.exit(1)
    else:
        sys.exit(exit_code or 0)
