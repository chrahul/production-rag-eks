"""
Tokens.

Phase 0 hardcoded three Python objects. This replaces that with signed JWTs.

The point of this file is what the application does NOT do. It does not manage
users, resolve group membership, or store entitlements. It verifies a signature
and reads claims.

That is why the identity provider is pluggable. Entra, Okta, Cognito, Keycloak
and Authentik all issue signed tokens carrying claims. Swapping between them is
a change to the verification key and the claim names, not to any code that
makes an authorization decision.

For the lab the tokens are signed with a local key. In production the key comes
from the identity provider's JWKS endpoint. The verification logic is the same.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import jwt

# In production this is not a shared secret at all. The identity provider signs
# with a private key and publishes the public half at a JWKS endpoint, and the
# application fetches it. HS256 with a shared secret keeps the lab simple
# without changing what the application does with the claims.
JWT_SECRET = os.getenv("JWT_SECRET", "phase0-local-development-only")
JWT_ALGORITHM = "HS256"
JWT_ISSUER = "https://auth.local/production-rag"
JWT_AUDIENCE = "rag-api"

TOKEN_TTL_SECONDS = 3600


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired or unverifiable."""


@dataclass
class Claims:
    """What the application reads out of a verified token.

    This is deliberately small. Anything not needed to make an authorization
    decision does not belong here.
    """

    subject: str
    name: str
    clearance: str
    teams: list[str]
    customers: list[str]


def issue(
    subject: str,
    name: str,
    clearance: str,
    teams: list[str] | None = None,
    customers: list[str] | None = None,
    ttl: int = TOKEN_TTL_SECONDS,
) -> str:
    """Mint a signed token.

    This function exists only because the lab has no identity provider. In
    production nothing in the application issues tokens.
    """
    now = int(time.time())
    payload = {
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "sub": subject,
        "iat": now,
        "exp": now + ttl,
        "name": name,
        "clearance": clearance,
        "teams": teams or [],
        "customers": customers or [],
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify(token: str) -> Claims:
    """Verify a token and return its claims.

    Signature, expiry, issuer and audience are all checked. A token that fails
    any of those is rejected before any authorization logic runs.
    """
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
        )
    except jwt.ExpiredSignatureError:
        raise TokenError("token has expired")
    except jwt.InvalidAudienceError:
        raise TokenError("token was not issued for this service")
    except jwt.InvalidIssuerError:
        raise TokenError("token came from an unknown issuer")
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"token is not valid: {exc}")

    clearance = payload.get("clearance")
    if not clearance:
        raise TokenError("token carries no clearance claim")

    return Claims(
        subject=payload["sub"],
        name=payload.get("name", payload["sub"]),
        clearance=clearance,
        teams=payload.get("teams", []),
        customers=payload.get("customers", []),
    )
