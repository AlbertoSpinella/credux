"""Reconciling configured credentials with what Identity Center reports.

A credential's identity is (sso_session, account_id, role_name). Profile names
are derived, so changing a naming template renames rather than duplicates.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Iterable

from .config import Config
from .errors import ConfigError
from .model import Credential, Registry, new_id
from .naming import profile_name, subdomain_from_start_url
from .providers.aws.sso import DiscoveredRole


@dataclass
class SyncPlan:
    created: list[Credential] = field(default_factory=list)
    renamed: list[tuple[Credential, str]] = field(default_factory=list)
    removed: list[Credential] = field(default_factory=list)
    unchanged: list[Credential] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.created or self.renamed or self.removed)


def _identity(session_name: str, account_id: str, role_name: str) -> tuple[str, str, str]:
    return (session_name, str(account_id), str(role_name))


def _placeholder_name(cred_id: str) -> str:
    return f"__credux-sync-tmp-{cred_id}__"


def plan_sso_sync(
    config: Config,
    registry: Registry,
    session_name: str,
    discovered: Iterable[DiscoveredRole],
    management_account_id: str | None = None,
) -> SyncPlan:
    """Diff discovered roles against configured credentials for one session.

    Never touches the registry - callers apply the result with `apply_plan`.

    `management_account_id` decides which discovered account gets the
    management-account naming prefix. Passed explicitly it wins over
    `sso_sessions.<name>.management_account_id`, because `credux sync` works
    that value out at run time (it asks, then verifies the answer against
    Organizations) and the verified id need not appear in config.json at all.
    Passing None keeps the configured value, for every caller that has no
    answer of its own.

    Refuses (raises `ConfigError`) rather than build a plan containing any
    profile-name collision: two discovered roles rendering to the same name,
    a rename landing on a name held by a credential from a different session
    or of a different kind, or a rename landing on a name still held by a
    credential that vanished from discovery but has not been pruned yet. A
    naming template that cannot tell two credentials apart is a
    configuration problem, not something to silently paper over by dropping
    one of them.
    """
    session_cfg = config.sso_sessions[session_name]
    subdomain = subdomain_from_start_url(session_cfg["start_url"])
    region = session_cfg.get("sso_region")
    if management_account_id is None:
        management_account_id = session_cfg.get("management_account_id")

    all_credentials = registry.all()
    known = {
        _identity(
            session_name,
            cred.params.get("account_id", ""),
            cred.params.get("role_name", ""),
        ): cred
        for cred in all_credentials
        if cred.kind == "sso" and cred.params.get("sso_session") == session_name
    }
    known_ids = {cred.id for cred in known.values()}
    # Profiles held by credentials this plan does not own at all: a different
    # session or a different kind. These can never become available to this
    # plan, regardless of --prune.
    foreign_profiles = {
        cred.profile: cred for cred in all_credentials if cred.id not in known_ids
    }

    # Pass 1: dedupe discovered roles by identity and compute each one's
    # wanted profile name, without mutating anything yet.
    entries: list[tuple[tuple[str, str, str], DiscoveredRole, str, dict[str, str]]] = []
    seen: set[tuple[str, str, str]] = set()
    for role in discovered:
        key = _identity(session_name, role.account_id, role.role_name)
        if key in seen:
            # Identity Center should never report the same (account, role) pair
            # twice, but if it does, treat repeats as the same role rather than
            # planning duplicate creates for it.
            continue
        seen.add(key)

        variables = {
            "org": subdomain,
            "subdomain": subdomain,
            "account_name": role.account_name,
            "account_id": role.account_id,
            "role": role.role_name,
            "region": region,
        }
        wanted = profile_name(
            "sso",
            config.naming,
            variables,
            management=str(role.account_id) == str(management_account_id or ""),
        )
        # `org` and `subdomain` are persisted, not just used to render the
        # name here and discarded. They are the only source those naming
        # variables have for an Identity Center credential, and anything that
        # re-renders the name later from `params` alone - `doctor._naming`,
        # for one - reports every sso credential as broken without them.
        params = {
            "sso_session": session_name,
            "account_id": role.account_id,
            "account_name": role.account_name,
            "role_name": role.role_name,
            "org": subdomain,
            "subdomain": subdomain,
        }
        entries.append((key, role, wanted, params))

    removed = [cred for key, cred in known.items() if key not in seen]
    # A vanished credential's name is only released if the caller later
    # applies this plan with --prune; a plan must stay valid on the safer
    # prune=False path too, so treat that name as still occupied.
    stale_profiles = {cred.profile: cred for cred in removed}

    # Pass 2: verify every wanted name is unique across the whole plan before
    # committing to any classification. Nothing has been mutated up to here.
    target_owners: dict[str, tuple[tuple[str, str, str], DiscoveredRole]] = {}
    for key, role, wanted, _params in entries:
        foreign = foreign_profiles.get(wanted)
        if foreign is not None:
            raise ConfigError(
                f"Cannot use profile name {wanted!r} for account {role.account_id!r} "
                f"role {role.role_name!r}: it is already used by credential "
                f"{foreign.profile!r} outside session {session_name!r}. "
                "Rename or remove that credential first."
            )

        stale = stale_profiles.get(wanted)
        if stale is not None:
            raise ConfigError(
                f"Cannot use profile name {wanted!r} for account {role.account_id!r} "
                f"role {role.role_name!r}: credential {stale.profile!r} still holds "
                "that name after vanishing from Identity Center discovery. "
                "Re-run with --prune to release the name, or rename it manually."
            )

        owner = target_owners.get(wanted)
        if owner is not None and owner[0] != key:
            _other_key, other_role = owner
            raise ConfigError(
                f"Naming template produces {wanted!r} for two different roles: "
                f"account {other_role.account_id!r} role {other_role.role_name!r} and "
                f"account {role.account_id!r} role {role.role_name!r}. The naming "
                "template does not disambiguate these credentials; add a "
                "distinguishing variable such as {account_id} to the template."
            )
        target_owners[wanted] = (key, role)

    # Pass 3: no collision is possible now - classify and mutate.
    plan = SyncPlan()
    for key, _role, wanted, params in entries:
        current = known.get(key)
        if current is None:
            plan.created.append(
                Credential(
                    id=new_id(),
                    kind="sso",
                    profile=wanted,
                    provider="aws",
                    region=region,
                    params=params,
                )
            )
            continue

        current.params.update(params)
        current.region = region
        if current.profile != wanted:
            previous = current.profile
            current.profile = wanted
            plan.renamed.append((current, previous))
        else:
            plan.unchanged.append(current)

    plan.removed.extend(removed)
    return plan


def apply_plan(registry: Registry, plan: SyncPlan, prune: bool = False) -> None:
    """Write a plan into the registry. Removal happens only when asked for.

    `plan_sso_sync` already refuses to build a plan containing any profile-name
    collision, so every target name arriving here is unique. Renames still
    land in two passes - first every renamed credential moves to a unique
    placeholder profile, then all of them move to their final name - because
    a straight swap (A -> B and B -> A at once) cannot be applied in a single
    pass: `Registry.replace` rejects a name still held by a different id.

    Guarantee: if any `replace` call made while renaming raises (for example
    because the registry changed underneath this call since the plan was
    built), every credential this call renamed is restored to the profile
    name it had on entry before the exception propagates. No credential is
    left on a placeholder name, whether the failure happened while moving
    credentials to their placeholders or while moving them to their final
    names. This function does not make the whole apply atomic - creates,
    unchanged writes and prune-removal are not covered by that guarantee -
    only the rename step, which is the only part that needs a multi-step
    dance in the first place.

    Creating is unconditional except for one narrow, deliberate case: replaying
    the exact same plan a second time (for example to apply with `prune=True`
    after an earlier `prune=False` dry-application) must not fail just because
    the previous call already created these credentials. So a create is
    skipped only when a credential with that exact id is already sitting on
    that exact profile - a true replay. Any other credential occupying the
    wanted name is a genuine, unresolved collision and raises `UsageError`
    from the registry; `plan_sso_sync` already guarantees this cannot happen
    from the plan's own contents, so it only fires if the registry drifted
    after the plan was built.
    """
    moved: list[tuple[Credential, str]] = []
    try:
        for cred, previous in plan.renamed:
            registry.replace(dataclasses.replace(cred, profile=_placeholder_name(cred.id)))
            moved.append((cred, previous))
        for cred, _previous in plan.renamed:
            registry.replace(cred)
    except Exception:
        for cred, previous in moved:
            try:
                registry.replace(dataclasses.replace(cred, profile=previous))
            except Exception:
                pass
        raise

    for cred in plan.created:
        occupant = registry.by_profile(cred.profile)
        if occupant is not None and occupant.id == cred.id:
            continue
        registry.add(cred)

    for cred in plan.unchanged:
        registry.replace(cred)

    if prune:
        for cred in plan.removed:
            registry.remove(cred.id)
