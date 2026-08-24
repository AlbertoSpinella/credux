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

**Two sources, and one of them is cached.** The version number and the files
come from different places. `latest_version` reads PyPI's JSON document over
urllib; the installer reads the file listing through pip. PyPI serves that
listing with `cache-control: max-age=600`, and pip keeps its own copy for as
long as it is allowed to, so for the first minutes after a release the two
disagree and credux asks pip for a version pip is convinced does not exist:

```
ERROR: Could not find a version that satisfies the requirement credux==0.3.0
       (from versions: 0.2.0, 0.2.1)
```

Two things answer this. The install command carries `--no-cache-dir`
(`--pip-args=--no-cache-dir` through pipx), so pip's own copy is never
consulted - it costs a re-download of the wheels on every upgrade, about
17 MB of which nearly all is botocore. And when the installer still cannot
find a version that came from the index, `apply` says so in those terms
rather than passing pip's message through, because raw it reads as "that
release does not exist" one line under credux announcing that it does. Only
that combination is explained: a version the *user* named and pip cannot find
is a typo, and every other installer failure speaks for itself.

**Choosing the installer.** It drives whichever installer owns the environment
credux is running in: `pipx install --force` for a pipx installation, `pip
install --upgrade` inside a plain virtual environment. Two cases are refused
rather than guessed at, both with exit code 8:

- an editable install (`pip install -e`), which tracks a working tree and should
  be updated with `git pull`;
- an installation outside any virtual environment, where pip would write into
  the system or user site directory.

**Why the command prints nothing afterwards.** The installer's stderr is teed,
not captured: `apply` has to read it to tell one failure from another, but
pipx writes its whole running commentary there, and holding that back until
the process exits would leave a minute-long install looking hung. Every line
is written through as it arrives and kept. After that, credux is replacing the
environment it is running from, so it exits as soon as the installer returns.
Anything computed after `apply` returns risks importing a module that is already
gone. On Windows the running `credux.exe` may be locked, in which case the
installer's own error is reported and nothing has changed.

Planning (`plan`) is separated from mutation (`apply`) in `upgrade.py` the same
way `sync.py` separates them, so the whole decision is inspectable before
anything is written.
