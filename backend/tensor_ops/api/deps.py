"""Request dependencies: DB session, authenticated principal, rate limiting."""
from __future__ import annotations

import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import new_session
from ..models import User
from ..security.auth import AuthError, verify_token
from ..security.authz import Principal, load_roles


def get_db() -> Iterator[Session]:
    s = new_session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def principal(request: Request, s: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> Principal:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise AuthError()
    claims = verify_token(settings, auth[7:].strip())
    sub = str(claims["sub"])
    user = None
    if settings.is_local:
        try:
            user = s.get(User, uuid.UUID(sub))
        except ValueError:
            user = None
    else:
        user = s.execute(select(User).where(User.external_subject == sub)).scalar_one_or_none()
        if user is None and claims.get("email") and claims.get("email_verified") is True:
            user = s.execute(select(User).where(User.email == str(claims["email"]).lower())).scalar_one_or_none()
            if user is not None and user.external_subject is None:
                user.external_subject = sub  # first login binds the IdP subject
    if user is None or not user.active:
        raise AuthError("Unknown or inactive user")  # users are provisioned, never self-registered
    s.info["user_id"] = user.id
    s.commit()  # next transaction carries app.user_id so the user's own memberships are visible under RLS
    roles = load_roles(s, user)
    s.info["tenant_ids"] = set(roles)
    s.commit()  # next transaction carries the tenant scope
    amr = claims.get("amr") or []
    p = Principal(user=user, roles=roles, mfa=any(m in amr for m in ("mfa", "otp", "hwk", "swk", "fido")),
                  require_mfa=settings.require_mfa_for_privileged)
    request.state.principal = p
    return p


class RateLimiter:
    """Small in-process token bucket per user and bucket name. Use a shared store (Redis) when running replicas."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, str], list[float]] = defaultdict(list)

    def check(self, key: str, bucket: str, limit: int, window_s: int) -> None:
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits[(key, bucket)] if now - t < window_s]
            if len(hits) >= limit:
                raise HTTPException(status_code=429, detail=f"rate limit: {limit} {bucket} per {window_s}s")
            hits.append(now)
            self._hits[(key, bucket)] = hits


limiter = RateLimiter()


def limit(bucket: str, n: int, window_s: int = 60):  # type: ignore[no-untyped-def]
    def dep(p: Principal = Depends(principal)) -> None:
        limiter.check(str(p.id), bucket, n, window_s)
    return dep
