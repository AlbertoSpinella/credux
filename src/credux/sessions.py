"""Starting, stopping and refreshing the sessions written to the AWS files."""

from __future__ import annotations

import dataclasses
from datetime import datetime
from pathlib import Path

from . import awsfiles
from .awsfiles import ProfileEntry
from .config import Config
from .errors import CreduxError
from .fsutil import expand
from .model import Credential, Registry
from .providers.base import ResolutionContext
from .state import ActiveSession, State


@dataclasses.dataclass
class StartOutcome:
    """What a batch start managed to do, successes and failures alike."""

    started: list[ActiveSession]
    failures: list[tuple[Credential, CreduxError]]


class SessionManager:
    """The only writer of ~/.aws/credentials and the state file."""

    def __init__(
        self,
        config: Config,
        registry: Registry,
        state: State,
        ctx: ResolutionContext,
    ) -> None:
        self.config = config
        self.registry = registry
        self.state = state
        self.ctx = ctx
        # What the last write found worth telling the user: the one-time
        # backup and whether a handwritten `default` profile is now shadowed.
        # Held rather than printed because this module has no output channel;
        # `cli._report_file_notices` is what turns them into text. They
        # describe the *last* write only, so a command that writes twice
        # reports the second write, by which point the backup is already
        # taken and the flag has been raised once.
        self.notices = awsfiles.WriteReport()
        self.config_notices = awsfiles.WriteReport()

    @property
    def credentials_path(self) -> Path:
        return expand(self.config.setting("aws_credentials_file", "~/.aws/credentials"))

    @property
    def config_path(self) -> Path:
        return expand(self.config.setting("aws_config_file", "~/.aws/config"))

    def _write(self, entries: list[ProfileEntry]) -> None:
        # Write order matters, and there is no transaction across three
        # separate files, so it is chosen to fail safe: credentials first,
        # then config, then state last. Credentials is the file AWS SDKs
        # actually read, so writing it first means a crash right after
        # leaves *real, usable* material on disk - `stop` can still remove
        # it later because `stop` reads the credentials file itself, not
        # just state. Writing state first would risk the opposite: a crash
        # between state.save() and write_credentials would leave state
        # claiming a session is active when the file never received it,
        # which is a worse lie for anything that trusts state without also
        # checking the file. Config is written before state for the same
        # reason - it is still AWS-facing material, whereas state is purely
        # credux's own bookkeeping and is safe to lag behind by one crash.
        self.notices = awsfiles.write_credentials(
            self.credentials_path, entries, self.config.setting("default_profile")
        )
        regions = [
            ProfileEntry(session.profile, "", "", region=session.region)
            for session in self.state.all()
            if session.region
        ]
        self.config_notices = awsfiles.write_config(self.config_path, regions)
        self.state.save()

    def _materialized(self) -> list[ProfileEntry]:
        return awsfiles.read_managed_entries(self.credentials_path)

    def _upsert(self, entries: list[ProfileEntry], entry: ProfileEntry) -> list[ProfileEntry]:
        kept = [item for item in entries if item.profile != entry.profile]
        kept.append(entry)
        return kept

    def _prepare(self, cred: Credential) -> tuple[ProfileEntry, ActiveSession]:
        """Resolve one credential into the pair of records a start writes."""
        resolved = self.ctx.resolve(cred)
        entry = ProfileEntry(
            profile=cred.profile,
            access_key_id=resolved.access_key_id,
            secret_access_key=resolved.secret_access_key,
            session_token=resolved.session_token,
            region=cred.region,
        )
        session = ActiveSession(
            profile=cred.profile,
            credential_id=cred.id,
            kind=cred.kind,
            expires_at=resolved.expiration.isoformat() if resolved.expiration else None,
            region=cred.region,
        )
        return entry, session

    def start(self, cred: Credential) -> ActiveSession:
        """Resolve a credential and write it into the credentials file."""
        entry, session = self._prepare(cred)
        self.state.set(session)
        self._write(self._upsert(self._materialized(), entry))
        return session

    def start_all(self, creds: list[Credential]) -> StartOutcome:
        """Start a whole batch, reporting per-credential failures rather than
        aborting on the first one.

        Two things this does that a loop over `start` cannot. It resolves
        every credential before writing anything and then writes once, so
        starting N credentials rewrites the AWS files once instead of N
        times, each rewrite re-reading and re-emitting the whole managed
        region. And a credential that cannot be resolved - an expired
        Identity Center token, a denied AssumeRole - costs only itself: the
        rest are still materialized, and the caller decides what to do with
        the failures. Only `CreduxError` is caught, so a genuine bug still
        surfaces as a crash rather than being reported as one credential's
        bad luck.

        Nothing is written when every credential failed, which also covers
        the empty batch: an unconditional write would re-assert the fake
        `default` profile (and truncate the managed region) for a command
        that materialized nothing.
        """
        started: list[ActiveSession] = []
        failures: list[tuple[Credential, CreduxError]] = []
        entries = self._materialized()

        for cred in creds:
            try:
                entry, session = self._prepare(cred)
            except CreduxError as exc:
                failures.append((cred, exc))
                continue
            entries = self._upsert(entries, entry)
            self.state.set(session)
            started.append(session)

        if started:
            self._write(entries)
        return StartOutcome(started=started, failures=failures)

    def stop(self, profile: str) -> bool:
        """Remove a profile from the credentials file. False if it was not there."""
        entries = self._materialized()
        remaining = [item for item in entries if item.profile != profile]
        was_active = len(remaining) != len(entries) or self.state.get(profile) is not None
        self.state.clear(profile)
        self._write(remaining)
        return was_active

    def rename(self, old_profile: str, new_profile: str) -> bool:
        """Move an active session from one profile name to another.

        A credential's `id` never changes but its `profile` is derived, and
        `credux sync` renames one whenever a naming template changes. Both the
        credentials file and `state.json` key on the profile *name*, so
        without this the registry moves on while the old name keeps a live,
        usable entry: the material is still real, but `credux list` and `credux
        stop <name>` both go through the registry and can no longer see it,
        leaving `credux stop --all` as the only way to clear it.

        Returns False, having written nothing, if the old profile was neither
        materialized nor in state - there is nothing to move, and rewriting
        the AWS files for a no-op rename would be a pointless side effect.

        Renaming more than one profile at once must go through `rename_all`,
        not a loop over this method; see that docstring for why.
        """
        return self.rename_all([(old_profile, new_profile)]) == 1

    def rename_all(self, pairs: list[tuple[str, str]]) -> int:
        """Apply a whole set of renames as one atomic move. Returns the count.

        Calling `rename` in a loop is wrong whenever the set contains a chain
        (A -> B, B -> C) or a swap (A -> B, B -> A), and `credux sync` produces
        both routinely: an Identity Center account renamed from `dev` to
        `test` while another is renamed from `test` to `staging` yields
        exactly that chain, and every target name in it is released within the
        same sync, so `plan_sso_sync`'s collision guards - which only reject
        names held by credentials *outside* the plan - correctly stay quiet.
        Applied one at a time, the first rename lands its material on a name
        the second is about to move, and the second carries it away: one
        credential's live material is destroyed, another's ends up served
        under a profile that every listing attributes to a third, and
        `state.json` disagrees with the registry. It is order-dependent, so
        the reverse discovery order happens to survive.

        `Registry.replace` refuses a name held by another id, so `apply_plan`
        needs a placeholder two-phase dance to work around it. Nothing here
        refuses anything, so no placeholder is needed: the whole result is
        computed against the entry list and state read at entry, releasing
        every old name before materializing any new one, and reaches disk in a
        single `_write`. Nothing is re-resolved, so a sync cannot make this
        path call AWS or prompt for MFA.

        Pairs whose old profile is neither materialized nor in state are
        skipped; if that leaves nothing to move, nothing is written at all.

        Two things this deliberately does not police, because
        `plan_sso_sync` already rejects both before a plan exists: two pairs
        renaming to the same target, and a target held by a profile that is
        not itself being renamed. Either one silently loses an entry here.
        """
        entries = self._materialized()
        by_profile = {item.profile: item for item in entries}

        moves: list[tuple[str, str, ActiveSession | None, ProfileEntry | None]] = []
        for old_profile, new_profile in pairs:
            session = self.state.get(old_profile)
            entry = by_profile.get(old_profile)
            if session is None and entry is None:
                continue
            moves.append((old_profile, new_profile, session, entry))

        if not moves:
            return 0

        # Phase 1: release every old name, in both the entry list and state,
        # before phase 2 puts anything back. This is what makes a chain or a
        # swap safe: no target name can still be occupied by a member of this
        # same set when it is written.
        released = {old_profile for old_profile, _new, _session, _entry in moves}
        remaining = [item for item in entries if item.profile not in released]
        for old_profile in released:
            self.state.clear(old_profile)

        # Phase 2: materialize each move under its new name, carrying that
        # credential's own material and expiry across untouched.
        for _old, new_profile, session, entry in moves:
            if session is not None:
                self.state.set(dataclasses.replace(session, profile=new_profile))
            if entry is not None:
                remaining = self._upsert(
                    remaining, dataclasses.replace(entry, profile=new_profile)
                )

        self._write(remaining)
        return len(moves)

    def stop_all(self) -> int:
        count = len(self._materialized())
        self.state.clear_all()
        self._write([])
        return count

    def refresh(
        self,
        threshold_minutes: int | None = None,
        profiles: list[str] | None = None,
        now: datetime | None = None,
    ) -> list[ActiveSession]:
        """Re-resolve active sessions that are close to expiry."""
        if threshold_minutes is None:
            threshold_minutes = int(self.config.setting("refresh_threshold_minutes", 10))
        self.ctx.clear_cache()

        renewed: list[ActiveSession] = []
        for session in self.state.all():
            if profiles is not None and session.profile not in profiles:
                continue
            if profiles is None and not session.is_expiring(threshold_minutes, now):
                continue
            cred = self.registry.by_id(session.credential_id)
            renewed.append(self.start(cred))
        return renewed
