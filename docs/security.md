# Security model

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
