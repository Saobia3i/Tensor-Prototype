"""Evidence upload sessions, ingestion, detail with provenance, manual mapping and expiring downloads."""
from __future__ import annotations

import secrets
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import audit
from ...config import get_settings
from ...ingestion.storage import get_store
from ...jobs.queue import enqueue
from ...models import (
    AIArtifact,
    Control,
    DownloadToken,
    Engagement,
    EngagementControl,
    EvidenceChunk,
    EvidenceControlLink,
    EvidenceItem,
    EvidenceRequest,
    Job,
    Report,
    ReviewDecision,
    User,
)
from ...security.authz import Principal, get_scoped
from ...services.evidence import create_upload_session, hash_token, receive_upload, set_status
from ...services.state_machines import EVIDENCE_HUMAN_ONLY
from ..deps import get_db, limit, principal

router = APIRouter(tags=["evidence"])


def ev_out(e: EvidenceItem) -> dict:
    return {"id": str(e.id), "engagement_id": str(e.engagement_id), "title": e.title, "filename": e.original_filename,
            "status": e.status, "version": e.version, "lineage_id": str(e.lineage_id), "source": e.source,
            "detected_type": e.detected_type, "size_bytes": e.size_bytes, "sha256": e.sha256, "error": e.error,
            "scan_result": e.scan_result, "request_id": str(e.request_id) if e.request_id else None,
            "collected_at": e.collected_at.isoformat() if e.collected_at else None,
            "period_start": e.period_start.isoformat() if e.period_start else None,
            "period_end": e.period_end.isoformat() if e.period_end else None,
            "expires_at": e.expires_at.isoformat() if e.expires_at else None,
            "retention_until": e.retention_until.isoformat() if e.retention_until else None,
            "page_count": e.page_count, "created_at": e.created_at.isoformat() if e.created_at else None}


def _can_see(p: Principal, e: EvidenceItem) -> None:
    if p.can(e.tenant_id, "evidence.read"):
        return
    if p.can(e.tenant_id, "evidence.read_approved") and e.status == "approved":
        return
    p.require(e.tenant_id, "evidence.read")  # raises 404/403 consistently


class UploadSessionIn(BaseModel):
    engagement_id: uuid.UUID
    title: str = Field(min_length=1, max_length=300)
    filename: str = Field(min_length=1, max_length=300, pattern=r"^[^/\\\x00]+$")
    size_bytes: int = Field(gt=0)
    declared_mime: str | None = Field(default=None, max_length=120)
    request_id: uuid.UUID | None = None
    supersedes_id: uuid.UUID | None = None
    period_start: date | None = None
    period_end: date | None = None


