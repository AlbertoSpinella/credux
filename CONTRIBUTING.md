# Contributing to credux

Issues and pull requests are welcome. Read this first, because the workflow is
not the usual one.

## How changes reach a release

Development happens in a separate tree, and what arrives in this repository is
the released state: one commit per release, which is why the history is short
and every commit is a version.

A pull request is therefore read as a **proposal** rather than as something
merged into this branch. An accepted one is applied upstream and appears in the
next release commit, credited in the discussion it came from. Nothing is lost
by opening one - it just will not carry your commit SHA into `main`.

If that model does not suit you, an issue describing the change is worth as
much as the patch.

## Before you write code

Read [docs/architecture.md](docs/architecture.md) for the layering rule and the
invariants any change must preserve, and [docs/testing.md](docs/testing.md) for
the test conventions and the traps this project has already hit. A change that
breaks an invariant will not be accepted no matter how well it works.

The project follows strict test-driven development: the test comes first, it
fails for the right reason, then the code makes it pass.

## Setting up

```bash
git clone https://github.com/AlbertoSpinella/credux.git
cd credux
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

The suite must be green before and after your change. It runs offline: no
test reaches AWS, and none touches your real `~/.aws` files or keychain.

## Good issues

- **Bug reports:** include your OS, `credux --version`, the exact command, and
  the output of `credux doctor`. Redact account IDs if you would rather not
  publish them - the shape of the failure is what matters.
- **Feature requests:** describe the workflow you are stuck on rather than the
  flag you want. Several of credux's commands exist in a different form from
  the one first proposed for them.
- **Another cloud provider:** [docs/providers.md](docs/providers.md) describes
  the seam. Say which provider and which credential kinds before writing it.

## Never in an issue, a PR, or a test fixture

Real access keys, session tokens, Identity Center start URLs for a live
tenant, or account IDs you are not free to publish. Credux exists to keep that
material out of files that get shared; do not let a bug report be the leak. If
you have already pasted one, rotate it - a redacted edit does not undo the
notification, the email, or the mirror.
