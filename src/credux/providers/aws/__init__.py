"""The AWS provider: dispatch by credential kind."""

from __future__ import annotations

from ...errors import ResolutionError
from ...model import Credential
from ..base import Provider, ResolutionContext, ResolvedCredentials, register
from . import federation, iam_role, iam_user, sso

_RESOLVERS = {
    "iam-user": iam_user.resolve,
    "iam-role": iam_role.resolve,
    "sso": sso.resolve,
}


class AwsProvider(Provider):
    name = "aws"
    kinds = tuple(_RESOLVERS)

    def _resolver(self, kind: str):
        resolver = _RESOLVERS.get(kind)
        if resolver is None:
            raise ResolutionError(
                f"The aws provider cannot handle kind {kind!r}; "
                f"supported kinds: {', '.join(self.kinds)}"
            )
        return resolver

    def resolve(self, cred: Credential, ctx: ResolutionContext) -> ResolvedCredentials:
        return self._resolver(cred.kind)(cred, ctx)

    def console_url(
        self, cred: Credential, ctx: ResolutionContext, resolved: ResolvedCredentials
    ) -> str:
        """A self-contained federation sign-in URL, for every credential kind.

        `sso` is deliberately not special-cased into an Identity Center portal
        deep link. Such a link carries no credential of its own: it only
        completes if the portal's session cookie is already in the browser
        container it opens in, and `credux console` opens each profile in its
        own container precisely so that cookie is not shared - so that path
        cannot finish without something else (a browser extension) hijacking
        the sign-in and choosing a container of its own. Identity Center role
        credentials already carry a session token, so they go through the same
        endpoint as an assumed role does and land, signed in, in whatever
        container they were opened in.
        """
        temporary, minted = federation.ensure_temporary(
            cred.profile, resolved, ctx, cred.region
        )
        destination = ctx.config.setting(
            "console_destination", "https://console.aws.amazon.com/"
        )
        # SessionDuration is only valid for a sign-in token derived from
        # GetFederationToken credentials, which is exactly the case
        # ensure_temporary reports with `minted`.
        return federation.signin_url(
            temporary,
            destination=destination,
            session_duration=federation.SESSION_DURATION_SECONDS if minted else None,
        )


register(AwsProvider())
