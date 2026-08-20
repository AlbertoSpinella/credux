# credux documentation

Start at the [project README](../README.md) for what credux is, how to install
it, and a quickstart.

## Using credux

| Document | Covers |
|---|---|
| [usage.md](usage.md) | Command reference, profile searching, `pu` and tab completion, Identity Center, running several sessions at once, scheduled refresh, the AWS console and browser containers. |
| [configuration.md](configuration.md) | Every key in `config.json`: `settings`, naming templates and the management-account prefix, `sso_sessions`, `integrations`, `post_sync`. |
| [security.md](security.md) | Where secrets live and where they never go, file permissions, the one-time backup, the console-URL caveat. |
| [troubleshooting.md](troubleshooting.md) | `credux doctor`, keychain on headless Linux and WSL, common failures, the exit-code table. |

## Working on credux

| Document | Covers |
|---|---|
| [architecture.md](architecture.md) | The layering rule, the five invariants any change must preserve, the module map, resolution and chaining, repo-wide rules. |
| [providers.md](providers.md) | How to add a provider for another cloud. |
| [testing.md](testing.md) | How to run the suite, the test conventions, and the traps this project has actually hit. |
| [releasing.md](releasing.md) | Cutting a release and how `credux upgrade` decides what to do. |

Read [architecture.md](architecture.md) before changing structure and
[testing.md](testing.md) before writing tests.
