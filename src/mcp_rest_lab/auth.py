"""auth.py — shared identity helpers for MCP and REST (§5.2).

HS256 + a local secret is a deliberate stand-in for a real IdP (Cognito/Auth0).
The claim *shapes* (iss/aud/sub/role/exp + custom claims) are kept identical to
what a production token would carry, so swapping to RS256/JWKS later is a verify()
change, not a rewrite of every caller.
"""

from __future__ import annotations

import os
import time

import jwt  # pyjwt 2.13.0

# Both issuer and audience are the app name. verify() validates both so a token
# minted for some other system is rejected.
ISSUER = "mcp-rest-lab"
AUDIENCE = "mcp-rest-lab"
ALGORITHM = "HS256"
TOKEN_TTL_SECONDS = 3600

# Dev-only default. Real deployments MUST set JWT_SECRET in the environment.
_DEV_SECRET = "dev-only-insecure-secret-change-me"

# The A-Team members this lab knows about. Teacher tokens carry this as their
# `students` claim (the set they are authorized to query).
STUDENTS = [
    "hannibal",
    "face",
    "ba_baracus",
    "murdock",
]


def secret() -> str:
    """Return the signing secret, from JWT_SECRET env or the dev default."""
    return os.environ.get("JWT_SECRET", _DEV_SECRET)


def make_token(sub: str, role: str, **extra) -> str:
    """Mint an HS256 token for ``sub`` with ``role`` and any extra claims.

    Extra claims (e.g. ``students=[...]`` for a teacher) are merged in alongside
    the standard set so the token shape mirrors a real IdP-issued JWT.
    """
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": sub,
        "role": role,
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
        **extra,
    }
    return jwt.encode(claims, secret(), algorithm=ALGORITHM)


def verify(token: str) -> dict:
    """Decode and validate a token, returning its claims.

    Passes ``audience`` and ``issuer`` so pyjwt actually enforces them (not just
    decodes). Raises ``jwt.InvalidTokenError`` (or a subclass, e.g.
    ``ExpiredSignatureError``) on any bad/expired/forged token.
    """
    return jwt.decode(
        token,
        secret(),
        algorithms=[ALGORITHM],
        audience=AUDIENCE,
        issuer=ISSUER,
    )


def bearer_from_header(authorization: str | None) -> str:
    """Extract the raw token from an ``Authorization: Bearer <t>`` header value.

    Raises ValueError if the header is missing or not a Bearer scheme, so callers
    can map it to a 401 / MCP error rather than silently treating it as anonymous.
    """
    if not authorization:
        raise ValueError("missing Authorization header")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise ValueError("Authorization header is not a Bearer token")
    return parts[1].strip()
