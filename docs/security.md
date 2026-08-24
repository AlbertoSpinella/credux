# Security model

## Why a credentials file, and not a wrapper

The tools credux is usually compared to keep secret material off disk. `aws-vault
exec` holds the keys in the keychain and injects them as environment variables
into one child process; a `credential_process` entry in `~/.aws/config` has the
SDK call a helper and read the keys from its stdout. Credux instead writes the
active session into `~/.aws/credentials`. That is a deliberate trade, and this
is the whole of it.

**What the file buys.** Every tool that reads a profile works, with no
per-tool integration and no re-launch: Terraform and its providers, boto3 in a
notebook you started an hour ago, k9s, an IDE's AWS plugin, a GUI client, a
`Makefile` target, a container that bind-mounts `~/.aws`, a script a colleague
sent you. A wrapper covers only the process it spawns, so anything long-running
or started outside it - the editor, the notebook kernel, the daemon - is not
covered by it. `credential_process` covers more than a wrapper does, at the
cost of invoking a helper on every SDK initialisation: keychain prompts arrive
at unpredictable moments, a truncated `PATH` (IDEs, cron, containers) breaks it
in ways whose error text names the SDK rather than the cause, and you still end
up editing `~/.aws/config` to declare it.

**What the file costs.** The keys for *active* sessions are readable by anything
that can read a file owned by your user. That is a real cost, and it is not the
same as "no worse than a wrapper":

- Backup and sync software picks the file up. A home directory in Dropbox,
  rclone, Time Machine, or a `tar` of `$HOME` now carries live credentials.
- Container bind-mounts of `~/.aws` hand the file to whatever runs inside.
- The exposure lasts as long as the session, not as long as one command.

Some of the gap is narrower than it looks. On Linux, any process running as you
can read `/proc/<pid>/environ` of a wrapper's child, and can read the keychain
through the same Secret Service API credux and aws-vault both use once the
keychain is unlocked. An attacker already executing code as your user is in a
strong position against every tool in this category. The difference is exposure
at rest and to software that was never attacking you.

**What credux does about the cost.** Only active sessions are written - an IAM
user's long-term keys reach the file only while a session using them is started,
and `credux stop` removes them. Identity Center and role sessions carry an
expiry and stop working on their own. The files are written atomically at
`0600`, and `credux doctor` reports looser permissions. Long-term secrets never
leave the keychain: what lands on disk for a role or an Identity Center
permission set is a temporary session token, not the credential that minted it.

**Use something else if** your threat model includes other local users, malware
running as you reading files at rest, or a policy that forbids plaintext
credentials on disk at all. `aws-vault` is the better fit for that, and credux
will not talk you out of it. Credux is built for the case where the machine is
trusted and the friction of a wrapper is the thing actually costing you.

## Where secrets live

**Long-term secrets live in the OS keychain only**, accessed through the
`keyring` package (macOS Keychain, Windows Credential Manager, or a Linux Secret
Service provider such as gnome-keyring or kwallet). IAM user access keys and
cached Identity Center tokens are stored there, never in `config.json`.

**`config.json` and `state.json`** (under `CREDUX_HOME`, `~/.credux` by default)
hold only metadata: credential definitions (kind, profile name, non-secret
`params`), naming templates, integration specs, and which profiles are currently
active and when they expire. Neither file ever contains an access key, secret
key, or session token.

**`~/.aws/credentials` and `~/.aws/config` do carry real, usable secret material
in plain text**, by necessity: that is the only format AWS SDKs read profiles
from. It is confined to the credux-managed block, it is only the material for
currently-*active* sessions (an IAM user's long-term keys are only written there
while a session using them is started), and both files are written atomically
with `0600` permissions. `credux doctor` flags a `credentials` file it finds with
looser permissions.

## The one-time backup

The first time credux modifies an existing `~/.aws/credentials` (or
`~/.aws/config`), it copies the file to `<path>.credux.bak` so nothing you had
before is lost to a first run, and prints the path it wrote it to. Nothing is
overwritten in the original: credux replaces only the text between its managed
block markers and preserves the rest byte for byte.

The copy is taken only while the file still has no managed block, so it is
always the file as it was *before credux existed on this machine*. A file that
already carries the markers is never backed up again: doing so would snapshot
credux's own output, live session tokens included, and would silently put a
credux-written file under the `.credux.bak` name for anyone who had deleted the
real one.

That copy therefore contains whatever real credentials were already in the file.
It is written `0600` and never touched again: credux does not refresh or rotate
it. `credux doctor` checks its permissions alongside the live file's. Delete it
once you are satisfied credux is behaving; nothing depends on it.

## A `default` profile you wrote yourself

Credux writes a placeholder `default` profile inside its managed block (see
[configuration.md](configuration.md#default_profile)). If the file already had a
`default` profile of your own outside that block, the file now holds two, and
which one an AWS SDK uses is decided by that SDK's parser rather than by credux.

Credux warns about this on the write that creates the situation and `credux
doctor` reports it as `shadowed default profile` for as long as it lasts. Fix it
by renaming your own profile, or turn credux's off with `credux config set
default_profile.enabled false`.

## Integration destinations are not secret files

They are written world- and group-readable (`0644`) rather than `0600`, because
they are meant for other programs (a shell rc, a launcher's config) to read, and
they never receive secret material regardless.

Templates and `post_sync` hooks see only an allowlist of non-secret fields,
enforced by field *name* rather than by guessing which values look secret. See
[configuration.md](configuration.md#what-a-template-can-see).

## The console sign-in URL is briefly visible in the local process list

`credux console` passes the URL as a plain command-line argument to the browser
process it launches. It has to: that is how `browser_command` invokes the
browser. So for as long as that process is starting, any other local process or
user able to read process argv (e.g. `ps -ef` on a shared machine) can see the
URL, which embeds a short-lived sign-in token.

Credux itself never prints or logs the URL.

## Rules the code is held to

These are enforced by the codebase's own invariants rather than by convention;
see [architecture.md](architecture.md#invariants) if you are changing credux
rather than using it.

- A secret never reaches `config.json`, `state.json`, a log line, an exception
  message, a rendered template, or a hook's input.
- Every write to `~/.aws/credentials` goes through `awsfiles.write_credentials`,
  which re-asserts the fake `default` profile so a command run without a profile
  fails loudly instead of using real credentials that happened to be in that
  slot.
- Writes to `config.json`, `state.json`, and the AWS files are atomic and
  `0600`.
