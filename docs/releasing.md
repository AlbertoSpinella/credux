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
5. Push both: `git push && git push --tags`.
6. Reinstall (`pip install -e .`) if you want the local `credux --version` to
   agree with the tag.

Nothing else in the repo records the version: `cli.py` reads it from the
installed distribution's metadata via
`@click.version_option(package_name="credux")`. That means `credux --version`
reports the version of the *installed* distribution, not the one in the working
tree, so an editable checkout keeps reporting the old number until it is
reinstalled.

## How `credux upgrade` works

Credux is not on PyPI, so both the tag lookup and the install go over https to
the repository itself rather than to an index. No credentials are involved: the
repository is public and `git ls-remote` reads its tags anonymously. Git is
never allowed to prompt for them either (`GIT_TERMINAL_PROMPT=0`), so a machine
that cannot reach the repository fails immediately instead of appearing to hang
on a username prompt.

This is why step 4 above is not optional: a release nobody can see is a release
`upgrade` cannot resolve. Until the tag is pushed, `credux upgrade` keeps
reporting the previous version as the newest one.

**Resolving the version.** With no `--version`, `upgrade` asks the repository for
its newest `vX.Y.Z` tag (`git ls-remote`) and stops without installing anything
if that is already the version being run. `--version` always installs, even over
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
