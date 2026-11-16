"""Expert review queue for AI proposals. Review actions are human-only (authenticated users, never workers)."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import AIArtifact, AIRun, Engagement, EvidenceItem, QuestionnaireItem, Tenant
from ...security.authz import Principal, get_scoped
from ...services.review import apply_review
from ..deps import get_db, principal

router = APIRouter(tags=["ai review"])


@router.get("/review-queue")
def queue(task_type: str | None = None, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    scope = p.tenants_with("ai.review")
    if not scope:
        return []
    q = select(AIArtifact, Engagement, Tenant).join(Engagement, Engagement.id == AIArtifact.engagement_id) \
        .join(Tenant, Tenant.id == AIArtifact.tenant_id) \
        .where(AIArtifact.tenant_id.in_(scope), AIArtifact.review_state == "proposed")
    if task_type:
        q = q.where(AIArtifact.task_type == task_type)
    rows = s.execute(q.order_by(AIArtifact.confidence.asc().nullsfirst(), AIArtifact.created_at)).all()
    titles: dict[uuid.UUID, str] = {}
    ev_ids = [a.subject_id for a, _, _ in rows if a.subject_type == "evidence"]
    if ev_ids:
        titles |= {e.id: e.title for e in s.execute(select(EvidenceItem).where(EvidenceItem.id.in_(ev_ids))).scalars()}
    qi_ids = [a.subject_id for a, _, _ in rows if a.subject_type == "questionnaire_item"]
    if qi_ids:
        titles |= {q.id: q.question[:140] for q in s.execute(select(QuestionnaireItem).where(QuestionnaireItem.id.in_(qi_ids))).scalars()}
    return [{"id": str(a.id), "task_type": a.task_type, "subject_type": a.subject_type, "subject_id": str(a.subject_id),
             "subject_title": titles.get(a.subject_id, ""), "confidence": a.confidence, "decision": a.decision,
             "warnings": a.validation_warnings, "engagement": e.name, "engagement_id": str(e.id), "client": t.name,
             "created_at": a.created_at.isoformat()} for a, e, t in rows]


@router.get("/ai-artifacts/{aid}")
def artifact(aid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    a = get_scoped(s, p, AIArtifact, aid, "ai.review")
    run = s.get(AIRun, a.ai_run_id) if a.ai_run_id else None
    return {"id": str(a.id), "task_type": a.task_type, "subject_type": a.subject_type, "subject_id": str(a.subject_id),
            "output": a.output, "final_output": a.final_output, "review_state": a.review_state, "version": a.version,
            "confidence": a.confidence, "decision": a.decision, "warnings": a.validation_warnings,
            "run": {"provider": run.provider, "model": run.model, "prompt_version": run.prompt_version,
                    "schema_version": run.schema_version, "input_tokens": run.input_tokens,
                    "output_tokens": run.output_tokens, "cost_usd": run.cost_usd, "latency_ms": run.latency_ms,
                    "request_id": run.request_id, "created_at": run.created_at.isoformat()} if run else None}


class ReviewIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject|edit)$")
    comment: str = Field(default="", max_length=4000)
    edited_output: dict[str, Any] | None = None
    expected_version: int
    review_seconds: int | None = Field(default=None, ge=0, le=86400)


@router.post("/ai-artifacts/{aid}/review")
def review(aid: uuid.UUID, body: ReviewIn, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    a = get_scoped(s, p, AIArtifact, aid, "ai.review")
    if not p.user.is_staff:
        raise HTTPException(status_code=403, detail="Forbidden")
    a = apply_review(s, a, p.user, body.decision, body.comment, body.edited_output, body.expected_version,
                     body.review_seconds)
    s.commit()
    return {"id": str(a.id), "review_state": a.review_state, "version": a.version}
