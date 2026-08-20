"""Profile-name templates. Pure: no IO, no AWS, no config loading."""

from __future__ import annotations

import string
from typing import Any, Mapping
from urllib.parse import urlparse

from .errors import ConfigError

TEMPLATE_KEYS: dict[str, str] = {
    "iam-user": "iam_user",
    "iam-role": "iam_role",
    "sso": "iam_sso",
}

KNOWN_VARIABLES = frozenset(
    {"org", "account_name", "account_id", "user", "role", "subdomain", "region"}
)

MANAGEMENT_PREFIX_KEY = "management_account_prefix"


def required_fields(template: str) -> set[str]:
    """Every {name} referenced by the template.

    Rejects anything that could silently render a malformed or half-filled
    name: unmatched braces, the empty auto-numbering field '{}', and any
    conversion (!r/!s/!a) or format spec (:>10), since those pass through
    str.format unchanged and corrupt the profile name rather than fail.
    """
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as exc:
        raise ConfigError(f"Naming template {template!r} is malformed: {exc}") from exc

    fields: set[str] = set()
    for _, field, format_spec, conversion in parsed:
        if field is None:
            continue
        if field == "":
            raise ConfigError(
                f"Naming template {template!r} uses an empty replacement field '{{}}'; "
                "every field must be named explicitly"
            )
        if conversion is not None:
            raise ConfigError(
                f"Naming template {template!r} uses a conversion specifier on field "
                f"{field!r} ('!{conversion}'); conversion specifiers are not allowed"
            )
        if format_spec:
            raise ConfigError(
                f"Naming template {template!r} uses a format spec on field {field!r} "
                f"(':{format_spec}'); format specs are not allowed"
            )
        fields.add(field)
    return fields


def validate_template(template: str) -> set[str]:
    """Reject a template before it is stored. Returns the fields it uses.

    Checks everything about a template that does not depend on a particular
    credential's values, so a bad one can be refused at the moment someone
    sets it rather than at the moment it would have produced a name.

    A template with no variables at all is rejected here but not by
    `required_fields`: it renders perfectly, to the same string for every
    credential, and the second credential added under it collides with the
    first.
    """
    fields = required_fields(template)
    unknown = sorted(fields - KNOWN_VARIABLES)
    if unknown:
        raise ConfigError(
            f"Naming template {template!r} uses unknown variable(s): {', '.join(unknown)}. "
            f"Known variables: {', '.join(sorted(KNOWN_VARIABLES))}"
        )
    if not fields:
        raise ConfigError(
            f"Naming template {template!r} uses no variables, so every credential would "
            f"render to the same profile name. Use at least one of: "
            f"{', '.join(sorted(KNOWN_VARIABLES))}"
        )
    return fields


def render(template: str, variables: Mapping[str, Any]) -> str:
    """Render a template, refusing to produce a partially-filled name."""
    fields = validate_template(template)
    missing = sorted(
        field
        for field in fields
        if variables.get(field) is None or str(variables.get(field)).strip() == ""
    )
    if missing:
        raise ConfigError(
            f"Naming template {template!r} needs a value for: {', '.join(missing)}"
        )
    try:
        return template.format(**{field: str(variables[field]) for field in fields})
    except (KeyError, IndexError, ValueError) as exc:
        raise ConfigError(
            f"Naming template {template!r} could not be rendered: {exc}"
        ) from exc


def profile_name(
    kind: str,
    naming: Mapping[str, str],
    variables: Mapping[str, Any],
    management: bool = False,
) -> str:
    """Render the profile name for a credential kind."""
    key = TEMPLATE_KEYS.get(kind)
    if key is None:
        raise ConfigError(
            f"Unknown credential kind {kind!r}; expected one of {', '.join(sorted(TEMPLATE_KEYS))}"
        )
    template = naming.get(key)
    if not template:
        raise ConfigError(f"No naming template configured for {key!r}")
    values = dict(variables)
    if management:
        prefix = naming.get(MANAGEMENT_PREFIX_KEY, "")
        account_name = values.get("account_name") or ""
        # startswith() is a deliberately loose "already prefixed" check: it can
        # misfire on a coincidental name like "management_reporting" with prefix
        # "management_", but that is an acceptable trade-off for the default
        # "management_" prefix and it keeps re-running profile_name() on an
        # already-prefixed name idempotent.
        if account_name and not str(account_name).startswith(prefix):
            values["account_name"] = f"{prefix}{account_name}"
    return render(template, values)


def subdomain_from_start_url(start_url: str) -> str:
    """The leftmost label of an Identity Center start URL host."""
    host = urlparse(start_url).hostname
    if not host or "." not in host:
        raise ConfigError(
            f"{start_url!r} is not a usable Identity Center start URL; "
            "expected something like https://<subdomain>.awsapps.com/start"
        )
    return host.split(".")[0]
