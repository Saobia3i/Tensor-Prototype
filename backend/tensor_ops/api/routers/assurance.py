"""Analytics, Type II tests, exceptions, milestones, spreadsheet import and control export."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import audit
from ...models import AuditException, ControlTest, Engagement, EngagementControl, Milestone, RemediationTask, User
from ...security.authz import Principal, get_scoped
from ...services import assurance
from ...services.analytics import engagement_analytics, gap_summary
from ..deps import get_db, limit, principal
from .questionnaires import _csv_safe

router = APIRouter(tags=["assurance & analytics"])


@router.get("/engagements/{eid}/analytics")
def analytics(eid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    e = get_scoped(s, p, Engagement, eid, "engagement.read")
    return engagement_analytics(s, e)


@router.get("/engagements/{eid}/executive-summary")
def executive_summary(eid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    e = get_scoped(s, p, Engagement, eid, "engagement.read")
    a = engagement_analytics(s, e)
    signer = s.get(User, e.signer_id) if e.signer_id else None
    return {"engagement": e.name, "frameworks": e.frameworks, "target_date": e.target_date.isoformat() if e.target_date else None,
            "signer": {"name": signer.name, "title": signer.expert_title} if signer else None,
            "findings": gap_summary(a), "analytics": a,
            "basis": "Rule-based from Tensor records (approved evidence, tests, exceptions). Not an audit opinion."}


@router.get("/metrics/readiness")
def portfolio_readiness(p: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    scope = p.tenants_with("metrics.read")
    if not scope:
        return []
    out = []
    for e in s.execute(select(Engagement).where(Engagement.tenant_id.in_(scope))).scalars():
        a = engagement_analytics(s, e)
        out.append({"engagement_id": str(e.id), "name": e.name, "frameworks": e.frameworks, "status": e.status,
                    "score": a["readiness"]["score"], "rating": a["readiness"]["rating"], "mode": a["mode"],
                    "open_exceptions": a["kpis"]["open_exceptions"], "implementation_pct": a["kpis"]["implementation_pct"],
                    "trend": [t["implemented_pct"] for t in a["trend"]]})
    return sorted(out, key=lambda x: x["score"])


@router.get("/engagements/{eid}/controls.csv")
def controls_csv(eid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> Response:
    e = get_scoped(s, p, Engagement, eid, "engagement.read")
    a = engagement_analytics(s, e)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Control", "Title", "Domain", "Framework refs", "Effectiveness", "Workflow status", "Owner", "Risk",
                "Self-assessment", "Approved evidence", "Signed off", "Last test", "Open exceptions"])
    for r in a["controls"]:
        lt = r["last_test"]
        w.writerow([r["code"], _csv_safe(r["title"]), r["domain"], "; ".join(r["refs"]), r["status"], r["workflow_status"],
                    _csv_safe(r["owner"]), r["risk"], r["self_assessment"] or "", r["approved_evidence"], r["signed_off"],
                    f"{lt['date']} {lt['result']} ({lt['deviations']}/{lt['samples']})" if lt else "", r["open_exceptions"]])
    audit.record(s, "controls.exported", tenant_id=e.tenant_id, actor_id=p.id, target_type="engagement", target_id=e.id)
    s.commit()
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"content-disposition": 'attachment; filename="controls.csv"'})


# ------------------------------------------------------------------ tests & exceptions
class TestIn(BaseModel):
    engagement_control_id: uuid.UUID
    test_date: date
    samples_tested: int = Field(ge=0, le=100000)
    deviations: int = Field(ge=0, le=100000)
    result: str | None = Field(default=None, pattern="^(pass|pass_with_exception|fail)$")
    population: int | None = Field(default=None, ge=0)
    procedure: str = Field(default="", max_length=4000)
    notes: str = Field(default="", max_length=4000)


@router.post("/engagements/{eid}/tests", status_code=201)
def create_test(eid: uuid.UUID, body: TestIn, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    e = get_scoped(s, p, Engagement, eid, "control.assess")
    ec = s.get(EngagementControl, body.engagement_control_id)
    if ec is None or ec.engagement_id != e.id:
        raise HTTPException(status_code=422, detail="control not in this engagement")
    try:
        t, exc = assurance.record_test(s, ec, test_date=body.test_date, samples=body.samples_tested, deviations=body.deviations,
                                       result=body.result, population=body.population, procedure=body.procedure,
                                       notes=body.notes, tester_id=p.id, actor_id=p.id)
    except ValueError as ex:
        raise HTTPException(status_code=422, detail=str(ex)) from ex
    s.commit()
    return {"id": str(t.id), "result": t.result, "exception": exc.code if exc else None}


@router.get("/engagements/{eid}/tests")
def list_tests(eid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    e = get_scoped(s, p, Engagement, eid, "engagement.read")
    from ...models import Control
    rows = s.execute(select(ControlTest, Control.code, Control.title)
                     .join(EngagementControl, EngagementControl.id == ControlTest.engagement_control_id)
                     .join(Control, Control.id == EngagementControl.control_id)
                     .where(ControlTest.engagement_id == e.id).order_by(ControlTest.test_date.desc())).all()
    return [{"id": str(t.id), "control": code, "title": title, "test_date": t.test_date.isoformat(), "samples": t.samples_tested,
             "deviations": t.deviations, "population": t.population, "result": t.result, "procedure": t.procedure,
             "notes": t.notes} for t, code, title in rows]


@router.get("/engagements/{eid}/exceptions")
def list_exceptions(eid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    e = get_scoped(s, p, Engagement, eid, "remediation.read")
    today = date.today()
    return [{"id": str(x.id), "code": x.code, "description": x.description, "severity": x.severity, "status": x.status,
             "opened_on": x.opened_on.isoformat(), "target_date": x.target_date.isoformat() if x.target_date else None,
             "days_open": ((x.closed_on or today) - x.opened_on).days, "owner": x.owner_name, "audit_impact": x.audit_impact,
             "overdue": x.status == "open" and bool(x.target_date and x.target_date < today)}
            for x in s.execute(select(AuditException).where(AuditException.engagement_id == e.id)
                               .order_by(AuditException.opened_on.desc())).scalars()]


class ExcPatch(BaseModel):
    status: str = Field(pattern="^(open|remediated|closed|risk_accepted)$")
    comment: str = Field(default="", max_length=2000)
    target_date: date | None = None


@router.patch("/exceptions/{xid}")
def patch_exception(xid: uuid.UUID, body: ExcPatch, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    x = get_scoped(s, p, AuditException, xid, "remediation.update_progress")
    if body.status in ("closed", "risk_accepted"):
        p.require(x.tenant_id, "remediation.verify")
        if len(body.comment.strip()) < 5:
            raise HTTPException(status_code=422, detail="closing or accepting an exception needs a comment")
        if body.status == "closed" and x.remediation_task_id:
            task = s.get(RemediationTask, x.remediation_task_id)
            if task and task.status != "verified_closed":
                raise HTTPException(status_code=409, detail="verify the linked remediation task first (retest the control)")
        x.closed_on, x.closed_by = date.today(), p.id
    elif body.status == "open":
        x.closed_on = None
    if body.target_date:
        p.require(x.tenant_id, "remediation.manage")
        x.target_date = body.target_date
    x.status = body.status
    audit.record(s, f"exception.{body.status}", tenant_id=x.tenant_id, actor_id=p.id, target_type="exception", target_id=x.id,
                 comment=body.comment[:200])
    s.commit()
    return {"id": str(x.id), "status": x.status}


# ------------------------------------------------------------------ milestones
@router.get("/engagements/{eid}/milestones")
def list_milestones(eid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    e = get_scoped(s, p, Engagement, eid, "engagement.read")
    today = date.today()
    return [{"id": str(m.id), "title": m.title, "planned_date": m.planned_date.isoformat(),
             "completed_on": m.completed_on.isoformat() if m.completed_on else None,
             "status": "done" if m.completed_on else ("late" if m.planned_date < today else "planned")}
            for m in s.execute(select(Milestone).where(Milestone.engagement_id == e.id)
                               .order_by(Milestone.planned_date, Milestone.ordinal)).scalars()]


class MilestoneIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    planned_date: date


@router.post("/engagements/{eid}/milestones", status_code=201)
def add_milestone(eid: uuid.UUID, body: MilestoneIn, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    e = get_scoped(s, p, Engagement, eid, "engagement.manage")
    m = Milestone(tenant_id=e.tenant_id, engagement_id=e.id, title=body.title, planned_date=body.planned_date, ordinal=99)
    s.add(m)
    s.commit()
    return {"id": str(m.id)}


class MilestonePatch(BaseModel):
    completed: bool | None = None
    planned_date: date | None = None


@router.patch("/milestones/{mid}")
def patch_milestone(mid: uuid.UUID, body: MilestonePatch, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    m = get_scoped(s, p, Milestone, mid, "engagement.manage")
    if body.completed is not None:
        m.completed_on = date.today() if body.completed else None
    if body.planned_date:
        m.planned_date = body.planned_date
    audit.record(s, "milestone.updated", tenant_id=m.tenant_id, actor_id=p.id, target_type="milestone", target_id=m.id)
    s.commit()
    return {"id": str(m.id), "completed_on": m.completed_on.isoformat() if m.completed_on else None}


# ------------------------------------------------------------------ spreadsheet import
@router.post("/engagements/{eid}/import", dependencies=[Depends(limit("imports", 30))])
async def import_sheet(eid: uuid.UUID, kind: str = Query(pattern="^(assessment|tests)$"), apply: bool = False,
                       create_tasks: bool = True, file: UploadFile = File(...), p: Principal = Depends(principal),
                       s: Session = Depends(get_db)) -> dict:
    e = get_scoped(s, p, Engagement, eid, "engagement.read")
    p.require(e.tenant_id, "evidence.upload" if kind == "assessment" else "control.assess")
    if create_tasks and apply and kind == "assessment" and not p.can(e.tenant_id, "remediation.manage"):
        create_tasks = False  # clients record their self-assessment; Tensor staff decide on remediation
    data = await file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="spreadsheet larger than 10 MB")
    try:
        log = assurance.import_sheet(s, e, file.filename or "upload.csv", data, kind=kind, apply=apply,
                                     create_tasks=create_tasks, actor_id=p.id)
    except ValueError as ex:
        raise HTTPException(status_code=422, detail=str(ex)) from ex
    except Exception as ex:  # malformed workbook
        raise HTTPException(status_code=422, detail="could not read the spreadsheet") from ex
    s.commit()
    return log
