"""Remediation tasks, time tracking and audit log."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import audit
from ...models import AuditEvent, Engagement, EngagementControl, Membership, RemediationTask, TimeEntry, User
from ...security.authz import Principal, get_scoped
from ...services.state_machines import REMEDIATION, REMEDIATION_STAFF_ONLY, check
from ..deps import get_db, principal

router = APIRouter(tags=["work"])


class TaskIn(BaseModel):
    engagement_id: uuid.UUID
    title: str = Field(min_length=3, max_length=300)
    description: str = Field(default="", max_length=5000)
    engagement_control_id: uuid.UUID | None = None
    owner_user_id: uuid.UUID | None = None
    due_date: date | None = None
    severity: str = Field(default="medium", pattern="^(low|medium|high|critical)$")


@router.post("/remediation-tasks", status_code=201)
def create_task(body: TaskIn, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    e = get_scoped(s, p, Engagement, body.engagement_id, "remediation.manage")
    if body.engagement_control_id:
        ec = s.get(EngagementControl, body.engagement_control_id)
        if ec is None or ec.engagement_id != e.id:
            raise HTTPException(status_code=422, detail="control not in engagement")
        if ec.status in ("not_started", "in_progress"):
            ec.status = "gap"
    if body.owner_user_id and not s.execute(select(Membership.id).where(
            Membership.user_id == body.owner_user_id, Membership.tenant_id == e.tenant_id)).first():
        raise HTTPException(status_code=422, detail="owner must be a member of this client")
    t = RemediationTask(tenant_id=e.tenant_id, **body.model_dump())
    s.add(t)
    s.flush()
    audit.record(s, "remediation.created", tenant_id=e.tenant_id, actor_id=p.id, target_type="remediation", target_id=t.id)
    s.commit()
    return {"id": str(t.id), "status": t.status}


class TaskPatch(BaseModel):
    version: int
    status: str | None = None
    owner_user_id: uuid.UUID | None = None
    due_date: date | None = None
    note: str = Field(default="", max_length=2000)


@router.patch("/remediation-tasks/{tid}")
def patch_task(tid: uuid.UUID, body: TaskPatch, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    t = get_scoped(s, p, RemediationTask, tid, "remediation.update_progress")
    if t.version != body.version:
        raise HTTPException(status_code=409, detail="task changed; reload")
    if body.status and body.status != t.status:
        check(REMEDIATION, t.status, body.status, "remediation")
        if body.status in REMEDIATION_STAFF_ONLY:
            p.require(t.tenant_id, "remediation.verify")
            if not p.user.is_staff:
                raise HTTPException(status_code=403, detail="only Tensor experts verify remediation")
            t.verified_by = p.id if body.status == "verified_closed" else None
        t.status = body.status
    if body.owner_user_id or body.due_date:
        p.require(t.tenant_id, "remediation.manage")
        t.owner_user_id = body.owner_user_id or t.owner_user_id
        t.due_date = body.due_date or t.due_date
    t.version += 1
    audit.record(s, "remediation.updated", tenant_id=t.tenant_id, actor_id=p.id, target_type="remediation",
                 target_id=t.id, status=t.status)
    s.commit()
    return {"id": str(t.id), "status": t.status, "version": t.version}


class TimeIn(BaseModel):
    engagement_id: uuid.UUID
    engagement_control_id: uuid.UUID | None = None
    minutes: int = Field(gt=0, le=12 * 60)
    activity: str = Field(pattern="^(evidence_collection|review|policy|questionnaire|signoff|meeting|remediation|other)$")
    ai_assisted: bool = False
    note: str = Field(default="", max_length=1000)
    work_date: date | None = None


@router.post("/time-entries", status_code=201)
def log_time(body: TimeIn, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    e = get_scoped(s, p, Engagement, body.engagement_id, "time.log")
    if body.engagement_control_id:
        ec = s.get(EngagementControl, body.engagement_control_id)
        if ec is None or ec.engagement_id != e.id:
            raise HTTPException(status_code=422, detail="control not in engagement")
    role_class = "expert" if p.user.can_sign else "engineer"
    te = TimeEntry(tenant_id=e.tenant_id, user_id=p.id, role_class=role_class,
                   **body.model_dump(exclude={"work_date"}), work_date=body.work_date or date.today())
    s.add(te)
    s.commit()
    return {"id": str(te.id), "role_class": role_class}


@router.get("/engagements/{eid}/time-entries")
def list_time(eid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    e = get_scoped(s, p, Engagement, eid, "time.log")
    users = {u.id: u.name for u in s.execute(select(User).where(User.is_staff.is_(True))).scalars()}
    return [{"id": str(t.id), "user": users.get(t.user_id, "?"), "minutes": t.minutes, "activity": t.activity,
             "role_class": t.role_class, "ai_assisted": t.ai_assisted, "note": t.note, "work_date": t.work_date.isoformat(),
             "engagement_control_id": str(t.engagement_control_id) if t.engagement_control_id else None}
            for t in s.execute(select(TimeEntry).where(TimeEntry.engagement_id == e.id)
                               .order_by(TimeEntry.work_date.desc())).scalars()]


@router.get("/audit-events")
def audit_events(tenant_id: uuid.UUID, limit: int = 200, p: Principal = Depends(principal),
                 s: Session = Depends(get_db)) -> list[dict]:
    p.require(tenant_id, "audit.read")
    users = {u.id: u.name for u in s.execute(select(User)).scalars()}
    rows = s.execute(select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
                     .order_by(AuditEvent.id.desc()).limit(min(max(limit, 1), 1000))).scalars()
    return [{"id": a.id, "at": a.created_at.isoformat(), "actor": users.get(a.actor_id, a.actor_type) if a.actor_id else a.actor_type,
             "action": a.action, "target_type": a.target_type, "target_id": a.target_id, "outcome": a.outcome,
             "correlation_id": a.correlation_id, "meta": a.meta} for a in rows]
