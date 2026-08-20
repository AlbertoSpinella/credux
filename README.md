# credux

Credux manages your AWS credentials and materializes the ones you are actively
using into `~/.aws/credentials` and `~/.aws/config`, under profile names it
derives from a template you control.

It understands three kinds of credential:

- long-term **IAM user** access keys, kept in your OS keychain;
- **IAM roles** assumed on demand, optionally chained through other credux
  credentials;
- **IAM Identity Center** (AWS SSO) permission sets, discovered automatically
  from your Identity Center account.

Any tool that takes `--profile` or reads `AWS_PROFILE` works with the result: no
wrapper, no credential process, no AWS CLI installation required.

## Requirements

- Python 3.10 or later
- An OS keychain: macOS Keychain, Windows Credential Manager, or a Linux Secret
  Service provider such as gnome-keyring or kwallet (see
  [troubleshooting](docs/troubleshooting.md#headless-linux-and-wsl-no-keychain)
  for headless Linux and WSL)
- For `credux console`'s default browser command: Firefox plus *two* extensions,
  [Multi-Account Containers](https://addons.mozilla.org/en-US/firefox/addon/multi-account-containers/)
  for the containers themselves and
  [Open external links in a container](https://addons.mozilla.org/en-US/firefox/addon/open-url-in-container/)
  for the `ext+container:` URL scheme credux hands to the browser. Any other
  browser works by pointing `settings.browser_command` at it.

Runtime dependencies are `boto3`, `click`, `rich`, `keyring`, and `jinja2`.

## Install

Credux is **not published to PyPI**; `pipx install credux` will not find it.
Install it from the repository with [pipx](https://pipx.pypa.io/):

```bash
pipx install git+https://github.com/AlbertoSpinella/credux.git
credux --version
```

pipx keeps credux in its own virtual environment and puts the `credux` command on
your `PATH` (usually `~/.local/bin`). Remove it with `pipx uninstall credux`.

If you do not have pipx: `brew install pipx`, `apt install pipx`, or
`python3 -m pip install --user pipx`.

A plain virtual environment does the same job, at the cost of keeping a clone
around:

```bash
git clone https://github.com/AlbertoSpinella/credux.git
cd credux
python3 -m venv .venv
.venv/bin/pip install .
ln -s "$PWD/.venv/bin/credux" ~/.local/bin/credux
```

### Upgrading

`credux upgrade` replaces this installation with another release, straight from
the repository:

```bash
credux upgrade                   # newest release tag
credux upgrade --version 0.1.0   # a specific release, also a way to repair
credux upgrade --from ./credux   # whatever is checked out in a local clone
```

It drives whichever installer owns the environment credux is running in, and
refuses an editable install or an installation outside any virtual environment
rather than writing over either. Since credux is not on PyPI, the tag lookup and
the install both go to the repository and need git access to it. Details in
[docs/releasing.md](docs/releasing.md#how-credux-upgrade-works).

## Quickstart

```bash
credux add iam-user            # prompts for org, account, user, and keys
credux start acme-billing-User-ada
eval "$(credux shell-init)"    # add this line to your shell rc instead, once
pu acme-billing-User-ada       # sets AWS_PROFILE in *this* shell
aws sts get-caller-identity
credux console acme-billing-User-ada   # open the account in a browser container
```

`credux start` writes the profile into `~/.aws/credentials`, so any tool that
reads a profile can use it right away. `credux shell-init` installs the `pu`
shell function (which sets `AWS_PROFILE` in your current shell) and registers tab
completion for bash, zsh, and fish.

Profile names are searched, not spelled out: `credux console billing` finds
`acme-billing-User-ada`, and an ambiguous term asks which one you meant.

Using IAM Identity Center? Register the session once and let credux discover
every account and permission set you can reach:

```bash
credux add sso-session acme --start-url https://acme.awsapps.com/start --sso-region eu-west-1
credux login acme     # device-authorization flow in your browser
credux sync acme      # discovers accounts/roles, creates/renames profiles
```

Then `credux list` shows everything configured, which sessions are active, and
how long each has left.

## Profile names are a template you own

`acme-billing-User-ada` above is credux's default naming template, not a rule.
It is one house style, and `credux add`/`credux sync` say so until you have
picked your own:

```bash
credux config set naming.iam_sso '{account_name}-{role}'
```

Variables: `org`, `account_name`, `account_id`, `user`, `role`, `subdomain`,
`region`. Identity Center profiles are renamed by the next `credux sync`;
IAM user and role profiles keep the name they were added under. See
[docs/configuration.md](docs/configuration.md#naming).

## Your existing `~/.aws` files

Credux owns one marker-delimited block in `~/.aws/credentials` and
`~/.aws/config`, and preserves everything outside it byte for byte. Before its
first write to a file that already existed, it copies that file to
`<path>.credux.bak` (mode `0600`) and tells you where. See
[docs/security.md](docs/security.md#the-one-time-backup).

## The fake `default` profile

Every write to `~/.aws/credentials` also (re-)writes a profile literally named
`default`, holding deliberately invalid keys (`test`/`test` unless you configure
otherwise).

AWS SDKs and the `aws` CLI silently fall back to `default` whenever no
`--profile`/`AWS_PROFILE` is given. Without that slot pinned to keys AWS will
reject, a script or terminal tab that forgot to select a profile could quietly
run against whatever real credentials happened to be sitting there. With the fake
profile in place, the same mistake fails loudly with an authentication error.

Disable it with `credux config set default_profile.enabled false`. If you had a
`default` profile of your own, credux warns that the two now compete and
`credux doctor` keeps reporting it.

## Documentation

| Document | Covers |
|---|---|
| [docs/usage.md](docs/usage.md) | Every command, profile searching, `pu` and completion, Identity Center, several sessions at once, scheduled refresh, browser containers. |
| [docs/configuration.md](docs/configuration.md) | Every `config.json` key: settings, naming templates, the management-account prefix, integrations, `post_sync` hooks. |
| [docs/security.md](docs/security.md) | Where secrets live and where they never go, file permissions, the one-time backup. |
| [docs/troubleshooting.md](docs/troubleshooting.md) | `credux doctor`, keychain on headless Linux and WSL, exit codes. |
| [docs/architecture.md](docs/architecture.md) | For contributors: the layering rule, the invariants, the module map. |

`examples/config.json` is a complete configuration file to read alongside
[docs/configuration.md](docs/configuration.md).

## Contributing

Issues and pull requests are welcome. The project follows strict test-driven
development.

Development happens in a separate tree, and what arrives here is the released
state: one commit per release, which is why the history is short and every
commit is a version. A pull request is read as a proposal rather than something
merged into this branch - an accepted one is applied upstream and appears in the
next release commit, credited in the discussion it came from.

```bash
git clone https://github.com/AlbertoSpinella/credux.git
cd credux
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

Before changing anything, read [docs/architecture.md](docs/architecture.md) for
the layering rule and the invariants any change must preserve, and
[docs/testing.md](docs/testing.md) for the test conventions and the traps this
project has already hit.

## Licence

MIT; see [LICENSE](LICENSE).
