# Configuration

Credux keeps its own files under `CREDUX_HOME` (`~/.credux` unless that variable
is set):

| File | Contents |
|---|---|
| `config.json` | Everything on this page. Metadata only, never secrets. |
| `state.json` | Which profiles are currently materialized and when they expire. |

`examples/config.json` in this repository is a complete, commented-by-example
file.

```bash
credux config path         # where config.json is
credux config show         # print it
credux config get <key>    # one settings key
credux config set <key> <value>
credux config edit         # open it in $EDITOR
```

`credux config set` writes under `settings`, plus one named `naming` key at a
time (`credux config set naming.iam_sso '<template>'`). It refuses
`integrations`, `post_sync`, and a bare `naming` rather than writing somewhere
nothing reads; edit those with `credux config edit`.

`config.json` has a numeric `version` field. A file declaring a version newer
than the running credux understands is rejected rather than half-read, and a
non-numeric `version` is a configuration error.

## `settings`

| Key | Default | Meaning |
|---|---|---|
| `aws_credentials_file` | `~/.aws/credentials` | The file whose managed block credux writes profiles into. `~` and environment variables are expanded. |
| `aws_config_file` | `~/.aws/config` | Same, for the profile's region and other non-secret settings. |
| `default_profile.enabled` | `true` | Whether to re-assert the fake `default` profile on every write. See below. |
| `default_profile.access_key_id` | `test` | The fake key written into `default`. |
| `default_profile.secret_access_key` | `test` | The fake secret written into `default`. |
| `browser_command` | Firefox container URL, see below | argv list run by `credux console`, and by `credux login` for a session with a `container`. |
| `refresh_threshold_minutes` | `10` | How close to expiry a session must be before a plain `credux refresh` re-resolves it. |
| `console_destination` | `https://console.aws.amazon.com/` | Where the federation endpoint sends the browser after establishing the session. Set it to a deeper console URL (an account dashboard, a specific service page) to land somewhere more specific. |

### `default_profile`

Every write to `~/.aws/credentials` also re-writes a profile literally named
`default`, using `default_profile.access_key_id` and `secret_access_key`.

This exists because AWS SDKs and the `aws` CLI silently fall back to `default`
whenever no `--profile`/`AWS_PROFILE` is given. Without credux pinning that slot
to keys AWS will reject, a script or terminal tab that forgot to select a profile
could quietly run against whatever real credentials happened to be sitting in
`default`. With the fake profile in place, the same mistake fails loudly with an
authentication error instead.

It is re-asserted on every `credux start`, `start --all`, `stop`, `stop --all`,
and `refresh`, so it cannot be permanently overwritten by hand.

If the credentials file already had a `default` profile of your own outside the
managed block, the file ends up with two, and which one an AWS SDK reads is that
SDK's decision. Credux warns on the write that creates the situation, and
`credux doctor` reports it as `shadowed default profile` until it is resolved.
Rename your own profile, or disable credux's.

Disable it with:

```bash
credux config set default_profile.enabled false
```

### `browser_command`

An argv list. The profile name and the sign-in URL are each available in two
forms, and which one you want depends on where your command puts the value:

| Placeholder | Use it when |
|---|---|
| `{profile}`, `{url}` | The value is its own argv element, e.g. `firefox {url}` or `--profile-directory={profile}`. Substituted verbatim. |
| `{profile_encoded}`, `{url_encoded}` | The value is embedded *inside another URL*, e.g. a container URL's query string. Percent-encoded. |

Getting this wrong is visible immediately: a raw URL inside a container URL's
query string truncates at its first `&`, and a percent-encoded URL handed to a
browser as a plain argument is treated as a search string rather than a URL.

Two further placeholders, `{color}` and `{icon}`, render a Firefox container's
styling. Both are derived from the profile name by hashing it into the eight
colors and twelve icons Firefox accepts, so an account keeps the same color and
icon on every run and every machine, with nothing stored anywhere - and
`...-PROD-...` becomes recognizable at a glance in the tab strip. Leave them out
and the container extension applies its own single fallback pair to everything it
creates, which is how every container ends up the same color.

## `naming`

Every profile name is rendered from a Python `str.format` template.

| Credential kind | Config key | Default template | Example |
|---|---|---|---|
| IAM user | `naming.iam_user` | `{org}-{account_name}-User-{user}` | `acme-billing-User-ada` |
| IAM role | `naming.iam_role` | `{org}-{account_name}-Role-{role}` | `acme-platform-Role-Admin` |
| Identity Center | `naming.iam_sso` | `{org}-{account_name}-SSO-{role}` | `acme-billing-SSO-ReadOnly` |

