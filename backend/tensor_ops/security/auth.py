"""Authentication: OIDC JWT verification (required outside local) or dev HS256 tokens (local only)."""
from __future__ import annotations

import time
import uuid
from functools import lru_cache
from typing import Any

import jwt
from fastapi import HTTPException

from ..config import Settings


class AuthError(HTTPException):
    def __init__(self, detail: str = "Not authenticated") -> None:
        super().__init__(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)


def issue_dev_token(settings: Settings, user_id: uuid.UUID, mfa: bool = True, ttl_s: int = 8 * 3600) -> str:
    if not settings.is_local or not settings.dev_jwt_secret:
        raise RuntimeError("Dev tokens are only available in the local environment")
    now = int(time.time())
    claims = {"sub": str(user_id), "iat": now, "exp": now + ttl_s, "iss": "tensor-dev", "aud": "tensor-ops",
              "amr": ["pwd", "mfa"] if mfa else ["pwd"]}
    return jwt.encode(claims, settings.dev_jwt_secret, algorithm="HS256")


def verify_token(settings: Settings, token: str) -> dict[str, Any]:
    try:
        if settings.is_local:
            return jwt.decode(token, settings.dev_jwt_secret, algorithms=["HS256"], audience="tensor-ops",
                              issuer="tensor-dev", options={"require": ["exp", "sub"]})
        key = _jwks_client(settings.oidc_jwks_url).get_signing_key_from_jwt(token)  # type: ignore[arg-type]
        return jwt.decode(token, key.key, algorithms=["RS256", "ES256"], audience=settings.oidc_audience,
                          issuer=settings.oidc_issuer, options={"require": ["exp", "sub", "iss", "aud"]})
    except jwt.PyJWTError as e:
        raise AuthError("Invalid or expired token") from e
