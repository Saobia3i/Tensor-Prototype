"""Human review of AI artifacts and expert sign-off. Only authenticated humans reach these functions."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..ai.validation import InvalidOutput, validate_evidence_review, validate_questionnaire
from ..models import (
    AIArtifact,
    Control,
    EngagementControl,
    EvidenceChunk,
    EvidenceControlLink,
    EvidenceItem,
    EvidenceRequest,
    QuestionnaireItem,
    ReviewDecision,
    SignOff,
    User,
)
from .evidence import set_status


def _chunk_text_for(s: Session, art: AIArtifact) -> dict[str, str]:
    ids = [uuid.UUID(c["chunk_uuid"]) for c in art.output.get("citations", [])]
    handles = {c["chunk_uuid"]: c["chunk_id"] for c in art.output.get("citations", [])}
    rows = s.execute(select(EvidenceChunk).where(EvidenceChunk.id.in_(ids))).scalars() if ids else []
    return {handles[str(r.id)]: r.text for r in rows}


def _all_chunk_handles(s: Session, art: AIArtifact) -> dict[str, str]:
    """For edits on evidence reviews: allow citing any chunk of the same evidence (handles E1..En by ordinal)."""
    rows = s.execute(select(EvidenceChunk).where(EvidenceChunk.evidence_id == art.subject_id)
                     .order_by(EvidenceChunk.ordinal)).scalars().all()
    return {f"E{i + 1}": r.text for i, r in enumerate(rows)}


def apply_review(s: Session, art: AIArtifact, reviewer: User, decision: str, comment: str,
                 edited_output: dict[str, Any] | None, expected_version: int, review_seconds: int | None) -> AIArtifact:
    if art.version != expected_version:
        raise HTTPException(status_code=409, detail="artifact changed since you loaded it; reload and retry")
    if art.review_state != "proposed":
        raise HTTPException(status_code=409, detail=f"artifact already {art.review_state}")
    if decision not in ("approve", "reject", "edit"):
        raise HTTPException(status_code=422, detail="decision must be approve, reject or edit")
    if decision == "reject" and len(comment.strip()) < 5:
        raise HTTPException(status_code=422, detail="a rejection needs a comment explaining why")
    final = art.output
    if decision == "edit":
        if not edited_output:
            raise HTTPException(status_code=422, detail="edited_output is required for edit")
        try:
            if art.task_type == "evidence_review":
                codes = {c.code for _, c in s.execute(
                    select(EngagementControl, Control).join(Control, Control.id == EngagementControl.control_id)
                    .where(EngagementControl.engagement_id == art.engagement_id)).all()}
                cleaned = {k: v for k, v in edited_output.items() if k in art.output}
                cleaned["citations"] = [{"chunk_id": c["chunk_id"], "quote": c["quote"]} for c in cleaned.get("citations", [])]
                v = validate_evidence_review(cleaned, _all_chunk_handles(s, art), codes)
            else:
                cleaned = {k: v for k, v in edited_output.items() if k in art.output}
                cleaned["citations"] = [{"chunk_id": c["chunk_id"], "quote": c["quote"]} for c in cleaned.get("citations", [])]
                v = validate_questionnaire(cleaned, _chunk_text_for(s, art))
        except InvalidOutput as e:
            raise HTTPException(status_code=422, detail=f"edited output invalid: {e}") from e
        final = v.data
    s.add(ReviewDecision(tenant_id=art.tenant_id, artifact_id=art.id, reviewer_id=reviewer.id, decision=decision,
                         comment=comment, edited_output=final if decision == "edit" else None,
                         review_seconds=review_seconds))
    art.version += 1
    if decision == "reject":
        art.review_state = "rejected"
    else:
        art.review_state = "edited" if decision == "edit" else "approved"
        art.final_output = final  # original model output stays untouched in art.output
    if art.task_type == "evidence_review":
        _apply_evidence_review(s, art, reviewer, decision, final)
    elif art.task_type == "questionnaire_answer":
        item = s.get(QuestionnaireItem, art.subject_id)
        if item:
            if decision == "reject":
                item.status, item.answer = "needs_info", None
            else:
                item.answer = final["answer"]
                item.status = "needs_info" if final["answer_type"] == "insufficient_evidence" else "approved"
    audit.record(s, f"review.{decision}", tenant_id=art.tenant_id, actor_id=reviewer.id, target_type="ai_artifact",
                 target_id=art.id, task_type=art.task_type)
    return art


def _apply_evidence_review(s: Session, art: AIArtifact, reviewer: User, decision: str, final: dict[str, Any]) -> None:
    ev = s.get(EvidenceItem, art.subject_id)
    if ev is None:
        return
    links = s.execute(select(EvidenceControlLink).where(EvidenceControlLink.evidence_id == ev.id)).scalars().all()
    if decision == "reject":
        for ln in links:
            if ln.status == "proposed":
                ln.status, ln.decided_by = "rejected", reviewer.id
        if ev.status in ("proposed", "extracted"):
            set_status(ev, "expert_review")
        return
    ecs = {c.code: ec for ec, c in s.execute(
        select(EngagementControl, Control).join(Control, Control.id == EngagementControl.control_id)
        .where(EngagementControl.engagement_id == ev.engagement_id)).all()}
    chosen = {ecs[c["control_code"]].id: c["rationale"] for c in final["candidate_controls"] if c["control_code"] in ecs}
    by_ec = {ln.engagement_control_id: ln for ln in links}
    for ec_id, why in chosen.items():
        ln = by_ec.get(ec_id)
        if ln is None:
            ln = EvidenceControlLink(tenant_id=ev.tenant_id, evidence_id=ev.id, engagement_control_id=ec_id,
                                     source_artifact_id=art.id)
            s.add(ln)
        ln.status, ln.rationale, ln.decided_by = "approved", why, reviewer.id
    for ln in links:
        if ln.engagement_control_id not in chosen and ln.status == "proposed":
            ln.status, ln.decided_by = "rejected", reviewer.id
    if final.get("period_start"):
        ev.period_start = date.fromisoformat(final["period_start"])
    if final.get("period_end"):
        ev.period_end = date.fromisoformat(final["period_end"])
        ev.expires_at = ev.period_end + timedelta(days=365)
    if final["decision"] == "insufficient_evidence" or not chosen:
        if ev.status in ("proposed", "expert_review"):
            set_status(ev, "needs_information")
        gaps = "; ".join(final.get("gaps", []))[:1500] or "Evidence did not support the requested control."
        s.add(EvidenceRequest(tenant_id=ev.tenant_id, engagement_id=ev.engagement_id,
                              title=f"More information needed: {ev.title}"[:300], instructions=gaps,
                              status="open", due_date=date.today() + timedelta(days=5)))
        return
    if ev.status == "needs_information":
        set_status(ev, "expert_review")
    if ev.status != "approved":
        set_status(ev, "approved")
    for old in s.execute(select(EvidenceItem).where(EvidenceItem.lineage_id == ev.lineage_id,
                                                    EvidenceItem.id != ev.id,
                                                    EvidenceItem.status == "approved")).scalars():
        set_status(old, "superseded")
    for ec_id in chosen:
        ec = s.get(EngagementControl, ec_id)
        if ec and ec.status in ("not_started", "in_progress", "gap"):
            ec.status = "evidence_collected"  # system-driven progress; sign-off remains a separate human act
    if ev.request_id:
        req = s.get(EvidenceRequest, ev.request_id)
        if req and req.status in ("open", "submitted"):
            req.status = "accepted"


def content_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def create_signoff(s: Session, *, tenant_id: uuid.UUID, engagement_id: uuid.UUID | None, subject_type: str,
                   subject_id: uuid.UUID, signer: User, statement: str, content: Any) -> SignOff:
    if not signer.can_sign or not signer.is_staff:
        raise HTTPException(status_code=403, detail="user is not an authorised signer")
    so = SignOff(tenant_id=tenant_id, engagement_id=engagement_id, subject_type=subject_type, subject_id=subject_id,
                 signer_id=signer.id, signer_name=signer.name, signer_title=signer.expert_title or "Tensor expert",
                 statement=statement, content_sha256=content_hash(content))
    s.add(so)
    s.flush()
    audit.record(s, "signoff.created", tenant_id=tenant_id, actor_id=signer.id, target_type=subject_type,
                 target_id=subject_id, signoff_id=str(so.id), sha256=so.content_sha256)
    return so