These defaults are one house style, not a recommendation. Change any of them
with:

```bash
credux config set naming.iam_sso '{account_name}-{role}'
```

Until every template is still the shipped default, `credux add` and `credux
sync` print a one-line reminder on stderr saying which template named the
profile and how to change it. Setting any template silences it for good.

Change it early if you are going to: an `sso` credential is renamed by the next
`credux sync` (the record is keyed by id, not by name), but an `iam-user` or
`iam-role` credential keeps the name it was added under, and only
`credux remove` followed by `credux add` will change it.

Available variables: `org`, `account_name`, `account_id`, `user`, `role`,
`subdomain`, `region`. A template can only use these. An unknown variable, an
empty replacement field (`{}`), a conversion/format spec (`{x!r}`, `{x:>8}`), or
a template using no variables at all (every credential would render to the same
name) is rejected rather than silently accepted. `credux config set` rejects
these when you type them; `config.json` edited by hand is checked when a name is
next rendered.

For an Identity Center credential, `org` and `subdomain` are both set to the
leftmost label of the session's start URL (e.g. `acme` from
`https://acme.awsapps.com/start`). There is no separate way to set `org` for
those credentials.

### Management-account prefix

`naming.management_account_prefix` (default `management_`) is prepended to
`account_name`, not to `org`, whenever a credential is flagged as belonging to
your AWS Organizations management account. The prepend is idempotent: re-running
it will not double the prefix.

Detection differs by kind:

- `credux add iam-user` calls `organizations:DescribeOrganization` with the keys
  you just supplied and compares the result's account id to the one you gave. If
  the caller lacks that permission it prints a warning and assumes "not
  management" rather than failing. Override either way with
  `--management`/`--no-management`.
- `credux add iam-role` never auto-detects. It defaults to "not management"
  unless you pass `--management` explicitly.
- Identity Center credentials are flagged by comparing the discovered account id
  to `sso_sessions.<name>.management_account_id`. You can set that with `credux
  add sso-session --management-account-id ...` or by editing `config.json`, but
  you do not have to: when it is unset, `credux sync` lists the accounts it
  discovered and asks which one is the management account.

Either way the answer is then verified. `credux sync` fetches credentials for one
role in the candidate account and calls `organizations:DescribeOrganization` from
there. A member account may make that call, so any role holding the permission
can name the management account for the whole organization. What happens next:

| Organizations says | `credux sync` does |
|---|---|
| the same account | uses it |
| nothing (no permission, expired token) | warns, uses the candidate anyway |
| a *different* account | reports both ids and stops, exit code 3, changing nothing |

The contradiction is fatal on purpose: naming profiles from the wrong management
account prefixes one account and un-prefixes another, and the next sync with a
corrected answer renames all of them back. A configured id is verified on every
sync too, since an organization can be restructured under a `config.json` that
then quietly renames the wrong profiles.

The question is skipped, with a warning and no prefix, whenever it cannot be
answered - an empty answer, an account id outside what discovery returned, no
stdin at all, Ctrl-C - so an unattended `credux sync` keeps working rather than
blocking on a prompt. Set `management_account_id` in `config.json` to answer it
once instead of on every sync. Both the question and the list are written to
stderr.

### Re-templating an existing credential

Only Identity Center sync actually renames existing credentials. `credux sync`
identifies a credential by `(session, account_id, role_name)`, not by its current
profile name, so editing `naming.iam_sso` and re-running `credux sync` renames the
matching credential in place instead of creating a duplicate.

This can collide. The shipped default templates contain no `{account_id}`, so two
accounts that happen to share the same `account_name` render to the same profile
name. When that happens, `credux sync` refuses to apply the plan at all (a
`ConfigError`, exit code 3) rather than silently dropping one of them - including
when the colliding name is still held by a credential that disappeared from
Identity Center but `--prune` has not been run yet. The error tells you to add a
distinguishing variable such as `{account_id}` to the template, or to re-run with
`--prune`.

IAM user and IAM role profile names, by contrast, are computed once at `credux
add` time and stored as-is. Changing the naming template afterward does not
rename them: `credux doctor` only checks that each credential's stored parameters
still render successfully under the current templates, not that the result
matches the stored name. To rename one, edit `config.json` by hand (`credux config
edit`) or remove and re-add it.

## `sso_sessions`

One entry per Identity Center session, keyed by the name you pass to `credux
login` and `credux sync`:

