"""Extension contract. Only server-side provider adapters may produce verified identities.

No callback or binding endpoint is enabled until a provider is configured and its
state, PKCE, nonce, signature, issuer and audience checks are implemented and tested.
"""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select

from app.core.errors import Unauthorized
from app.core.security import create_access_token
from app.models.identity import User, UserIdentity


@dataclass(frozen=True)
class VerifiedIdentity:
    provider: str
    issuer: str
    subject: str


class ExternalLoginAdapter(Protocol):
    def authorization_url(self, *, state: str, challenge: str, nonce: str) -> str: ...

    def verify_callback(self, *, code: str, verifier: str, nonce: str) -> VerifiedIdentity: ...


def login_linked_identity(db, identity: VerifiedIdentity) -> str:
    """Trusted adapter result only. An email match must never create or merge a user."""
    user = db.scalar(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(
            UserIdentity.provider == identity.provider,
            UserIdentity.issuer == identity.issuer,
            UserIdentity.subject == identity.subject,
        )
    )
    if user is None or not user.is_active:
        raise Unauthorized("此第三方身份尚未绑定可用账户")
    return create_access_token(str(user.id), {"ver": user.auth_version})
