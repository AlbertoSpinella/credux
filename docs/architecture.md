# Architecture

Credux is a CLI that manages cloud credentials (IAM users, chained IAM roles,
IAM Identity Center) and materializes the active ones into `~/.aws/credentials`
under template-derived profile names.

This document states the rules the existing code and tests were built to, not
just a description of what happens to be there today. Read it before changing
structure.

## The layering rule

**Only code under `src/credux/providers/` knows a specific cloud exists.**

Everything else - `cli.py`, `app.py`, `sessions.py`, `sync.py`,
`integrations.py`, `doctor.py`, `model.py`, `naming.py`, `awsfiles.py`,
`completion.py` - works in terms of a provider-neutral `Credential` (an `id`, a
`kind`, a `profile`, a `provider` name, an optional `region`, and an opaque
`params` dict that only that credential's own provider interprets) and a
`ResolvedCredentials` (access key, secret key, optional session token, optional
expiration, optional region).

`ResolutionContext` dispatches to `provider_for(cred).resolve(...)`. Nothing
above that layer branches on `cred.kind` or `cred.provider` itself, except the
naming module's `TEMPLATE_KEYS` mapping (kind -> naming-template key), which is
provider-neutral by construction and does not import anything AWS-specific.

If you find yourself importing `boto3`, `botocore`, or anything under
`providers/aws/` from outside `providers/`, stop: that logic belongs inside a
provider.

Boto3 clients are never constructed directly by callers that need to be
testable. Everything goes through a `client_factory(service, region=,
credentials=)` callable, threaded through `ResolutionContext`, so tests can
substitute a stubbed client. `providers/base.default_client_factory` is the only
place that calls `boto3.client(...)` for real.

That one place builds its own botocore session with the `profile` variable
detached from the environment. credux writes profile names, it never reads
them: `credux env` exports `AWS_PROFILE` for the user's shell, and boto3's
default session would read that export straight back in, so a profile that is
stale or not yet written would break the very command that writes it -
`credux login` died with `ProfileNotFound` before reaching the network. Every
other AWS environment variable is left alone.

## Invariants

These must hold after any change, not just before your change shipped.

### 1. The fake `default` profile is always re-asserted

Every write to `~/.aws/credentials` (`SessionManager._write`, called from
`start`, `stop`, `stop_all`, `rename`, `refresh`) re-adds a `default` profile
from `settings.default_profile`, or omits it if disabled. This exists so a
command run without `--profile`/`AWS_PROFILE` fails loudly against fake keys
instead of silently hitting whatever real credentials would otherwise occupy
that slot.

Do not add a code path that writes the credentials file without going through
`awsfiles.write_credentials`.

### 2. A managed region preserves everything outside it

`awsfiles.py` (for `~/.aws/credentials` and `~/.aws/config`) and
`integrations.py` (for integration destinations in `region` mode) each own a
marker-delimited block and must copy through, byte for byte, whatever a human
wrote outside it. See the duplicated-marker-search trap in
[testing.md](testing.md) before touching either implementation.

Two consequences of that boundary are things only the pre-write text can tell
you, so `awsfiles` returns them as a `WriteReport` rather than dropping them:
the one-time backup (taken only when the existing text has *no* managed block,
so it is always the pre-credux file and never a snapshot of credux's own
output), and whether a human-written `default` profile now sits outside the
block alongside the placeholder credux writes inside it. `SessionManager` keeps
the last report on `notices`/`config_notices`; `cli._report_file_notices` is the
only thing that turns either into text, because neither `awsfiles` nor
`sessions` has an output channel.

### 3. Secrets live in the keychain and, transiently, in the AWS credentials file

Nowhere else. Never in `config.json`, `state.json`, a log line, an exception
message, a rendered integration template, or a `post_sync` hook's input.

`integrations._context()` builds the only view of a credential that templates
and hooks ever see, and it is an explicit allowlist (`_SAFE_PARAM_KEYS`) of
field *names*, not a denylist of things that look secret. A new `params` key is
invisible to integrations until someone deliberately adds it to that allowlist.

### 4. Writes to `config.json`, `state.json`, and the AWS files are atomic and `0600`

`fsutil.atomic_write_text` writes to a temp file in the same directory and
`os.replace`s it into place, so a reader never observes a partial file and a
crash mid-write cannot corrupt the previous version.

Integration destinations are the one deliberate exception: they are `0644`
because they are meant for other programs to read and never carry secret
material.

### 5. A credential's `id` never changes; its `profile` is derived and can

`id` is how a rename is distinguished from a delete-and-recreate: `sync`
identifies a discovered Identity Center role by `(session, account_id,
role_name)`, not by name, so a naming-template edit renames the existing record
(same `id`) instead of creating a duplicate.

Anything that treats `profile` as a stable primary key rather than `id` is
wrong.

## Module map

| Module | Responsibility |
|---|---|
| `cli.py` | The Click command tree; translates CLI arguments into calls on `App`, `Registry`, `SessionManager`, `sync`, `integrations`, `doctor`. |
| `app.py` | `App`, built once per invocation: owns `Config`, `Registry`, `State`, and the `client_factory`; builds a `ResolutionContext` or `SessionManager` on demand. |
| `config.py` | `Config`: the parsed `config.json` (settings, naming templates, `sso_sessions`, `credentials`, `integrations`, `post_sync`), with defaults deep-merged in. Metadata only, never secrets. |
| `model.py` | `Credential` and `Registry`: add/replace/remove, plus `search` and `resolve`. |
| `state.py` | `State` and `ActiveSession`: which profiles are currently materialized and when they expire. Credux's own bookkeeping, not AWS-facing. |
| `store.py` | The only module that touches `keyring`; wraps every call to turn a `KeyringError` into `SecretStoreError`. |
| `awsfiles.py` | Reads and writes the managed region of `~/.aws/credentials` and `~/.aws/config`. |
| `sessions.py` | `SessionManager`: the only writer of the AWS files and `state.json`. |
| `sync.py` | Diffs Identity Center discovery against configured credentials (`plan_sso_sync`) and applies the result (`apply_plan`). |
| `integrations.py` | Renders Jinja2 templates and runs `post_sync` shell hooks from the non-secret credential list. |
| `naming.py` | Pure `str.format`-based profile-name rendering; no IO, no AWS. |
| `doctor.py` | Independent read-only diagnostics; each one wrapped in `_safe` so one crashing check cannot take the rest down. |
| `completion.py` | Shell tab completion: renders Click's completion script and supplies the `shell_complete` callbacks. |
| `shellenv.py` | Renders the `export`/`set` statement `credux env` prints, per shell family. |
| `browser.py` | Renders and launches `settings.browser_command` for `credux console`. |
| `upgrade.py` | `credux upgrade`: resolves a release version, works out which installer owns this installation, runs it. |
| `fsutil.py` | `expand` (`~`/env-var expansion), `atomic_write_text`, `backup_once`. |
| `errors.py` | `CreduxError` subclasses, each carrying the process exit code it maps to. |
| `providers/base.py` | `Provider` ABC, `ResolvedCredentials`, `ResolutionContext` (caching + cycle detection), the provider registry. |
| `providers/aws/__init__.py` | `AwsProvider`: dispatches by `cred.kind` to `iam_user` / `iam_role` / `sso`; builds console URLs via `federation.py`. |
| `providers/aws/iam_user.py`, `iam_role.py`, `sso.py` | One resolver per credential kind. |
| `providers/aws/sso_login.py` | The Identity Center OIDC device-authorization flow and its cached-token store. |
| `providers/aws/federation.py` | Exchanges credentials for a console sign-in URL via the AWS federation endpoint. |
| `providers/aws/org.py` | Best-effort Organizations management-account detection; a denied or failed API call means "unknown", never a crash. |
| `templates/` | Built-in Jinja2 integration templates, shipped as package data. |
| `shell/` | The `cdx` shell-function wrappers printed by `credux shell-init`, one per shell family. Also package data. |

Every module starts with a docstring stating its single responsibility. Public
functions carry type hints.

## Notes on individual modules

**`model.py`.** `Registry.search` returns the exact profile-name match if there
is one, else every case-insensitive substring match, sorted. `Registry.resolve`
is the non-interactive form: exactly one match or a `UsageError`. `cli._find` is
the interactive form: it asks which match was meant, on stderr, and turns a
declined or unanswerable question back into `resolve`'s ambiguity error.

**`sessions.py`.** `start_all` is the batch form behind `credux start --all`: it
resolves every credential before writing anything, writes once, and returns a
`StartOutcome` carrying the successes and the per-credential `CreduxError`s
instead of aborting on the first failure. It writes nothing when nothing
succeeded, so an all-failed (or empty) batch does not truncate the managed
region just to re-assert the fake `default`. `rename` exists because both AWS
files key on the profile *name* while the registry keys on `id`, so `sync`
renaming a credential must move the live session too or strand it (invariant 5).

**`sync.py`.** Pure planning is separated from mutation on purpose:
`plan_sso_sync` never touches the registry, so it can be inspected or rejected
before anything changes.

**`integrations.py`.** `run_all` attempts every integration and hook regardless
of earlier failures and reports what happened as an `IntegrationOutcome`.

**`naming.py`.** Rejects anything that could produce a partially-filled or
malformed name rather than emit one. `validate_template` is the half of that
which needs no credential: it is what `credux config set naming.<key>` calls
before storing a template, so a bad one is refused when it is typed rather than
when it would first have failed to name something.

**`completion.py`.** Three things constrain it. First, bash's and zsh's completion
scripts are not mutually parseable (bash fails on zsh's `for type key descr in
...` at *parse* time, so an `if [ -n "$ZSH_VERSION" ]` guard around both would
not help: bash parses the whole compound command before running any of it), and
`shellenv` collapses both into one "posix" wrapper, so `posix_snippet` emits
each script as quoted-heredoc *data* and lets the running shell `eval` only its
own. Refusing to emit a script that contains the heredoc delimiter is a security
property, not tidiness: an early-closed heredoc hands the remainder to the shell
as startup commands. Second, a completer runs under Click's resilient parsing,
so the group callback has not run and there is no `App` on the context. It loads
`Config` itself and swallows every failure to return an empty list, because a
traceback would be printed over the user's prompt line at the moment they
pressed TAB.

Third, Click ships completion backends for bash, zsh and fish only, and what it
is missing for PowerShell is the *script*, not the mechanism: the protocol is
two environment variables in and one line per candidate out. So `completion.py`
registers a `PowerShellComplete` with `click.shell_completion.add_completion_class`
rather than special-casing PowerShell in `shell-init`, and everything above it -
`source()`, the `_CREDUX_COMPLETE` dispatch in the installed binary - serves it
through Click's own machinery unchanged. Its answer is tab-separated, one
candidate per line, because that is what PowerShell can split without a parser,
so `format_completion` strips both characters out of every field. `cmd` still has
no backend, and `source()` returning `""` for it is the case that stays.

Completing `cdx` is a fourth constraint. It is a shell function, so Click's
script - which registers itself against the `credux` command alone - never sees
it, and each wrapper carries a completer of its own. Those completers ask the
hidden `credux completions profiles` command, which calls the same
`complete_profiles` the `credux env` argument uses, so the two lists cannot
drift. The bash and zsh completers live in `shell/cdx.bash` and `shell/cdx.zsh`
rather than in `credux.sh`: they ride the same quoted heredoc as Click's
scripts, which keeps `credux.sh` parseable by any POSIX shell. They read only
stdout and discard stderr and the exit status - the group callback loads the
config eagerly, so a broken one still fails, and a diagnostic offered as a
candidate would be inserted into the command line on TAB.

**`upgrade.py`.** Planning (`plan`) is separated from mutation (`apply`) the
same way `sync.py` separates them. It refuses an editable install and a non-venv
install rather than writing over either. The CLI command prints nothing computed
after `apply` returns: at that point the venv the process is running from has
been replaced, so a not-yet-imported module may be gone. See
[releasing.md](releasing.md).

## Resolution and chaining

`ResolutionContext.resolve(cred)` is how every credential becomes usable
material. It:

1. Returns a cached `ResolvedCredentials` if this credential was already
   resolved during this context's lifetime. One context exists per CLI
   invocation, so this only matters within a single command, e.g. resolving a
   role's source once even if two other roles chain through it.
2. Detects cycles: it pushes `cred.id` onto a stack before resolving and pops it
   after. If `cred.id` is already on the stack, resolution raises
   `ResolutionError` naming the whole chain instead of recursing forever.
3. Delegates to `provider_for(cred).resolve(cred, self)`, which for AWS
   dispatches by `cred.kind`.

An `iam-role` credential's `resolve()` calls `ctx.resolve_id(params.source)` to
obtain its parent's material before calling `sts:AssumeRole`. That recursive
call is what makes chains of arbitrary depth work, and it is also what the cycle
guard above protects. `iam-user` and `sso` credentials are leaves: they never
call back into `ctx.resolve`.

## Repo-wide rules

- Python >= 3.10. Runtime dependencies are limited to `boto3`, `click`, `rich`,
  `keyring`, `jinja2`; the test dependencies are `pytest` and `build`, the
  second only so `tests/test_packaging.py` can build a real sdist and check
  what it contains. Do not add a new runtime or test dependency without
  updating this list and `pyproject.toml` together.
- Nothing personal, corporate, or customer-specific anywhere in the repo except
  the `LICENSE` copyright line and the project URLs in `pyproject.toml`: no real
  names, domains, account ids, or paths. Examples use placeholders such as
  `acme`, `example`, `123456789012`.
- Never use the em dash character in code, comments, or documentation. Use a
  hyphen or a colon instead.
- Secrets never appear in logs, command output, or documentation examples.
- Follow test-driven development: write the failing test first, watch it fail
  for the expected reason, then make it pass. See [testing.md](testing.md).
