"""Engagements, engagement controls (assessments), evidence requests, sign-off and the client trust ledger."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import audit
from ...models import (
    Control,
    ControlMapping,
    Engagement,
    EngagementControl,
    EvidenceControlLink,
    EvidenceItem,
    EvidenceRequest,
    Framework,
    FrameworkRequirement,
    RemediationTask,
    Report,
    SignOff,
    User,
)
from ...security.authz import Principal, get_scoped
from ...services.review import create_signoff
from ...services.snapshot import controls_for
from ...services.state_machines import ENGAGEMENT, ENGAGEMENT_CONTROL, check
from ..deps import get_db, principal

router = APIRouter(tags=["engagements"])
FRAMEWORK_CODES = {"SOC2", "ISO27001", "ISO42001", "PCIDSS"}


def eng_out(e: Engagement, extra: dict | None = None) -> dict:
    return {"id": str(e.id), "tenant_id": str(e.tenant_id), "name": e.name, "service_type": e.service_type,
            "frameworks": e.frameworks, "scope": e.scope, "systems": e.systems, "status": e.status,
            "start_date": e.start_date.isoformat() if e.start_date else None,
            "target_date": e.target_date.isoformat() if e.target_date else None,
            "delivery_owner_id": str(e.delivery_owner_id) if e.delivery_owner_id else None,
            "signer_id": str(e.signer_id) if e.signer_id else None, "contract_value_usd": e.contract_value_usd,
            "version": e.version} | (extra or {})


class EngagementIn(BaseModel):
    tenant_id: uuid.UUID
    name: str = Field(min_length=2, max_length=200)
    service_type: str = Field(default="audit_ready_year", pattern="^(audit_ready_year|founding_client|stay_compliant|white_label|internal)$")
    frameworks: list[str] = Field(min_length=1, max_length=4)
    scope: str = Field(default="", max_length=5000)
    systems: list[str] = Field(default_factory=list, max_length=50)
    start_date: date | None = None
    target_date: date | None = None
    signer_id: uuid.UUID | None = None
    contract_value_usd: float = Field(default=0, ge=0)


def create_engagement(s: Session, p: Principal, body: EngagementIn) -> Engagement:
    p.require(body.tenant_id, "engagement.manage")
    bad = set(body.frameworks) - FRAMEWORK_CODES
    if bad:
        raise HTTPException(status_code=422, detail=f"unknown frameworks: {sorted(bad)}")
    if body.signer_id:
        signer = s.get(User, body.signer_id)
        if not signer or not signer.can_sign or not signer.is_staff or not p.roles.get(body.tenant_id):
            raise HTTPException(status_code=422, detail="signer must be an authorised Tensor signer")
    start = body.start_date or date.today()
    e = Engagement(tenant_id=body.tenant_id, name=body.name, service_type=body.service_type,
                   frameworks=body.frameworks, scope=body.scope, systems=body.systems, start_date=start,
                   target_date=body.target_date or start + timedelta(weeks=8), delivery_owner_id=p.id,
                   signer_id=body.signer_id, contract_value_usd=body.contract_value_usd)
    s.add(e)
    s.flush()
    ctls = controls_for(s, body.frameworks)
    weeks = {"Governance": 1, "Risk": 1, "People": 2, "Access": 2, "Assets": 3, "Change": 3, "Infrastructure": 4,
             "Data": 4, "Operations": 5, "Payments": 5, "Resilience": 6, "Vendors": 6, "AI governance": 6}
    for c in ctls:
        ec = EngagementControl(tenant_id=e.tenant_id, engagement_id=e.id, control_id=c.id)
        s.add(ec)
        s.flush()
        s.add(EvidenceRequest(
            tenant_id=e.tenant_id, engagement_id=e.id, engagement_control_id=ec.id,
            title=f"{c.code}: {c.title}"[:300],
            instructions="Please provide: " + "; ".join(c.evidence_examples) + f". Guidance: {c.guidance}",
            due_date=start + timedelta(weeks=weeks.get(c.domain, 6)), status="open"))
    from ...services.assurance import create_default_milestones
    create_default_milestones(s, e)
    audit.record(s, "engagement.created", tenant_id=e.tenant_id, actor_id=p.id, target_type="engagement",
                 target_id=e.id, frameworks=body.frameworks, controls=len(ctls))
    return e


@router.get("/engagements")
def list_engagements(tenant_id: uuid.UUID | None = None, p: Principal = Depends(principal),
                     s: Session = Depends(get_db)) -> list[dict]:
    scope = p.tenants_with("engagement.read")
    if tenant_id:
        p.require(tenant_id, "engagement.read")
        scope = {tenant_id}
    if not scope:
        return []
    engs = s.execute(select(Engagement).where(Engagement.tenant_id.in_(scope)).order_by(Engagement.created_at.desc())).scalars().all()
    counts = dict(s.execute(select(EngagementControl.engagement_id, func.count())
                            .where(EngagementControl.engagement_id.in_([e.id for e in engs]),
                                   EngagementControl.status.in_(["evidence_collected", "ready_for_signoff", "signed_off"]))
                            .group_by(EngagementControl.engagement_id)).all()) if engs else {}
    totals = dict(s.execute(select(EngagementControl.engagement_id, func.count())
                            .where(EngagementControl.engagement_id.in_([e.id for e in engs]),
                                   EngagementControl.status != "not_applicable")
                            .group_by(EngagementControl.engagement_id)).all()) if engs else {}
    return [eng_out(e, {"controls_total": totals.get(e.id, 0), "controls_evidenced": counts.get(e.id, 0)}) for e in engs]


@router.post("/engagements", status_code=201)
def post_engagement(body: EngagementIn, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    e = create_engagement(s, p, body)
    s.commit()
    return eng_out(e)


@router.get("/engagements/{eid}")
def get_engagement(eid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    e = get_scoped(s, p, Engagement, eid, "engagement.read")
    status_counts = dict(s.execute(select(EngagementControl.status, func.count())
                                   .where(EngagementControl.engagement_id == e.id)
                                   .group_by(EngagementControl.status)).all())
    ev_counts = dict(s.execute(select(EvidenceItem.status, func.count()).where(EvidenceItem.engagement_id == e.id)
                               .group_by(EvidenceItem.status)).all())
    if not p.can(e.tenant_id, "evidence.read"):  # auditors: approved evidence only, no pipeline counts
        ev_counts = {"approved": ev_counts.get("approved", 0)}
    open_req = s.execute(select(func.count()).select_from(EvidenceRequest).where(
        EvidenceRequest.engagement_id == e.id, EvidenceRequest.status == "open")).scalar_one()
    signer = s.get(User, e.signer_id) if e.signer_id else None
    return eng_out(e, {"control_status": status_counts, "evidence_status": ev_counts, "open_requests": open_req,
                       "signer": {"name": signer.name, "title": signer.expert_title} if signer else None,
                       "my_role": p.role_in(e.tenant_id)})


class EngagementPatch(BaseModel):
    version: int
    status: str | None = None
    signer_id: uuid.UUID | None = None
    target_date: date | None = None
    scope: str | None = Field(default=None, max_length=5000)
    contract_value_usd: float | None = Field(default=None, ge=0)


@router.patch("/engagements/{eid}")
def patch_engagement(eid: uuid.UUID, body: EngagementPatch, p: Principal = Depends(principal),
                     s: Session = Depends(get_db)) -> dict:
    e = get_scoped(s, p, Engagement, eid, "engagement.manage")
    if e.version != body.version:
        raise HTTPException(status_code=409, detail="engagement changed; reload")
    if body.status and body.status != e.status:
        check(ENGAGEMENT, e.status, body.status, "engagement")
        if body.status == "audit_ready":
            pending = s.execute(select(func.count()).select_from(EngagementControl).where(
                EngagementControl.engagement_id == e.id,
                EngagementControl.status.notin_(["signed_off", "not_applicable"]))).scalar_one()
            if pending:
                raise HTTPException(status_code=409, detail=f"{pending} controls are not signed off yet")
        e.status = body.status
    if body.signer_id:
        u = s.get(User, body.signer_id)
        if not u or not u.can_sign or not u.is_staff:
            raise HTTPException(status_code=422, detail="signer must be an authorised Tensor signer")
        e.signer_id = u.id
    for f in ("target_date", "scope", "contract_value_usd"):
        v = getattr(body, f)
        if v is not None:
            setattr(e, f, v)
    e.version += 1
    audit.record(s, "engagement.updated", tenant_id=e.tenant_id, actor_id=p.id, target_type="engagement",
                 target_id=e.id, fields=[k for k, v in body.model_dump().items() if v is not None and k != "version"])
    s.commit()
    return eng_out(e)


@router.get("/engagements/{eid}/controls")
def engagement_controls(eid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    e = get_scoped(s, p, Engagement, eid, "engagement.read")
    rows = s.execute(select(EngagementControl, Control).join(Control, Control.id == EngagementControl.control_id)
                     .where(EngagementControl.engagement_id == e.id).order_by(Control.code)).all()
    refs: dict[uuid.UUID, list[str]] = {}
    for cid, fw, ref in s.execute(select(ControlMapping.control_id, Framework.code, FrameworkRequirement.ref)
                                  .join(FrameworkRequirement, FrameworkRequirement.id == ControlMapping.requirement_id)
                                  .join(Framework, Framework.id == FrameworkRequirement.framework_id)
                                  .where(Framework.code.in_(e.frameworks))).all():
        refs.setdefault(cid, []).append(f"{fw} {ref}")
    approved = dict(s.execute(select(EvidenceControlLink.engagement_control_id, func.count())
                              .join(EvidenceItem, EvidenceItem.id == EvidenceControlLink.evidence_id)
                              .where(EvidenceItem.engagement_id == e.id, EvidenceItem.status == "approved",
                                     EvidenceControlLink.status == "approved")
                              .group_by(EvidenceControlLink.engagement_control_id)).all())
    proposed = dict(s.execute(select(EvidenceControlLink.engagement_control_id, func.count())
                              .where(EvidenceControlLink.status == "proposed",
                                     EvidenceControlLink.engagement_control_id.in_([ec.id for ec, _ in rows]))
                              .group_by(EvidenceControlLink.engagement_control_id)).all()) if rows else {}
    sos = {so.subject_id: so for so in s.execute(select(SignOff).where(
        SignOff.engagement_id == e.id, SignOff.subject_type == "engagement_control")).scalars()}
    return [{"id": str(ec.id), "control_id": str(c.id), "code": c.code, "domain": c.domain, "title": c.title,
             "objective": c.objective, "guidance": c.guidance, "evidence_examples": c.evidence_examples,
             "status": ec.status, "applicability_rationale": ec.applicability_rationale, "version": ec.version,
             "framework_refs": sorted(refs.get(c.id, [])), "approved_evidence": approved.get(ec.id, 0),
             "proposed_links": proposed.get(ec.id, 0),
             "signoff": {"signer": sos[ec.id].signer_name, "title": sos[ec.id].signer_title,
                         "signed_at": sos[ec.id].signed_at.isoformat()} if ec.id in sos else None}
            for ec, c in rows]


class ECPatch(BaseModel):
    version: int
    status: str | None = None
    applicability_rationale: str | None = Field(default=None, max_length=3000)
    reviewer_notes: str | None = Field(default=None, max_length=5000)
    owner_name: str | None = Field(default=None, max_length=200)
    risk_rating: str | None = Field(default=None, pattern="^(high|medium|low)$")


@router.patch("/engagement-controls/{ecid}")
def patch_ec(ecid: uuid.UUID, body: ECPatch, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    ec = get_scoped(s, p, EngagementControl, ecid, "control.assess")
    if ec.version != body.version:
        raise HTTPException(status_code=409, detail="control changed; reload")
    if body.status and body.status != ec.status:
        if body.status == "signed_off":
            raise HTTPException(status_code=409, detail="use the sign-off endpoint")
        check(ENGAGEMENT_CONTROL, ec.status, body.status, "control")
        if body.status == "not_applicable" and len((body.applicability_rationale or ec.applicability_rationale).strip()) < 10:
            raise HTTPException(status_code=422, detail="not applicable needs a written rationale")
        if body.status == "ready_for_signoff":
            n = s.execute(select(func.count()).select_from(EvidenceControlLink)
                          .join(EvidenceItem, EvidenceItem.id == EvidenceControlLink.evidence_id)
                          .where(EvidenceControlLink.engagement_control_id == ec.id,
                                 EvidenceControlLink.status == "approved", EvidenceItem.status == "approved")).scalar_one()
            if not n:
                raise HTTPException(status_code=409, detail="no approved evidence linked to this control")
        prev = ec.status
        ec.status = body.status
        audit.record(s, "control.status_changed", tenant_id=ec.tenant_id, actor_id=p.id,
                     target_type="engagement_control", target_id=ec.id, frm=prev, to=body.status)
    if body.applicability_rationale is not None:
        ec.applicability_rationale = body.applicability_rationale
    if body.reviewer_notes is not None:
        ec.reviewer_notes = body.reviewer_notes
    if body.owner_name is not None:
        ec.owner_name = body.owner_name
    if body.risk_rating is not None:
        ec.risk_rating = body.risk_rating
    ec.version += 1
    s.commit()
    return {"id": str(ec.id), "status": ec.status, "version": ec.version}


class SignIn(BaseModel):
    statement: str = Field(default="I have reviewed the approved evidence for this control and confirm it is ready "
                                    "for independent audit.", min_length=20, max_length=2000)


@router.post("/engagement-controls/{ecid}/signoff", status_code=201)
def signoff_control(ecid: uuid.UUID, body: SignIn, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    ec = get_scoped(s, p, EngagementControl, ecid, "signoff.create")
    check(ENGAGEMENT_CONTROL, ec.status, "signed_off", "control")
    e = s.get(Engagement, ec.engagement_id)
    assert e is not None
    if e.signer_id and e.signer_id != p.id and p.role_in(e.tenant_id) != "quality_reviewer":
        raise HTTPException(status_code=403, detail="only the engagement's named signer can sign off controls")
    evidence = s.execute(select(EvidenceItem.id, EvidenceItem.sha256)
                         .join(EvidenceControlLink, EvidenceControlLink.evidence_id == EvidenceItem.id)
                         .where(EvidenceControlLink.engagement_control_id == ec.id,
                                EvidenceControlLink.status == "approved", EvidenceItem.status == "approved")
                         .order_by(EvidenceItem.id)).all()
    so = create_signoff(s, tenant_id=ec.tenant_id, engagement_id=ec.engagement_id, subject_type="engagement_control",
                        subject_id=ec.id, signer=p.user, statement=body.statement,
                        content={"engagement_control": str(ec.id), "evidence": [[str(i), h] for i, h in evidence]})
    ec.status = "signed_off"
    ec.version += 1
    s.commit()
    return {"signoff_id": str(so.id), "content_sha256": so.content_sha256, "signer": so.signer_name}


@router.get("/engagements/{eid}/requests")
def requests(eid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    e = get_scoped(s, p, Engagement, eid, "request.read")
    rows = s.execute(select(EvidenceRequest).where(EvidenceRequest.engagement_id == e.id)
                     .order_by(EvidenceRequest.due_date, EvidenceRequest.title)).scalars()
    return [{"id": str(r.id), "title": r.title, "instructions": r.instructions, "status": r.status,
             "due_date": r.due_date.isoformat() if r.due_date else None,
             "engagement_control_id": str(r.engagement_control_id) if r.engagement_control_id else None,
             "assignee_user_id": str(r.assignee_user_id) if r.assignee_user_id else None} for r in rows]


class RequestIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    instructions: str = Field(default="", max_length=5000)
    due_date: date | None = None
    engagement_control_id: uuid.UUID | None = None
    assignee_user_id: uuid.UUID | None = None


@router.post("/engagements/{eid}/evidence-requests", status_code=201)
def create_request(eid: uuid.UUID, body: RequestIn, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    e = get_scoped(s, p, Engagement, eid, "request.manage")
    if body.engagement_control_id:
        ec = s.get(EngagementControl, body.engagement_control_id)
        if not ec or ec.engagement_id != e.id:
            raise HTTPException(status_code=422, detail="control not in this engagement")
    r = EvidenceRequest(tenant_id=e.tenant_id, engagement_id=e.id, **body.model_dump())
    s.add(r)
    s.flush()
    audit.record(s, "request.created", tenant_id=e.tenant_id, actor_id=p.id, target_type="evidence_request", target_id=r.id)
    s.commit()
    return {"id": str(r.id)}


class RequestPatch(BaseModel):
    status: str = Field(pattern="^(open|submitted|cancelled)$")


@router.patch("/evidence-requests/{rid}")
def patch_request(rid: uuid.UUID, body: RequestPatch, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    r = get_scoped(s, p, EvidenceRequest, rid, "request.read")
    if body.status == "cancelled":
        p.require(r.tenant_id, "request.manage")
    elif not (p.can(r.tenant_id, "evidence.upload")):
        raise HTTPException(status_code=403, detail="Forbidden")
    r.status = body.status
    s.commit()
    return {"id": str(r.id), "status": r.status}


@router.get("/engagements/{eid}/trust-ledger")
def trust_ledger(eid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    """Client- and auditor-visible record of who signed what, over which content hash."""
    e = get_scoped(s, p, Engagement, eid, "signoff.read")
    sos = s.execute(select(SignOff).where(SignOff.engagement_id == e.id).order_by(SignOff.signed_at.desc())).scalars().all()
    reports = s.execute(select(Report).where(Report.engagement_id == e.id, Report.status == "released")).scalars().all()
    approved = s.execute(select(func.count()).select_from(EvidenceItem).where(
        EvidenceItem.engagement_id == e.id, EvidenceItem.status == "approved")).scalar_one()
    signer = s.get(User, e.signer_id) if e.signer_id else None
    return {"engagement": e.name, "named_signer": {"name": signer.name, "title": signer.expert_title} if signer else None,
            "approved_evidence_items": approved,
            "signoffs": [{"subject_type": x.subject_type, "subject_id": str(x.subject_id), "signer": x.signer_name,
                          "title": x.signer_title, "statement": x.statement, "content_sha256": x.content_sha256,
                          "signed_at": x.signed_at.isoformat()} for x in sos],
            "released_reports": [{"id": str(r.id), "kind": r.kind, "sha256": r.sha256,
                                  "released_at": r.released_at.isoformat() if r.released_at else None} for r in reports],
            "statement": "Tensor prepares and a named expert signs readiness work. An independent audit firm issues the report."}


@router.get("/engagements/{eid}/remediation")
def remediation(eid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    e = get_scoped(s, p, Engagement, eid, "remediation.read")
    return [{"id": str(t.id), "title": t.title, "description": t.description, "status": t.status, "severity": t.severity,
             "due_date": t.due_date.isoformat() if t.due_date else None, "version": t.version,
             "engagement_control_id": str(t.engagement_control_id) if t.engagement_control_id else None,
             "owner_user_id": str(t.owner_user_id) if t.owner_user_id else None,
             "verified_by": str(t.verified_by) if t.verified_by else None}
            for t in s.execute(select(RemediationTask).where(RemediationTask.engagement_id == e.id)
                               .order_by(RemediationTask.created_at)).scalars()]
