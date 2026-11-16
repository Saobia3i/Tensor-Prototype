"""Customer-authorised, read-only connectors."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import audit
from ...connectors import service as svc
from ...connectors.github import _ORG
from ...jobs.queue import enqueue
from ...models import Connector, Engagement
from ...security.authz import Principal, get_scoped
from ..deps import get_db, limit, principal

router = APIRouter(tags=["connectors"])


def _out(c: Connector) -> dict:
    return {"id": str(c.id), "engagement_id": str(c.engagement_id), "provider": c.provider, "config": c.config,
            "status": c.status, "scopes": c.scopes, "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
            "last_error": c.last_error, "customer_approved": c.customer_approved_by is not None,
            "has_credential": c.secret_ref is not None}


@router.get("/connector-providers")
def providers(_: Principal = Depends(principal)) -> dict:
    return svc.PROVIDERS


class ConnectorIn(BaseModel):
    provider: str = Field(pattern="^(github)$")
    config: dict[str, str]


@router.post("/engagements/{eid}/connectors", status_code=201)
def create(eid: uuid.UUID, body: ConnectorIn, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    e = get_scoped(s, p, Engagement, eid, "connector.manage")
    if body.provider == "github" and not _ORG.match(body.config.get("org", "")):
        raise HTTPException(status_code=422, detail="config.org must be a GitHub organisation name")
    c = Connector(tenant_id=e.tenant_id, engagement_id=e.id, provider=body.provider,
                  config={"org": body.config["org"]}, scopes=[])
    s.add(c)
    s.flush()
    audit.record(s, "connector.created", tenant_id=e.tenant_id, actor_id=p.id, target_type="connector", target_id=c.id)
    s.commit()
    return _out(c)


@router.get("/engagements/{eid}/connectors")
def list_c(eid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    e = get_scoped(s, p, Engagement, eid, "connector.read")
    return [_out(c) for c in s.execute(select(Connector).where(Connector.engagement_id == e.id)).scalars()]


@router.post("/connectors/{cid}/customer-approve")
def customer_approve(cid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    c = get_scoped(s, p, Connector, cid, "connector.customer_approve")
    c.customer_approved_by = p.id
    audit.record(s, "connector.customer_approved", tenant_id=c.tenant_id, actor_id=p.id, target_type="connector",
                 target_id=c.id, scopes=svc.PROVIDERS[c.provider]["mode"])
    s.commit()
    return _out(c)


class AuthIn(BaseModel):
    token: str = Field(min_length=10, max_length=500)


@router.post("/connectors/{cid}/authorize")
def authorize(cid: uuid.UUID, body: AuthIn, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    c = get_scoped(s, p, Connector, cid, "connector.manage")
    if c.status == "revoked":
        raise HTTPException(status_code=409, detail="connector revoked; create a new one")
    res = svc.authorize(s, c, body.token, p.id)
    s.commit()
    return _out(c) | {"result": res}


@router.post("/connectors/{cid}/sync", status_code=202, dependencies=[Depends(limit("connector_sync", 10))])
def sync(cid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    c = get_scoped(s, p, Connector, cid, "connector.manage")
    if c.status not in ("active", "degraded"):
        raise HTTPException(status_code=409, detail=f"connector is {c.status}")
    job = enqueue(s, "connector_sync", c.tenant_id, {"connector_id": str(c.id)},
                  idempotency_key=svc.sync_idempotency_key(c), requested_by=p.id, max_attempts=5)
    s.commit()
    return {"job_id": str(job)}


@router.post("/connectors/{cid}/revoke")
def revoke(cid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    c = s.get(Connector, cid)
    if c is None:
        raise HTTPException(status_code=404, detail="Not found")
    if not (p.can(c.tenant_id, "connector.manage") or p.can(c.tenant_id, "connector.customer_approve")):
        p.require(c.tenant_id, "connector.manage")
    svc.revoke(s, c, p.id)
    s.commit()
    return _out(c)