```json
"sso_sessions": {
  "acme": {
    "start_url": "https://acme.awsapps.com/start",
    "sso_region": "eu-west-1",
    "management_account_id": "111111111111",
    "container": "Acme"
  }
}
```

`management_account_id` is optional; see above for what happens when it is
missing.

`container` is optional too. Without it, `credux login` opens the
device-authorization page in the system's default browser. With it, the page
goes through `browser_command` instead, with the container name in place of
`{profile}` / `{profile_encoded}`, so with the default command it opens inside
that Firefox container. A container that already exists is reused as it is;
one that does not is created, styled from its name like a `credux console`
container.

The setting exists because a browser cannot route this page by itself: AWS
serves the verification page from a regional sign-in host shared by every
Identity Center instance (`<region>.signin.aws`), so a rule that sends a host
to a container cannot tell one session from another. Set it with `credux add
sso-session ... --container NAME`, or `credux config edit` for an existing
session.

## `integrations`

`integrations` and `post_sync` are **top-level** keys in `config.json`, siblings
of `settings` rather than entries inside it.

An integration spec:

| Field | Meaning |
|---|---|
| `name` | Label used in the printed status line. |
| `enabled` | Default `true`; `false` skips it entirely. |
| `template` | `builtin:<name>` for a shipped template (currently `builtin:aliases.sh.j2`), or a filesystem path to your own Jinja2 template. `~` and environment variables are expanded. |
| `dest` | Filesystem path written on every sync. |
| `mode` | `"region"` (default) or `"replace"`. |
| `comment` | Comment-line prefix used to build the managed-block markers in `region` mode (default `#`; use `//`, `--`, etc. for other languages). Ignored in `replace` mode. |

In `region` mode, credux inserts or updates a marked block inside `dest` and
leaves everything else in the file untouched - the right choice for a shell rc
file or any format with comments. In `replace` mode the entire file is overwritten
with the rendered output, which is the only option for formats with no comment
syntax, such as plain JSON.

### What a template can see

A template, and a `post_sync` hook, only ever sees non-secret data: for each
credential, its `profile`, `kind`, `region`, and an allowlisted subset of its
parameters (`account_id`, `account_name`, `role`, `role_name`, `user`,
`sso_session`, `role_arn`).

Both role keys are listed because they come from different places: `credux add
iam-role` stores the short role name you gave it under `role`, while `credux sync`
stores an Identity Center permission set name under `role_name`, so a credential
has one or the other and `null` for the rest.

No access key, secret key, session token, or any other parameter is ever placed
in a rendered file or a hook's input. The allowlist is enforced by field name, so
a future parameter that happens to look secret-shaped still cannot leak through
it.

### The built-in template

`builtin:aliases.sh.j2` renders one shell alias per credential
(`alias credux-<profile>='eval "$(credux env <profile>)"'`), meant to be sourced
from your shell rc in `region` mode.

`examples/config.json` shows it wired up, alongside a disabled
`launcher-shortcuts` entry whose `template` is
`~/.credux/templates/ulauncher-shortcuts.json.j2` - a path in *your own* credux
template directory, not a path inside the credux repository or the installed
package. `examples/ulauncher-shortcuts.json.j2` in this repository is the file
that belongs there: copy it to `~/.credux/templates/ulauncher-shortcuts.json.j2`
(or wherever you keep your own templates, adjusting `template` to match) before
flipping `launcher-shortcuts`'s `enabled` to `true`, or `credux sync` will fail
that integration with "references a missing template" (exit code 7), since
nothing ships a file at that path for you.

That template renders one launcher shortcut per credential, each running `credux
console <profile>`, and uses `replace` mode because JSON has no comment syntax to
hang a managed block off of. Ulauncher is only an example of the mechanism;
nothing about credux is tied to any particular application launcher. Adapting the
template to another launcher, or to a different kind of generated file entirely,
is a matter of writing your own `.j2` file and pointing an integration's
`template` at it.

## `post_sync`

A list of shell commands run after every integration has been rendered. They run
through a shell, so they can be short pipelines or aliases, exactly like an
interactive command, and receive on stdin the same per-credential list described
above, as JSON:

```json
[
  {"profile": "acme-billing-SSO-ReadOnly", "kind": "sso", "region": "eu-west-1",
   "account_id": "123456789012", "account_name": "billing", "role": null,
   "role_name": "ReadOnly", "user": null, "sso_session": "acme", "role_arn": null}
]
```

Every hook runs even if an earlier one failed. `credux sync` prints one line per
hook and exits 7 if any integration or hook failed.
