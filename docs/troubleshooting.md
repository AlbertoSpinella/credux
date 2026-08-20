# Troubleshooting

## `credux doctor`

Run `credux doctor` for a summary of everything credux can check:

- the active keychain backend
- the credentials file and its permissions
- whether the fake `default` profile is in place
- whether a `default` profile you wrote yourself is competing with it
- whether `browser_command`'s binary is on `PATH`
- dangling IAM-role chains
- whether every stored credential still renders under the current naming
  templates
- whether any active session has expired

Each check reports `ok`, `warn`, or `fail`. `credux doctor` exits with code 1 only
if at least one check is `fail`. A `warn` still exits 0, since it is advisory:
"browser not found" does not stop anything else from working.

## Headless Linux and WSL: no keychain

The `keyring` package needs a running Secret Service provider, or an equivalent
OS-native backend, to store anything. Neither a bare server nor a default WSL
session normally has one running, and `credux doctor` reports the "secret store"
check as `fail` in that case.

Fixes, best first:

- Start one yourself, e.g. `gnome-keyring-daemon --start --components=secrets`.
  You may need to unlock or initialize its keyring the first time. Install and
  enable `kwalletd` instead if you use KDE.
- On WSL specifically, a graphical login session is not started automatically.
  Starting `gnome-keyring-daemon` in your shell's startup file works, but only
  lasts for that shell session unless you arrange for it to run at login.
- As a last resort, a file-based keyring backend (e.g. `keyrings.alt`) will
  satisfy `keyring`'s API without a Secret Service daemon, but stores secrets
  less securely than a real OS keychain. Understand that trade-off before relying
  on it.

## Tab completion does not fire

Usually one of two things: a shell that has not re-read its startup file (open a
new one), or a stale command hash from before credux was installed (`hash -r` in
bash, `rehash` in zsh). See [usage.md](usage.md#tab-completion).

## A profile you just started is missing

Credux takes no lock on the files it writes, so a scheduled `credux refresh
--all` firing at the same moment as an interactive `credux start` can lose one of
the two changes. Nothing is corrupted; re-run the command. See
[usage.md](usage.md#concurrency-caveat).

## A scheduled refresh fails with `Aborted!`

The credential has `params.mfa_serial` set, so resolving it needs a fresh MFA
code, and a timer has no terminal to read one from. Do not schedule refresh for
MFA credentials. See
[usage.md](usage.md#do-not-schedule-a-refresh-for-an-mfa-credential).

## `credux sync` stops with two account ids

Your configured or answered management account contradicts what
`organizations:DescribeOrganization` reports. This is fatal on purpose, because
guessing wrong renames profiles in both directions. See
[configuration.md](configuration.md#management-account-prefix).

## `credux sync` refuses the plan over a name collision

Two accounts render to the same profile name under the current `naming.iam_sso`
template. Add a distinguishing variable such as `{account_id}`, or re-run with
`--prune` if the collision is with a credential that no longer exists in Identity
Center. See
[configuration.md](configuration.md#re-templating-an-existing-credential).

## Exit codes

Read directly off the error that was raised.

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Generic error, or a confirmation prompt was declined/aborted |
| 2 | Usage error (bad arguments, ambiguous or unknown profile name, ...) |
| 3 | Configuration error (malformed `config.json`, bad naming template, sync collision, ...) |
| 4 | Resolution error (a credential could not be turned into usable material) |
| 5 | Secret store error (the OS keychain is unavailable or refused an operation) |
| 6 | Identity Center token error (missing or expired; run `credux login`) |
| 7 | An integration or `post_sync` hook failed during `credux sync` |
| 8 | `credux upgrade` could not replace this installation |
