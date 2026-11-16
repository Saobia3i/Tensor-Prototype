"""Verified deletion requests with two-person approval."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import audit
from ...jobs.queue import enqueue
from ...models import DeletionRequest, EvidenceItem
from ...security.authz import Principal, get_scoped
from ..deps import get_db, principal

router = APIRouter(tags=["data deletion"])


class DelIn(BaseModel):
    tenant_id: uuid.UUID
    scope: str = Field(pattern="^(evidence_item|tenant)$")
    target_id: uuid.UUID


@router.post("/deletion-requests", status_code=201)
def request_deletion(body: DelIn, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    p.require(body.tenant_id, "deletion.request")
    if body.scope == "evidence_item":
        ev = s.get(EvidenceItem, body.target_id)
        if ev is None or ev.tenant_id != body.tenant_id:
            raise HTTPException(status_code=404, detail="Not found")
    elif body.target_id != body.tenant_id:
        raise HTTPException(status_code=422, detail="tenant deletion must target the tenant itself")
    d = DeletionRequest(tenant_id=body.tenant_id, scope=body.scope, target_id=body.target_id, requested_by=p.id)
    s.add(d)
    s.flush()
    audit.record(s, "deletion.requested", tenant_id=body.tenant_id, actor_id=p.id, target_type=body.scope,
                 target_id=body.target_id)
    s.commit()
    return {"id": str(d.id), "status": d.status}


@router.get("/tenants/{tenant_id}/deletion-requests")
def list_requests(tenant_id: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    p.require(tenant_id, "deletion.request")
    return [{"id": str(d.id), "scope": d.scope, "target_id": str(d.target_id), "status": d.status, "result": d.result,
             "requested_by": str(d.requested_by), "approved_by": str(d.approved_by) if d.approved_by else None}
            for d in s.execute(select(DeletionRequest).where(DeletionRequest.tenant_id == tenant_id)).scalars()]


@router.post("/deletion-requests/{did}/approve", status_code=202)
def approve(did: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    d = get_scoped(s, p, DeletionRequest, did, "deletion.approve")
    if d.status != "requested":
        raise HTTPException(status_code=409, detail=f"request is {d.status}")
    if d.requested_by == p.id:
        raise HTTPException(status_code=403, detail="two-person rule: a different person must approve")
    d.status, d.approved_by = "approved", p.id
    job = enqueue(s, "execute_deletion", d.tenant_id, {"deletion_request_id": str(d.id)},
                  idempotency_key=f"delete:{d.id}", requested_by=p.id)
    audit.record(s, "deletion.approved", tenant_id=d.tenant_id, actor_id=p.id, target_type=d.scope, target_id=d.target_id)
    s.commit()
    return {"id": str(d.id), "status": d.status, "job_id": str(job)}
