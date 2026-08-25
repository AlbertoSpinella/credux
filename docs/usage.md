# Usage

Full command reference and the behaviours worth knowing about. For install and a
five-line quickstart, see the [README](../README.md).

## Commands

| Command | What it does |
|---|---|
| `credux add iam-user` | Prompts for org, account, user, and access keys; stores the keys in the OS keychain. |
| `credux add iam-role` | Registers a role to assume, optionally chained through another credux credential (`--source`). |
| `credux add sso-session <name>` | Registers an IAM Identity Center session (`--start-url`, `--sso-region`, `--management-account-id`). |
| `credux list` | Every configured credential, marking the active ones and their remaining time-to-live. `--json` for scripting. |
| `credux remove <profile>` | Removes a credential from the registry. `--yes` skips the confirmation. `--all` wipes everything, see below. |
| `credux start <profile>...` | Resolves credentials and writes the profiles into `~/.aws/credentials`. `--all` starts everything. |
| `credux stop <profile>...` | Removes those profiles from the managed block. `--all` stops everything. |
| `credux refresh` | Re-resolves active sessions close to expiry. `--all` re-resolves every active session; `--threshold` overrides the window. |
| `credux env <profile>` | Prints the `export AWS_PROFILE=...` statement for your shell to `eval`. `--unset` to clear it; `--shell` to pick a syntax. |
| `credux shell-init` | Prints the `cdx` shell function and tab-completion registration. `--shell posix\|fish\|powershell`. |
| `credux console <profile>` | Opens the AWS Management Console for that credential in a browser (container by default). |
| `credux login <session>` | Runs the Identity Center device-authorization flow and caches the token. |
| `credux sync [session]` | Discovers Identity Center accounts and permission sets, creates/renames profiles, then runs integrations and `post_sync`. `--prune` removes profiles for roles you lost access to. |
| `credux config path\|show\|get\|set\|edit` | Inspect and modify `config.json`. See [configuration.md](configuration.md). |
| `credux doctor` | Read-only diagnostics. See [troubleshooting.md](troubleshooting.md). |
| `credux upgrade` | Replaces this installation with another release. See [releasing.md](releasing.md). |

`--mfa-code` is a global option on `credux` itself, so it can be supplied
non-interactively to any command that ends up resolving an MFA-protected
credential.

## Profile arguments are searched, not spelled out

Every command that takes a profile name (`start`, `stop`, `env`, `console`,
`remove`, and `add iam-role --source`) searches for the name instead of requiring
it verbatim. The term matches anywhere in the profile name, case insensitively,
so `credux console billing` finds `acme-billing-User-ada`. One match is used
straight away; several matches are listed and you are asked which one you meant:

```
$ credux console acme
'acme' matches 2 credentials:
  1) acme-billing-User-ada
  2) acme-dev-Role-Admin
Which one:
```

A name that matches a profile exactly is never treated as a search term, so
`acme-dev` still means `acme-dev` even when `acme-dev-Role-Admin` also exists.

The list and the question are printed on stderr, so `eval "$(credux env NAME)"`
stays safe to use. When there is nothing to read an answer from (a script, a
pipe, a cron job), an ambiguous term is a usage error naming the candidates
rather than a prompt nobody can answer.

## Starting over: `credux remove --all`

`credux remove --all` deletes every credential, every secret those credentials
own in the keychain, every session they have open in `~/.aws/credentials`, and
every Identity Center session registered in `config.json`. The sessions go too
because leaving them would make the wipe look undone: the next `credux sync`
would recreate every SSO-derived credential you just removed.

It cannot be confirmed with `--yes`, and cannot be combined with a profile name.
`--yes` is a blanket "assume yes" that scripts and aliases carry around for the
single-profile form, and letting it answer for `--all` would turn one stray flag
into the loss of everything credux knows about. There is deliberately no
non-interactive path: a human types the phrase, or nothing happens.

```
$ credux remove --all
This deletes 7 credentials, their stored secrets, and 2 Identity Center sessions.
Type 'permanently delete' to confirm:
```

Anything else leaves every file untouched. What credux does not delete: the
`~/.aws/config` and `~/.aws/credentials` content outside its own managed block,
the cached Identity Center tokens under `~/.aws/sso/cache`, and your `naming`
templates, `integrations`, and `post_sync` settings.

## Selecting a profile in your shell: `cdx`

`cdx` is a shell function, not a credux subcommand. A child process cannot
change its parent shell's environment, so `credux env` prints a statement
(`export AWS_PROFILE=...`) for the shell to `eval`, and `cdx` (installed by
sourcing the output of `credux shell-init`) is a two-line wrapper around that.

