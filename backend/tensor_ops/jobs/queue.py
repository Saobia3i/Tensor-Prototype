"""Postgres-backed durable job queue (FOR UPDATE SKIP LOCKED). Swap seam for Temporal (ADR-002)."""
from __future__ import annotations

import logging
import socket
import traceback
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..db import admin_session, new_session
from ..models import Job

log = logging.getLogger("tensor.jobs")
Handler = Callable[[Session, Job], dict[str, Any] | None]
HANDLERS: dict[str, Handler] = {}
WORKER_ID = f"{socket.gethostname()}:{uuid.uuid4().hex[:6]}"


class PermanentError(Exception):
    """Do not retry (e.g. validation failure, revoked credentials)."""


def handler(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn
    return deco


def enqueue(session: Session, kind: str, tenant_id: uuid.UUID | None, payload: dict[str, Any], *,
            idempotency_key: str | None = None, max_attempts: int = 3, requested_by: uuid.UUID | None = None,
            delay_s: int = 0) -> uuid.UUID:
    """Insert a job in the caller's transaction. Re-enqueueing with the same idempotency key returns the existing job."""
    job_id = uuid.uuid4()
    stmt = insert(Job).values(
        id=job_id, tenant_id=tenant_id, kind=kind, payload=payload, idempotency_key=idempotency_key,
        max_attempts=max_attempts, requested_by=requested_by, status="queued",
        run_after=datetime.now(timezone.utc) + timedelta(seconds=delay_s), attempts=0,
        created_at=datetime.now(timezone.utc),
    )
    if idempotency_key:
        stmt = stmt.on_conflict_do_nothing(index_elements=["idempotency_key"])
    inserted = session.execute(stmt.returning(Job.id)).scalar_one_or_none()
    if inserted is None:  # idempotency key already used: return the existing job
        return session.execute(select(Job.id).where(Job.idempotency_key == idempotency_key)).scalar_one()
    return job_id


def claim(kinds: list[str] | None = None) -> uuid.UUID | None:
    with admin_session() as s:
        q = select(Job).where(Job.status == "queued", Job.run_after <= datetime.now(timezone.utc))
        if kinds:
            q = q.where(Job.kind.in_(kinds))
        job = s.execute(q.order_by(Job.run_after).limit(1).with_for_update(skip_locked=True)).scalar_one_or_none()
        if job is None:
            return None
        job.status, job.locked_by, job.locked_at = "running", WORKER_ID, datetime.now(timezone.utc)
        job.attempts += 1
        s.commit()
        return job.id


def run_job(job_id: uuid.UUID) -> str:
    """Execute one claimed job inside a tenant-scoped (RLS) session for that job's tenant only."""
    with admin_session() as a:
        job = a.get(Job, job_id)
        assert job is not None
        kind, tenant_id = job.kind, job.tenant_id
    fn = HANDLERS.get(kind)
    s = new_session(tenant_ids=[tenant_id] if tenant_id else [], user_id=None)
    status, error, result = "succeeded", None, None
    try:
        if fn is None:
            raise PermanentError(f"no handler for {kind}")
        job_view = s.get(Job, job_id)
        assert job_view is not None
        result = fn(s, job_view)
        s.commit()
    except PermanentError as e:
        s.rollback()
        status, error = "dead", f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        s.rollback()
        status, error = "failed", f"{type(e).__name__}: {e}"
        log.warning("job failed", extra={"fields": {"job_id": str(job_id), "kind": kind,
                                                    "trace": traceback.format_exc(limit=3)}})
    finally:
        s.close()
    with admin_session() as a:
        job = a.get(Job, job_id)
        assert job is not None
        job.last_error, job.locked_by, job.locked_at = error, None, None
        if status == "failed" and job.attempts < job.max_attempts:
            job.status = "queued"
            job.run_after = datetime.now(timezone.utc) + timedelta(seconds=min(300, 2 ** job.attempts * 5))
        else:
            job.status = "dead" if status == "failed" else status
            job.result = result
            job.finished_at = datetime.now(timezone.utc)
        a.commit()
        return job.status


def run_pending(max_jobs: int = 100, kinds: list[str] | None = None, ignore_delay: bool = False) -> int:
    """Synchronously drain the queue (used by tests, the demo seeder and the CLI)."""
    n = 0
    while n < max_jobs:
        if ignore_delay:
            with admin_session() as a:
                for j in a.execute(select(Job).where(Job.status == "queued")).scalars():
                    j.run_after = datetime.now(timezone.utc)
                a.commit()
        jid = claim(kinds)
        if jid is None:
            break
        run_job(jid)
        n += 1
    return n
