# Adding a provider

A provider is the only place in credux that knows a specific cloud exists. See
the layering rule in [architecture.md](architecture.md) for what that buys and
what it forbids.

## Steps

1. Create `src/credux/providers/<name>/` with an `__init__.py` that subclasses
   `Provider`, sets `name` and `kinds`, and implements `resolve()`.
   `console_url()` is optional: the base class raises `ResolutionError` if it is
   not overridden and something calls it.

2. Register the instance with `providers.base.register(...)`. See how
   `providers/aws/__init__.py` does it at import time, and how `app.py` imports
   the package (`from .providers import aws as aws_provider  # noqa: F401`)
   purely so that import-time registration runs.

3. Add naming-template support: a new entry in `naming.TEMPLATE_KEYS` mapping
   the new kind(s) to a `naming.<key>` config key, and a default in
   `config.DEFAULT_NAMING`.

4. Route every API call through the injected `client_factory`, never a
   directly-constructed SDK client, or the provider cannot be tested with a
   stub.

5. Write tests using the same shape as `tests/providers/aws/`: a
   `Stubber`-backed fixture for every remote call, no network, no real
   filesystem outside `tmp_path`/`CREDUX_HOME`, and an in-memory keyring for
   anything that touches secrets. See [testing.md](testing.md).

## The provider interface

```python
class Provider(ABC):
    name: str            # "aws"
    kinds: tuple[str, ...]   # ("iam-user", "iam-role", "sso")

    def resolve(self, cred: Credential, ctx: ResolutionContext) -> ResolvedCredentials: ...
    def console_url(self, cred: Credential, ctx: ResolutionContext) -> str: ...  # optional
```

`resolve()` receives the credential and the context. Read the credential's
opaque `params` dict (only your provider interprets it), call
`ctx.client_factory(service, region=..., credentials=...)` for anything remote,
and return `ResolvedCredentials`.

If your kind chains onto another credux credential, call
`ctx.resolve_id(params["source"])` to get the parent's material. That recursion
is cycle-guarded by `ResolutionContext`; do not implement your own chain
walking.

Anything long-term and secret that your provider needs goes through `store.py`,
never into `config.json` (invariant 3 in [architecture.md](architecture.md)).

## Reference implementation

`providers/aws/` is the worked example:

- `__init__.py` - dispatch by `cred.kind`, plus `console_url` via
  `federation.py`
- `iam_user.py` - a leaf resolver reading long-term keys from the keychain,
  optionally exchanging them for an MFA session token
- `iam_role.py` - the chaining resolver: `ctx.resolve_id(source)` then
  `sts:AssumeRole`
- `sso.py` - a leaf resolver using a cached Identity Center token
- `sso_login.py` - the OIDC device-authorization flow and its token store
- `federation.py` - credentials to console sign-in URL
- `org.py` - best-effort management-account detection; a denied or failed call
  means "unknown", never a crash