```bash
eval "$(credux shell-init)"        # put this line in your shell rc, once
cdx acme-billing-User-ada          # sets AWS_PROFILE in *this* shell
cdx                                # no argument: unsets AWS_PROFILE again
```

`credux shell-init` detects your shell automatically, or takes `--shell
posix|fish|powershell`. Note the asymmetry with `credux env`: `credux env
--shell cmd` is supported (it prints a `set VAR=value` statement for `cmd.exe`),
but there is no `cmd` wrapper for `credux shell-init` to print, so `credux
shell-init --shell cmd`, or auto-detecting into `cmd`, is a usage error rather
than silently falling back to something else.

## Tab completion

The same `credux shell-init` output also registers tab completion, so there is
nothing extra to install: `credux li<TAB>` completes to `credux list`, and every
command that takes a profile name completes the profiles you have configured
(`credux start acme-<TAB>`). `login` and `sync` complete the Identity Center
session names from your config. Profile completion matches the same way the
commands themselves do - substring, case insensitive - so anything offered is
something the command will accept.

`cdx` completes profile names too: `cdx mana<TAB>` offers every profile whose
name contains `mana`, the same set `cdx mana<Enter>` would have searched.
Because `cdx` is a shell function rather than a command, the shell cannot learn
about it from credux's own completion script, so each wrapper registers a
completer that asks `credux completions profiles` for the candidates - one
list, so `cdx <TAB>` and `credux env <TAB>` can never drift apart.

Completion is available for bash, zsh, fish, and PowerShell, for both `credux`
and `cdx`. The `posix` wrapper serves both bash and zsh, and their completion
scripts are not interchangeable, so it carries both and picks the right one when
your shell starts. Click ships no PowerShell completion script, so credux
supplies its own backend for it; the one gap is Windows PowerShell 5.1, which
does not consult a completer while the token under the cursor is a bare `-` or
`--`, so `credux env --<TAB>` offers nothing there while `credux env --s<TAB>`
completes to `--shell`. PowerShell 7 has no such gap. `cmd` has neither a
wrapper nor completion.

If completion does not appear, the usual causes are a shell that has not re-read
its startup file (open a new one) or a stale command hash from before credux was
installed (`hash -r` in bash, `rehash` in zsh). The latter is also why
`cred<TAB>` may not offer `credux` itself in a shell that predates the install.

## Identity Center (AWS SSO)

Register the session once and let credux discover every account and permission
set you can reach:

```bash
credux add sso-session acme --start-url https://acme.awsapps.com/start --sso-region eu-west-1
credux login acme            # opens the device-authorization page in a browser
credux sync acme             # discovers accounts/roles, creates/renames profiles
credux sync acme --prune     # also removes profiles for roles you lost access to
```

`credux sync` with no session name syncs every configured session. Without
`--prune`, a credential that vanished from Identity Center is left alone, so you
never lose an in-progress rename or a manual tweak by surprise. With `--prune` it
is deleted from credux's registry, and its profile is also removed from
`~/.aws/credentials` and `~/.aws/config` on the next `start`/`stop`/`refresh`.

`credux login` and `credux sync` need working device-authorization support in
your terminal: `login` prints the confirmation code and tries to open the
verification URL in a browser, so it needs *some* way to reach a browser, either
locally or by copying the printed URL elsewhere.

Profile names for discovered roles come from the `naming.iam_sso` template,
which starts out as credux's default and is meant to be replaced
(`credux config set naming.iam_sso '<template>'`). Changing it later is safe:
the next `credux sync` renames the existing credentials rather than duplicating
them. How renames, collisions, and the management-account prefix behave is in
[configuration.md](configuration.md).

## Multiple sessions at once

Multiple credentials can be active at once: `credux start` and `credux stop` only
touch their own profile's entry, leaving every other active session in place.
`credux start` takes several names at once, and `credux start --all` starts every
configured credential (the mirror image of `credux stop --all`):

```bash
credux start acme-dev-SSO-Admin acme-test-SSO-Admin
credux start --all
```

