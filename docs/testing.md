# Testing

Credux follows strict test-driven development: write the failing test first,
watch it fail for the expected reason, then make it pass.

## Running the suite

```bash
pip install -e ".[dev]"
pytest
```

`pytest` is the only test dependency. If your environment has `pytest` but not
credux installed into it, run the suite against the source tree directly:

```bash
PYTHONPATH=src pytest
```

## Conventions

- **Every test that could touch AWS uses `botocore.stub.Stubber`** via the
  `stubbed_client` fixture (`tests/providers/aws/conftest.py`), never a real
  `boto3` call. `stubbed_client.factory` is what gets passed as
  `client_factory`.
- **`stubbed_client` builds one stubbed client per *service name*, not per
  call.** If a test resolves a chain that calls `sts` twice (e.g. two chained
  `iam-role` credentials), both calls share the same `Stubber` queue. Order your
  `add_response` calls to match resolve order, and do not assume a second call
  to the same service gets an independent client.
- **Every test isolates the filesystem** with the `credux_home` fixture
  (`tests/conftest.py`, which sets `CREDUX_HOME` to a `tmp_path` subdirectory)
  and `tmp_path` for anything else on disk. No test may touch a real `~/.aws`.
- **Every test that touches `store.py` uses the in-memory `memory_keyring`
  fixture** (defined in `tests/test_store.py`, imported into other test modules
  with `from tests.test_store import memory_keyring  # noqa: F401`), never the
  real OS keychain.
- **No test launches a real browser or reaches the network.**
  `sso_login.login` and `browser.open_container` both accept an injectable
  opener/sleeper/runner for exactly this reason.
- **Prefer asserting on exit codes** (`CreduxError.exit_code`) and on substrings
  of the error message over asserting on exception types alone, matching the
  existing CLI tests.

## Traps this project actually hit

### A parameter default is bound once, at import time

And a second bare import binds a second, independent copy of the name.

Several modules (`app.py`, `providers/base.py`, `browser.py`, `sso_login.py`,
`doctor.py`, `integrations.py`) need a caller to be able to monkeypatch a module
attribute (`subprocess.run`, `subprocess.Popen`, `webbrowser.open`,
`time.sleep`, `shutil.which`, `provider_base.default_client_factory`, ...)
*after* the module defining the function has already been imported, and have
that patch take effect on the next call.

A `def f(x=some.module.attr)` signature evaluates `some.module.attr` once, at
import time, and stays bound to that value forever: a later
`monkeypatch.setattr(some.module, "attr", fake)` has no effect on it. Worse,
`from some.module import attr` at the top of *this* module creates a second name
bound to the same original value, so even patching `some.module.attr` directly
would not reach the copy.

The pattern used throughout this codebase is: default the parameter to `None`,
then resolve it inside the function body by importing the module (not the name)
and reading the attribute fresh.

```python
import some.module as m

def f(x=None):
    if x is None:
        x = m.attr
```

Do this for any new injectable dependency. Do not "simplify" an existing one
back into a parameter default.

### Marker-region search is duplicated, not shared

`awsfiles.py` and `integrations.py` both implement the same idea: find a begin
marker, then search for the end marker *starting from the begin marker's
position*, never from index 0, so a stray line that merely looks like an end
marker somewhere earlier in a human-edited file cannot be mistaken for the close
of the managed region.

They implement it as two independent pieces of code
(`awsfiles._locate_managed_region` / `replace_managed_region` versus
`integrations._replace_region`). A bug fixed in one is not automatically fixed
in the other. See
`test_region_mode_converges_despite_stray_end_marker_before_begin_marker` in
`tests/test_integrations.py`, which explicitly calls itself "the parallel
mechanism" to the equivalent `awsfiles` fix.

If you touch the marker-search logic in either file, check whether the same
class of bug exists in the other one too, and add a test there as well. Do not
assume the two have been, or will stay, unified.

### A fixture can pass for a reason unrelated to what the test claims

The original nested-role-chain test
(`test_role_chained_on_a_role_resolves_recursively` in
`tests/providers/aws/test_iam_role.py`) used identical `AssumeRole` response
payloads and undistinguished `expected_params` at both levels of the chain, on a
`Stubber` fixture that hands back one client per *service name* rather than per
call. The test could therefore pass even if the outer call's request or the inner
call's response were silently swapped, because nothing about the assertions
could tell the two levels apart.

Fixed by giving each level of the chain distinguishable request parameters and a
distinguishable `AccessKeyId`, and asserting which level's result actually
reaches the caller.

When you add a test that claims to verify ordering, chaining, or "the right one
of two similar things happened", make sure the fixture data actually could
distinguish the right outcome from the wrong one, not just from "nothing
happened".
