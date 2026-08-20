"""Optional derived files rendered from the credential list, plus post-sync hooks.

Templates and hooks are given names and accounts only. Secret material is never
part of the rendering context.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from jinja2 import Environment, StrictUndefined, TemplateError

from .config import Config
from .errors import IntegrationError
from .fsutil import atomic_write_text, expand
from .model import Credential

BUILTIN_PREFIX = "builtin:"
BUILTIN_DIR = Path(__file__).parent / "templates"
GENERATED_BY = "credux"

BEGIN_MARKER = ">>> credux managed block >>> do not edit inside"
END_MARKER = "<<< credux managed block <<<"

_MODES = ("region", "replace")

_SAFE_PARAM_KEYS = (
    "account_id",
    "account_name",
    # Both role keys are listed on purpose: `add iam-role` stores the short
    # name under `role`, while `sync` stores an Identity Center permission set
    # under `role_name`. Neither one covers both writers.
    "role",
    "role_name",
    "user",
    "sso_session",
    "role_arn",
)


def _context(credentials: Iterable[Credential]) -> dict[str, Any]:
    """Only non-secret fields reach a template or a hook."""
    return {
        "generated_by": GENERATED_BY,
        "credentials": [
            {
                "profile": cred.profile,
                "kind": cred.kind,
                "region": cred.region,
                **{key: cred.params.get(key) for key in _SAFE_PARAM_KEYS},
            }
            for cred in credentials
        ],
    }


def _template_text(spec: dict[str, Any]) -> str:
    reference = spec.get("template")
    if not reference:
        raise IntegrationError(f"Integration {spec.get('name', '?')!r} has no 'template'")
    if reference.startswith(BUILTIN_PREFIX):
        path = BUILTIN_DIR / reference[len(BUILTIN_PREFIX) :]
    else:
        path = expand(reference)
    if not path.exists():
        raise IntegrationError(
            f"Integration {spec.get('name', '?')!r} references a missing template: {path}"
        )
    return path.read_text(encoding="utf-8")


def render_template(spec: dict[str, Any], credentials: Iterable[Credential]) -> str:
    environment = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
    try:
        template = environment.from_string(_template_text(spec))
        return template.render(**_context(credentials))
    except TemplateError as exc:
        raise IntegrationError(
            f"Integration {spec.get('name', '?')!r} failed to render: {exc}"
        ) from exc


def _replace_region(existing: str, body: str, comment: str) -> str:
    begin = f"{comment} {BEGIN_MARKER}"
    end = f"{comment} {END_MARKER}"
    block = f"{begin}\n{body.rstrip()}\n{end}\n"
    start_at = existing.find(begin)
    end_at = existing.find(end, start_at) if start_at != -1 else -1
    if start_at == -1 or end_at == -1:
        prefix = existing
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        if prefix:
            prefix += "\n"
        return prefix + block
    tail = existing[end_at + len(end) :].lstrip("\n")
    return existing[:start_at] + block + (f"\n{tail}" if tail else "")


def apply(spec: dict[str, Any], credentials: Iterable[Credential]) -> Path:
    """Render one integration to its destination. Returns the path written."""
    destination = spec.get("dest")
    if not destination:
        raise IntegrationError(f"Integration {spec.get('name', '?')!r} has no 'dest'")
    mode = spec.get("mode", "region")
    if mode not in _MODES:
        raise IntegrationError(
            f"Integration {spec.get('name', '?')!r} has an invalid mode {mode!r}; "
            f"expected one of {_MODES}"
        )
    path = expand(destination)
    body = render_template(spec, credentials)

    # Integration destinations are user-supplied paths meant for other programs
    # to read (shell rc fragments, launcher config, ...). They never carry
    # secret material, so world/group-readable 0o644 is correct here, unlike
    # the 0o600 credux uses for the AWS credentials file.
    if mode == "replace":
        atomic_write_text(path, body, mode=0o644)
        return path

    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    comment = spec.get("comment", "#")
    atomic_write_text(path, _replace_region(existing, body, comment), mode=0o644)
    return path


@dataclass
class IntegrationOutcome:
    """What happened when `run_all` applied every integration and ran every hook.

    `lines` is meant to be printed to the user verbatim. `failed` is True if
    any integration or post-sync hook failed, so the caller can still show
    everything that *did* succeed and exit non-zero.
    """

    lines: list[str] = field(default_factory=list)
    failed: bool = False


def run_all(
    config: Config,
    credentials: Iterable[Credential],
    runner: Callable[..., Any] | None = None,
) -> IntegrationOutcome:
    """Apply every enabled integration, then run every post-sync hook.

    One bad integration (a missing template, an invalid mode, an unwritable
    destination, ...) is reported and does not stop the remaining
    integrations or the post-sync hooks from running - the same tolerance
    the hook loop below already has for a hook that exits non-zero.
    """
    if runner is None:
        runner = subprocess.run
    credentials = list(credentials)
    lines: list[str] = []
    failed = False

    for spec in config.integrations:
        if not spec.get("enabled", True):
            continue
        name = spec.get("name", "?")
        try:
            path = apply(spec, credentials)
        except (IntegrationError, OSError) as exc:
            lines.append(f"integration {name!r} failed: {exc}")
            failed = True
            continue
        lines.append(f"wrote {path} ({name})")

    if config.post_sync:
        payload = json.dumps(_context(credentials)["credentials"], indent=2)
        for command in config.post_sync:
            # shell=True is intentional: post_sync commands are the user's own
            # commands from their own config file, equivalent to a shell alias.
            result = runner(
                command,
                shell=True,
                input=payload,
                text=True,
                capture_output=True,
            )
            code = getattr(result, "returncode", 0)
            if code:
                lines.append(f"post_sync {command!r} failed with exit code {code}")
                failed = True
            else:
                lines.append(f"ran {command!r}")

    return IntegrationOutcome(lines=lines, failed=failed)
