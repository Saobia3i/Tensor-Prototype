"""Policy drafting from Tensor templates -> client approval -> published as evidence (through the normal pipeline)."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import audit
from ...models import Engagement, Policy, Tenant
from ...security.authz import Principal, get_scoped
from ...services import policies as pol
from ...services.evidence import ingest_system_document
from ..deps import get_db, principal

router = APIRouter(tags=["policies"])


def _out(x: Policy, body: bool = False) -> dict:
    d = {"id": str(x.id), "engagement_id": str(x.engagement_id), "template_key": x.template_key, "title": x.title,
         "status": x.status, "version": x.version, "parameters": x.parameters,
         "evidence_id": str(x.evidence_id) if x.evidence_id else None, "updated_at": x.updated_at.isoformat()}
    if body:
        d["body_markdown"] = x.body_markdown
    return d


@router.get("/policy-templates")
def templates(_: Principal = Depends(principal)) -> list[dict]:
    return pol.list_templates()


class PolicyIn(BaseModel):
    template_key: str = Field(pattern=r"^[a-z0-9\-]+$")
    parameters: dict[str, str] = Field(default_factory=dict)


@router.post("/engagements/{eid}/policies", status_code=201)
def generate(eid: uuid.UUID, body: PolicyIn, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    e = get_scoped(s, p, Engagement, eid, "policy.manage")
    t = s.get(Tenant, e.tenant_id)
    params = {"company_name": t.name if t else ""} | {k: v for k, v in body.parameters.items() if len(k) < 50}
    try:
        title, md, missing = pol.render(body.template_key, params)
    except ValueError as ex:
        raise HTTPException(status_code=422, detail=str(ex)) from ex
    x = Policy(tenant_id=e.tenant_id, engagement_id=e.id, template_key=body.template_key, title=title,
               body_markdown=md, parameters=params)
    s.add(x)
    s.flush()
    audit.record(s, "policy.drafted", tenant_id=e.tenant_id, actor_id=p.id, target_type="policy", target_id=x.id)
    s.commit()
    return _out(x, True) | {"missing_parameters": missing}


@router.get("/engagements/{eid}/policies")
def list_policies(eid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    e = get_scoped(s, p, Engagement, eid, "policy.read")
    return [_out(x) for x in s.execute(select(Policy).where(Policy.engagement_id == e.id).order_by(Policy.title)).scalars()]


@router.get("/policies/{pid}")
def get_policy(pid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    return _out(get_scoped(s, p, Policy, pid, "policy.read"), True)


class PolicyPatch(BaseModel):
    version: int
    body_markdown: str = Field(min_length=20, max_length=100_000)


@router.patch("/policies/{pid}")
def edit(pid: uuid.UUID, body: PolicyPatch, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    x = get_scoped(s, p, Policy, pid, "policy.manage")
    if x.version != body.version:
        raise HTTPException(status_code=409, detail="policy changed; reload")
    if x.status == "published":
        raise HTTPException(status_code=409, detail="published policies are immutable; create a new version")
    x.body_markdown, x.status = body.body_markdown, "draft"  # any edit requires fresh client approval
    x.approved_by_client = None
    x.version += 1
    audit.record(s, "policy.edited", tenant_id=x.tenant_id, actor_id=p.id, target_type="policy", target_id=x.id)
    s.commit()
    return _out(x, True)


@router.post("/policies/{pid}/client-approve")
def client_approve(pid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    x = get_scoped(s, p, Policy, pid, "policy.client_approve")
    if x.status != "draft":
        raise HTTPException(status_code=409, detail=f"policy is {x.status}")
    if "TO CONFIRM]" in x.body_markdown:
        raise HTTPException(status_code=409, detail="fill in all [TO CONFIRM] placeholders first")
    x.status, x.approved_by_client = "client_approved", p.id
    audit.record(s, "policy.client_approved", tenant_id=x.tenant_id, actor_id=p.id, target_type="policy", target_id=x.id)
    s.commit()
    return _out(x)


@router.post("/policies/{pid}/publish")
def publish(pid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    x = get_scoped(s, p, Policy, pid, "policy.manage")
    if x.status != "client_approved":
        raise HTTPException(status_code=409, detail="the client must approve the policy before it is published")
    header = f"Approved by client user {x.approved_by_client} on {date.today().isoformat()} (version {x.version}).\n\n"
    ev = ingest_system_document(s, tenant_id=x.tenant_id, engagement_id=x.engagement_id,
                                title=f"{x.title} (approved policy v{x.version})",
                                filename=f"{x.template_key}-v{x.version}.md",
                                data=(header + x.body_markdown).encode(), source="policy",
                                source_ref=f"policy:{x.id}:v{x.version}", actor_id=p.id)
    x.status, x.evidence_id = "published", ev.id
    audit.record(s, "policy.published", tenant_id=x.tenant_id, actor_id=p.id, target_type="policy", target_id=x.id,
                 evidence_id=str(ev.id))
    s.commit()
    return _out(x)
