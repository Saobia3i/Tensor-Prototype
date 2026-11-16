"""Client deliverables: drafted from approved records, independently approved, then released by a signer."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import audit
from ...models import Engagement, Report
from ...security.authz import Principal, get_scoped
from ...services import reports as rep
from ...services.review import create_signoff
from ...services.state_machines import REPORT, check
from ..deps import get_db, principal
from .evidence import _issue_download

router = APIRouter(tags=["reports"])


def _out(r: Report) -> dict:
    return {"id": str(r.id), "engagement_id": str(r.engagement_id), "kind": r.kind, "status": r.status,
            "sha256": r.sha256, "created_at": r.created_at.isoformat(),
            "released_at": r.released_at.isoformat() if r.released_at else None,
            "generated_by": str(r.generated_by) if r.generated_by else None,
            "approved_by": str(r.approved_by) if r.approved_by else None}


def _can_read(p: Principal, r: Report) -> None:
    if r.status == "released":
        p.require(r.tenant_id, "report.read_released")
    else:
        p.require(r.tenant_id, "report.read_draft")


class ReportIn(BaseModel):
    kind: str = Field(pattern="^(evidence_index|gap_report|weekly_status)$")


@router.post("/engagements/{eid}/reports", status_code=201)
def generate(eid: uuid.UUID, body: ReportIn, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    e = get_scoped(s, p, Engagement, eid, "report.generate")
    content = rep.build(s, e, body.kind)
    sha = rep.digest(content)
    r = Report(tenant_id=e.tenant_id, engagement_id=e.id, kind=body.kind, content=content, sha256=sha,
               html=rep.render_html(content, "draft", sha), generated_by=p.id)
    s.add(r)
    s.flush()
    audit.record(s, "report.generated", tenant_id=e.tenant_id, actor_id=p.id, target_type="report", target_id=r.id, kind=body.kind)
    s.commit()
    return _out(r)


@router.get("/engagements/{eid}/reports")
def list_reports(eid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    e = get_scoped(s, p, Engagement, eid, "report.read_released")
    q = select(Report).where(Report.engagement_id == e.id)
    if not p.can(e.tenant_id, "report.read_draft"):
        q = q.where(Report.status == "released")
    return [_out(r) for r in s.execute(q.order_by(Report.created_at.desc())).scalars()]


@router.get("/reports/{rid}", response_class=HTMLResponse)
def view(rid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> HTMLResponse:
    r = s.get(Report, rid)
    if r is None:
        raise HTTPException(status_code=404, detail="Not found")
    _can_read(p, r)
    audit.record(s, "report.viewed", tenant_id=r.tenant_id, actor_id=p.id, target_type="report", target_id=r.id)
    s.commit()
    return HTMLResponse(r.html, headers={"content-security-policy": "default-src 'none'; style-src 'unsafe-inline'"})


@router.post("/reports/{rid}/approve")
def approve(rid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    r = get_scoped(s, p, Report, rid, "report.approve")
    check(REPORT, r.status, "approved", "report")
    if r.generated_by == p.id:
        raise HTTPException(status_code=403, detail="independent review: the approver must not be the author")
    stale = rep.stale_references(s, r.content)
    if stale:
        raise HTTPException(status_code=409, detail=f"{len(stale)} cited evidence items are no longer approved; regenerate")
    r.status, r.approved_by = "approved", p.id
    r.html = rep.render_html(r.content, "approved", r.sha256)
    audit.record(s, "report.approved", tenant_id=r.tenant_id, actor_id=p.id, target_type="report", target_id=r.id)
    s.commit()
    return _out(r)


@router.post("/reports/{rid}/release")
def release(rid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    r = get_scoped(s, p, Report, rid, "report.release")
    check(REPORT, r.status, "released", "report")
    stale = rep.stale_references(s, r.content)
    if stale:
        raise HTTPException(status_code=409, detail=f"{len(stale)} cited evidence items changed since approval; regenerate")
    create_signoff(s, tenant_id=r.tenant_id, engagement_id=r.engagement_id, subject_type="report", subject_id=r.id,
                   signer=p.user, statement=f"I release this {r.kind.replace('_', ' ')} to the client.", content=r.content)
    r.status, r.released_at = "released", datetime.now(timezone.utc)
    r.html = rep.render_html(r.content, "released", r.sha256)
    audit.record(s, "report.released", tenant_id=r.tenant_id, actor_id=p.id, target_type="report", target_id=r.id)
    s.commit()
    return _out(r)


@router.post("/reports/{rid}/download")
def download_link(rid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    r = s.get(Report, rid)
    if r is None:
        raise HTTPException(status_code=404, detail="Not found")
    _can_read(p, r)
    return _issue_download(s, p, r.tenant_id, "report", r.id)
