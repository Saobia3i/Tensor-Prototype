"""Database engines and sessions.

The application engine connects as a non-owner role subject to row-level security. Each transaction sets
`app.tenant_ids` and `app.user_id` from session.info so that RLS is a second, independent tenant boundary.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings


@lru_cache
def app_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True, future=True)


@lru_cache
def admin_engine() -> Engine:
    """Owner role: migrations, seeding and cross-tenant maintenance only. Never used by request handlers."""
    return create_engine(get_settings().admin_database_url, pool_pre_ping=True, future=True)


def _apply_scope(session: Session, connection) -> None:  # type: ignore[no-untyped-def]
    ids = session.info.get("tenant_ids") or set()
    uid = session.info.get("user_id")
    connection.execute(
        text("select set_config('app.tenant_ids', :ids, true), set_config('app.user_id', :uid, true)"),
        {"ids": ",".join(str(i) for i in sorted(ids, key=str)), "uid": str(uid) if uid else ""},
    )


@event.listens_for(Session, "after_begin")
def _after_begin(session: Session, transaction, connection) -> None:  # type: ignore[no-untyped-def]
    if session.info.get("rls", False):
        _apply_scope(session, connection)


def widen_scope(session: Session, tenant_id: uuid.UUID) -> None:
    """Add a tenant to the current transaction's RLS scope (e.g. right after creating that tenant)."""
    session.info.setdefault("tenant_ids", set()).add(tenant_id)
    if session.in_transaction():
        _apply_scope(session, session.connection())


_AppSession = sessionmaker(expire_on_commit=False, future=True)


def new_session(tenant_ids: Iterable[uuid.UUID] = (), user_id: uuid.UUID | None = None) -> Session:
    s = _AppSession(bind=app_engine())
    s.info["rls"] = True
    s.info["tenant_ids"] = set(tenant_ids)
    s.info["user_id"] = user_id
    return s


def admin_session() -> Session:
    s = _AppSession(bind=admin_engine())
    s.info["rls"] = False
    return s


@contextmanager
def session_scope(tenant_ids: Iterable[uuid.UUID] = (), user_id: uuid.UUID | None = None) -> Iterator[Session]:
    s = new_session(tenant_ids, user_id)
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
