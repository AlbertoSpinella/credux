# Changelog

Every entry here is also a [GitHub release](https://github.com/AlbertoSpinella/credux/releases),
and every release is one commit: the history of this repository is the list
below. Versions follow [semantic versioning](https://semver.org/); while the
major version is `0`, a minor bump is where a breaking change may appear.

## 0.3.1 - 2026-08-24

### Fixed

- **`credux upgrade` no longer fails on a release published minutes ago.**
  The version number and the files come from two places - the JSON document
  credux reads, and the file listing pip reads - and PyPI serves the listing
  with a ten-minute cache, so the two disagreed just after a release and pip
  was asked for a version it was convinced did not exist. The install now
  carries `--no-cache-dir`, and when the index itself is still catching up the
  failure says so instead of passing pip's `No matching distribution found`
  through, which read as "that release does not exist" one line under credux
  announcing that it did. A version *you* named and pip cannot find is still
  reported as it was: that one is a typo, not a lag.

## 0.3.0 - 2026-08-24

### Changed

- **The `pu` shell function is now `cdx`.** Breaking: `pu` is gone. The
  function `credux shell-init` prints, and the completer registered for it,
  are both named `cdx` - `cdx acme-billing-User-ada` sets `AWS_PROFILE` in the
  current shell, `cdx` with no argument unsets it, and `cdx mana<TAB>` still
  completes profile names. The new name arrives with the output your shell rc
  already sources, so open a new shell after upgrading; a `pu` left over from
  a previous `eval` lives only in shells started before the upgrade.

## 0.2.1 - 2026-08-24

### Added

- **`pu` completes profile names.** `pu mana<TAB>` now offers every profile
  whose name contains `mana`, the same set `pu mana` would have searched.
  `pu` is a shell function, so the completion script credux already installed
  never saw it; each wrapper now registers a completer that asks credux for
  the candidates. bash, zsh, fish, and PowerShell. The candidates come from
  the same place `credux env <TAB>` takes them, so the two cannot offer
  different sets. Nothing to install: it arrives with the output of
  `credux shell-init` your shell already sources, so open a new shell after
  upgrading.
- **CONTRIBUTING.md**, and this file, both shipped in the source
  distribution.

### Changed

- **The README says where credux sits among aws-vault, granted, and
  aws-sso-util**, and [docs/security.md](docs/security.md) argues the
  credentials-file trade in full: what writing real keys to
  `~/.aws/credentials` buys, what it costs, and when to use something else
  instead.

## 0.2.0 - 2026-08-20

**credux is on PyPI.**

```bash
pipx install credux
```

### Changed

- **Installs from the index.** `pipx install credux` and `pip install credux`
  now work; no clone and no git URL.
- **`credux upgrade` resolves from the index.** It asks PyPI for the newest
  release and installs `credux==X.Y.Z` rather than reading git tags and
  installing from a `git+` URL. git is no longer needed on the machine being
  upgraded. An index that cannot be reached is reported as a failure rather
  than treated as "no upgrade available", so a network problem never looks
  like an up-to-date installation.
- **Releases are built and uploaded by CI**, on a version tag, over PyPI
  trusted publishing. The suite runs on Python 3.10 through 3.13 and the tag
  is checked against the declared version before anything is uploaded.
- **The source distribution is bounded to an explicit allowlist** rather than
  taking whatever the VCS does not ignore.

### Fixed

- The test suite runs under both `pytest` and `python -m pytest`. Seven
  modules import fixtures from `tests.test_store`, which needs the repository
  root importable; the documented `pytest` invocation never supplied it.

### Upgrading from 0.1.x

`credux upgrade` still works: 0.1.x resolves over git tags, so it upgrades
once over git and lands on the index-based code. Or reinstall directly:

```bash
pipx install --force credux
```

## 0.1.1 - 2026-08-20

### Fixed

- `credux upgrade` resolves over anonymous https, so it works for anyone
  rather than only for someone with an ssh key on file.
- The release tooling refuses to publish a tree containing internal paths,
  and refuses to overwrite a public checkout it did not write itself, so a
  change accepted upstream cannot be silently reverted by the next release.

This release predates publication to PyPI and installs from the repository:

```bash
pipx install git+https://github.com/AlbertoSpinella/credux.git@v0.1.1
```

## 0.1.0 - 2026-08-20

First public release of credux: a cross-platform CLI that manages cloud
credentials (IAM users, chained IAM roles, IAM Identity Center) and
materializes the active ones into `~/.aws/credentials` under profile names
derived from a template.

This release predates publication to PyPI and installs from the repository:

```bash
pipx install git+https://github.com/AlbertoSpinella/credux.git@v0.1.0
```