`--all` resolves everything first and then writes `~/.aws/credentials` once,
rather than rewriting it per credential. A credential that cannot be resolved -
an expired Identity Center token, a denied `AssumeRole` - is reported on stderr
and costs only itself: every other credential is still started, and the exit code
is that first failure's own code (see
[troubleshooting.md](troubleshooting.md#exit-codes)). It is an error to combine
`--all` with profile names, and `credux start` with neither is a usage error.

```bash
credux list          # human-readable table, with time-to-live
credux list --json   # for scripting
```

## Keeping sessions fresh

`credux refresh` re-resolves only the active sessions that are within
`settings.refresh_threshold_minutes` (default 10, override with `--threshold`) of
expiring. `credux refresh --all` re-resolves *every* active session
unconditionally, regardless of how much time is left, which is what you want on a
timer.

**systemd (Linux)**, `~/.config/systemd/user/credux-refresh.timer`:

```ini
[Unit]
Description=Run credux refresh periodically

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
```

`~/.config/systemd/user/credux-refresh.service`:

```ini
[Unit]
Description=Refresh active credux sessions

[Service]
Type=oneshot
ExecStart=%h/.local/bin/credux refresh --all
```

Enable with `systemctl --user enable --now credux-refresh.timer`.

**Windows (Task Scheduler)**, from an elevated prompt:

```bat
schtasks /Create /SC MINUTE /MO 15 /TN "Credux Refresh" /TR "credux refresh --all"
```

### Do not schedule a refresh for an MFA credential

A scheduled refresh only works unattended for credentials that need no
interactive input. That is a property of a credential's *configuration*, not its
kind: **any** credential with `params.mfa_serial` set - an IAM user resolved
directly, or a chained IAM role that itself requires MFA to assume - needs a
fresh MFA code on every resolve, including a scheduled one.

Identity Center credentials need no interactive input as long as the cached
Identity Center token has not itself expired; if it has, `refresh` raises and
tells you to `credux login` again.

Watch for this failure mode in a timer's logs. A systemd or cron job normally
runs with no controlling terminal, so an unattended refresh that hits an
`mfa_serial` credential does not hang: it fails immediately with `Aborted!` on
stderr and exit code 1, because reading the absent MFA code from stdin raises
end-of-file and credux treats that the same as a user cancelling the prompt.

### Concurrency caveat

Credux takes no lock on `~/.aws/credentials`, `~/.aws/config`, `config.json` or
`state.json`. Each individual write is atomic (a temp file plus `os.replace`), so
no reader ever sees a half-written file and a crash cannot corrupt the previous
version - but read-modify-write as a whole is not.

If a scheduled `credux refresh --all` fires at the same moment you run `credux
start` in a terminal, both read the managed block, each computes its own new
version of it, and the one that writes second wins: the other's change is simply
lost. Nothing is corrupted and the next `start`/`stop`/`refresh` puts things
right, but pick a timer interval that is unlikely to collide with interactive use,
and re-run the command if a profile you just started is not there.

## The AWS console: `credux console`

`credux console <profile>` resolves credentials, obtains a one-time AWS console
sign-in URL, and runs `settings.browser_command` with the profile name and the
URL substituted into it.

The default command opens the console in a Firefox container named after the
profile, so two accounts opened side by side never share cookies or an active
session in the browser:

```json
["firefox", "ext+container:name={profile_encoded}&url={url_encoded}&color={color}&icon={icon}"]
```

That default needs two Firefox extensions, not one:

| Extension | What it provides |
| --- | --- |
| [Multi-Account Containers](https://addons.mozilla.org/en-US/firefox/addon/multi-account-containers/) | The containers themselves, and the UI that manages them. |
| [Open external links in a container](https://addons.mozilla.org/en-US/firefox/addon/open-url-in-container/) | A handler for the `ext+container:` URL scheme, which Firefox does not understand on its own. |

Any equivalent pair works: the second one is one of an "open a link in a
container" family, and the first is what the family is built on. With neither, or
with only the handler, the link will not open a container at all.

A second extension which intercepts AWS sign-ins and re-homes the tab (the `AWS
SSO Containers` family) will fight this one: it closes the tab credux placed and
opens its own, in a container it named itself. Run one container mechanism, not
two.

Every credential kind goes through the AWS federation endpoint, Identity Center
ones included: the URL handed to the browser carries its own one-shot sign-in
token, so it establishes the session by itself inside whatever container it opens
in. It never depends on a cookie already being there, which a container is
designed not to have.

Point `settings.browser_command` elsewhere for a different browser, a different
containers extension, or no isolation at all:

```bash
credux config set browser_command '["google-chrome", "--profile-directory={profile}", "{url}"]'
```

The available placeholders and `settings.console_destination` are documented in
[configuration.md](configuration.md#browser_command).

## Integrations

`credux sync` renders every enabled entry in `integrations` and then runs every
command in `post_sync`, even if one of them fails: it prints one line per
integration and per hook, and only exits non-zero (exit code 7) after everything
has had a chance to run.

What they can be configured to do, and the allowlist of non-secret fields a
template or hook can see, is in
[configuration.md](configuration.md#integrations).