@router.post("/evidence/upload-sessions", status_code=201, dependencies=[Depends(limit("uploads", 60))])
def upload_session(body: UploadSessionIn, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    eng = get_scoped(s, p, Engagement, body.engagement_id, "evidence.upload")
    if body.request_id:
        r = s.get(EvidenceRequest, body.request_id)
        if r is None or r.engagement_id != eng.id:
            raise HTTPException(status_code=422, detail="request not in this engagement")
    prev = None
    if body.supersedes_id:
        prev = get_scoped(s, p, EvidenceItem, body.supersedes_id, "evidence.upload")
        if prev.engagement_id != eng.id:
            raise HTTPException(status_code=422, detail="superseded evidence is in another engagement")
    ev, token = create_upload_session(s, tenant_id=eng.tenant_id, engagement_id=eng.id, user_id=p.id, title=body.title,
                                      filename=body.filename, declared_mime=body.declared_mime,
                                      size_bytes=body.size_bytes, request_id=body.request_id, supersedes=prev,
                                      period_start=body.period_start, period_end=body.period_end)
    audit.record(s, "evidence.upload_session", tenant_id=eng.tenant_id, actor_id=p.id, target_type="evidence", target_id=ev.id)
    s.commit()
    return {"evidence_id": str(ev.id), "upload_url": f"/v1/uploads/{ev.id}", "upload_token": token,
            "expires_at": ev.upload_expires_at.isoformat() if ev.upload_expires_at else None}


@router.put("/uploads/{evidence_id}", status_code=202)
async def upload(evidence_id: uuid.UUID, request: Request, token: str, p: Principal = Depends(principal),
                 s: Session = Depends(get_db)) -> dict:
    ev = get_scoped(s, p, EvidenceItem, evidence_id, "evidence.upload")
    max_b = get_settings().max_upload_bytes
    declared = int(request.headers.get("content-length") or 0)
    if declared > max_b:
        raise HTTPException(status_code=413, detail="too large")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > max_b:
            raise HTTPException(status_code=413, detail="too large")
    job = receive_upload(s, ev, token, bytes(data), p.id)
    if ev.request_id:
        r = s.get(EvidenceRequest, ev.request_id)
        if r and r.status == "open":
            r.status = "submitted"
    s.commit()
    return {"evidence_id": str(ev.id), "status": ev.status, "job_id": str(job)}


@router.get("/evidence")
def list_evidence(engagement_id: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    eng = get_scoped(s, p, Engagement, engagement_id, "evidence.read_approved")
    q = select(EvidenceItem).where(EvidenceItem.engagement_id == eng.id, EvidenceItem.status != "awaiting_upload")
    if not p.can(eng.tenant_id, "evidence.read"):
        q = q.where(EvidenceItem.status == "approved")
    return [ev_out(e) for e in s.execute(q.order_by(EvidenceItem.created_at.desc())).scalars()]


@router.get("/evidence/{evidence_id}")
def evidence_detail(evidence_id: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    ev = s.get(EvidenceItem, evidence_id)
    if ev is None:
        raise HTTPException(status_code=404, detail="Not found")
    _can_see(p, ev)
    chunks = s.execute(select(EvidenceChunk).where(EvidenceChunk.evidence_id == ev.id)
                       .order_by(EvidenceChunk.ordinal)).scalars().all()
    links = s.execute(select(EvidenceControlLink, EngagementControl, Control)
                      .join(EngagementControl, EngagementControl.id == EvidenceControlLink.engagement_control_id)
                      .join(Control, Control.id == EngagementControl.control_id)
                      .where(EvidenceControlLink.evidence_id == ev.id)).all()
    is_staff_view = p.can(ev.tenant_id, "ai.review")
    arts = s.execute(select(AIArtifact).where(AIArtifact.subject_id == ev.id).order_by(AIArtifact.created_at.desc())
                     ).scalars().all() if is_staff_view else []
    decisions = {}
    if arts:
        users = {u.id: u.name for u in s.execute(select(User).where(User.is_staff.is_(True))).scalars()}
        for d in s.execute(select(ReviewDecision).where(ReviewDecision.artifact_id.in_([a.id for a in arts]))).scalars():
            decisions.setdefault(str(d.artifact_id), []).append({
                "decision": d.decision, "comment": d.comment, "reviewer": users.get(d.reviewer_id, "?"),
                "at": d.created_at.isoformat()})
    versions = s.execute(select(EvidenceItem.id, EvidenceItem.version, EvidenceItem.status)
                         .where(EvidenceItem.lineage_id == ev.lineage_id).order_by(EvidenceItem.version)).all()
    audit.record(s, "evidence.viewed", tenant_id=ev.tenant_id, actor_id=p.id, target_type="evidence", target_id=ev.id)
    s.commit()
    return ev_out(ev) | {
        "chunks": [{"id": str(c.id), "handle": f"E{i + 1}", "page": c.page, "char_start": c.char_start,
                    "char_end": c.char_end, "text": c.text, "method": c.extraction_method} for i, c in enumerate(chunks)],
        "links": [{"id": str(ln.id), "engagement_control_id": str(ec.id), "control": c.code, "title": c.title,
                   "status": ln.status, "rationale": ln.rationale} for ln, ec, c in links],
        "artifacts": [{"id": str(a.id), "task_type": a.task_type, "review_state": a.review_state, "output": a.output,
                       "final_output": a.final_output, "confidence": a.confidence, "decision": a.decision,
                       "warnings": a.validation_warnings, "version": a.version, "created_at": a.created_at.isoformat(),
                       "ai_run_id": str(a.ai_run_id) if a.ai_run_id else None,
                       "decisions": decisions.get(str(a.id), [])} for a in arts],
        "versions": [{"id": str(i), "version": v, "status": st} for i, v, st in versions],
        "can_review": is_staff_view,
    }


@router.post("/evidence/{evidence_id}/ai-review", status_code=202, dependencies=[Depends(limit("ai_jobs", 30))])
def request_ai_review(evidence_id: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    ev = get_scoped(s, p, EvidenceItem, evidence_id, "ai.run")
    if ev.status not in ("extracted", "proposed", "expert_review", "needs_information"):
        raise HTTPException(status_code=409, detail=f"evidence is {ev.status}; cannot review yet")
    job = enqueue(s, "ai_review_evidence", ev.tenant_id, {"evidence_id": str(ev.id)},
                  idempotency_key=f"ai_review:{ev.id}:{ev.status_version}:manual", requested_by=p.id)
    s.commit()
    return {"job_id": str(job)}


class LinkIn(BaseModel):
    engagement_control_id: uuid.UUID
    rationale: str = Field(min_length=5, max_length=2000)


@router.post("/evidence/{evidence_id}/links", status_code=201)
def manual_link(evidence_id: uuid.UUID, body: LinkIn, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    """Expert maps evidence to a control directly (no AI) and approves the evidence."""
    ev = get_scoped(s, p, EvidenceItem, evidence_id, "evidence.review")
    ec = s.get(EngagementControl, body.engagement_control_id)
    if ec is None or ec.engagement_id != ev.engagement_id:
        raise HTTPException(status_code=422, detail="control not in this engagement")
    if ev.status not in ("extracted", "proposed", "expert_review", "needs_information", "approved"):
        raise HTTPException(status_code=409, detail=f"evidence is {ev.status}")
    ln = s.execute(select(EvidenceControlLink).where(EvidenceControlLink.evidence_id == ev.id,
                                                     EvidenceControlLink.engagement_control_id == ec.id)).scalar_one_or_none()
    if ln is None:
        ln = EvidenceControlLink(tenant_id=ev.tenant_id, evidence_id=ev.id, engagement_control_id=ec.id)
        s.add(ln)
    ln.status, ln.rationale, ln.decided_by = "approved", body.rationale, p.id
    if ev.status == "extracted":
        set_status(ev, "expert_review")
    if ev.status == "needs_information":
        set_status(ev, "expert_review")
    if ev.status != "approved":
        set_status(ev, "approved")
    if ec.status in ("not_started", "in_progress", "gap"):
        ec.status = "evidence_collected"
    audit.record(s, "evidence.linked_manually", tenant_id=ev.tenant_id, actor_id=p.id, target_type="evidence",
                 target_id=ev.id, control=str(ec.id))
    s.commit()
    return {"link_id": str(ln.id), "evidence_status": ev.status}


class StatusIn(BaseModel):
    status: str
    comment: str = Field(default="", max_length=2000)


@router.post("/evidence/{evidence_id}/status")
def set_evidence_status(evidence_id: uuid.UUID, body: StatusIn, p: Principal = Depends(principal),
                        s: Session = Depends(get_db)) -> dict:
    ev = get_scoped(s, p, EvidenceItem, evidence_id, "evidence.review")
    if body.status not in EVIDENCE_HUMAN_ONLY | {"expert_review"}:
        raise HTTPException(status_code=422, detail="reviewers may set approved, rejected, needs_information or expert_review")
    if body.status in ("rejected", "needs_information") and len(body.comment.strip()) < 5:
        raise HTTPException(status_code=422, detail="a comment is required")
    if body.status == "approved":
        has = s.execute(select(EvidenceControlLink.id).where(EvidenceControlLink.evidence_id == ev.id,
                                                             EvidenceControlLink.status == "approved")).first()
        if not has:
            raise HTTPException(status_code=409, detail="approve a control mapping first")
    set_status(ev, body.status)
    audit.record(s, f"evidence.{body.status}", tenant_id=ev.tenant_id, actor_id=p.id, target_type="evidence",
                 target_id=ev.id, comment=body.comment[:200])
    s.commit()
    return {"id": str(ev.id), "status": ev.status}


def _issue_download(s: Session, p: Principal, tenant_id: uuid.UUID, subject_type: str, subject_id: uuid.UUID) -> dict:
    token = secrets.token_urlsafe(32)
    s.add(DownloadToken(tenant_id=tenant_id, token_hash=hash_token(token), subject_type=subject_type,
                        subject_id=subject_id, user_id=p.id,
                        expires_at=datetime.now(timezone.utc) + timedelta(seconds=get_settings().download_ttl_s)))
    audit.record(s, f"{subject_type}.download_link", tenant_id=tenant_id, actor_id=p.id, target_type=subject_type,
                 target_id=subject_id)
    s.commit()
    return {"download_url": f"/v1/downloads/{token}", "expires_in_s": get_settings().download_ttl_s}


@router.post("/evidence/{evidence_id}/download")
def evidence_download(evidence_id: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    ev = s.get(EvidenceItem, evidence_id)
    if ev is None:
        raise HTTPException(status_code=404, detail="Not found")
    _can_see(p, ev)
    if ev.status in ("awaiting_upload", "quarantined", "scanning", "rejected_security", "deleted", "duplicate"):
        raise HTTPException(status_code=409, detail="file is not available for download")
    return _issue_download(s, p, ev.tenant_id, "evidence", ev.id)


@router.get("/downloads/{token}")
def download(token: str, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> Response:
    dt = s.execute(select(DownloadToken).where(DownloadToken.token_hash == hash_token(token))).scalar_one_or_none()
    if dt is None or dt.user_id != p.id:
        raise HTTPException(status_code=404, detail="Not found")
    if dt.expires_at < datetime.now(timezone.utc) or dt.used_at is not None:
        raise HTTPException(status_code=410, detail="download link expired")
    dt.used_at = datetime.now(timezone.utc)
    audit.record(s, f"{dt.subject_type}.downloaded", tenant_id=dt.tenant_id, actor_id=p.id,
                 target_type=dt.subject_type, target_id=dt.subject_id)
    s.commit()
    if dt.subject_type == "evidence":
        ev = s.get(EvidenceItem, dt.subject_id)
        if ev is None or not ev.object_key:
            raise HTTPException(status_code=404, detail="Not found")
        data = get_store().get(ev.object_key)
        safe = "".join(ch for ch in ev.original_filename if ch.isalnum() or ch in "._- ")[:100] or "evidence"
        return Response(data, media_type="application/octet-stream",
                        headers={"content-disposition": f'attachment; filename="{safe}"'})
    if dt.subject_type == "report":
        rep = s.get(Report, dt.subject_id)
        if rep is None:
            raise HTTPException(status_code=404, detail="Not found")
        return HTMLResponse(rep.html, headers={"content-security-policy": "default-src 'none'; style-src 'unsafe-inline'"})
    raise HTTPException(status_code=404, detail="Not found")


@router.get("/jobs/{job_id}")
def job_status(job_id: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    j = s.get(Job, job_id)
    if j is None or not (j.requested_by == p.id or (j.tenant_id and j.tenant_id in p.tenant_ids)):
        raise HTTPException(status_code=404, detail="Not found")
    return {"id": str(j.id), "kind": j.kind, "status": j.status, "attempts": j.attempts,
            "last_error": j.last_error if p.user.is_staff else None, "result": j.result}
