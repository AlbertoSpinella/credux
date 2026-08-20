# Releasing and upgrade mechanics

This is the authoritative description of the release procedure.

## Cutting a release

Versions are bumped by hand and marked with an annotated git tag. There is no
automation deriving one from the other, so both steps happen together or the tag
and the metadata disagree.

1. Bump `version` in `pyproject.toml` following semantic versioning. While the
   major version is `0`, a breaking change bumps the minor and everything else
   bumps the patch.
2. Run the test suite. Do not tag a failing tree.
3. Commit that on its own: `chore: release v<x.y.z>`.
4. Tag the commit: `git tag -a v<x.y.z> -m "v<x.y.z>"`. The tag name is the
   version with a leading `v`; the `pyproject.toml` value never carries one.
5. Push both: `git push && git push --tags`. On the published repository the
   tag push is what starts the release workflow: it runs the suite on every
   supported Python, refuses a tag that disagrees with `pyproject.toml`, and
   uploads to the index over OIDC.
6. Reinstall (`pip install -e .`) if you want the local `credux --version` to
   agree with the tag.

Nothing else in the repo records the version: `cli.py` reads it from the
installed distribution's metadata via
`@click.version_option(package_name="credux")`. That means `credux --version`
reports the version of the *installed* distribution, not the one in the working
tree, so an editable checkout keeps reporting the old number until it is
reinstalled.

## How `credux upgrade` works

Credux is published to PyPI, so both the version lookup and the install go to
the index. The lookup is a single request to the project's JSON document; no
credentials are involved, and neither git nor a clone is needed on the machine
being upgraded. An index that cannot be reached is reported as a failure rather
than treated as "no upgrade available", so a network problem never looks like
an up-to-date installation.

This is why the tag push is not optional: it is what triggers publication.
Until the workflow has run and the version is on the index, `credux upgrade`
keeps reporting the previous version as the newest one.

**Resolving the version.** With no `--version`, `upgrade` asks the index for the
project's newest release and stops without installing anything if that is
already the version being run. `--version` always installs, even over
the same version, so it doubles as a repair. `--from` takes a path and installs
whatever is checked out there, so it cannot be combined with `--version`.

**Choosing the installer.** It drives whichever installer owns the environment
credux is running in: `pipx install --force` for a pipx installation, `pip
install --upgrade` inside a plain virtual environment. Two cases are refused
rather than guessed at, both with exit code 8:

- an editable install (`pip install -e`), which tracks a working tree and should
  be updated with `git pull`;
- an installation outside any virtual environment, where pip would write into
  the system or user site directory.

**Why the command prints nothing afterwards.** Credux is replacing the
environment it is running from, so it exits as soon as the installer returns.
Anything computed after `apply` returns risks importing a module that is already
gone. On Windows the running `credux.exe` may be locked, in which case the
installer's own error is reported and nothing has changed.

Planning (`plan`) is separated from mutation (`apply`) in `upgrade.py` the same
way `sync.py` separates them, so the whole decision is inspectable before
anything is written.
