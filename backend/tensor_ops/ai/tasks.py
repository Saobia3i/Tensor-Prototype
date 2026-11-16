"""AI task orchestration: authorized retrieval -> minimal context -> structured generation -> validation -> proposal.

Nothing here approves anything. Outputs are stored as `proposed` and wait for a named human reviewer.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..logging import redact_secrets
from ..models import (
    AIArtifact,
    AIRun,
    Control,
    EngagementControl,
    EvidenceChunk,
    EvidenceControlLink,
    EvidenceItem,
    Questionnaire,
    QuestionnaireItem,
)
from . import prompts, schemas
from .provider import AIResult, ProviderError, Request, get_provider
from .validation import InvalidOutput, Validated, validate_evidence_review, validate_questionnaire

MAX_CONTEXT_CHARS = 48_000


class OverBudget(RuntimeError):
    pass


def _spent_today(s: Session, tenant_id: uuid.UUID) -> float:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return float(s.execute(select(func.coalesce(func.sum(AIRun.cost_usd), 0.0)).where(
        AIRun.tenant_id == tenant_id, AIRun.created_at >= start)).scalar_one())


def _check_budget(s: Session, tenant_id: uuid.UUID, req: Request) -> None:
    st = get_settings()
    est = (len(req.system) + len(req.user)) / 4 / 1e6 * st.ai_price_in_per_mtok + \
        req.max_output_tokens / 1e6 * st.ai_price_out_per_mtok
    if est > st.ai_job_budget_usd:
        raise OverBudget(f"estimated ${est:.3f} exceeds per-job budget ${st.ai_job_budget_usd:.2f}")
    if _spent_today(s, tenant_id) + est > st.ai_tenant_daily_budget_usd:
        raise OverBudget("tenant daily AI budget exhausted")


def _run(s: Session, tenant_id: uuid.UUID, engagement_id: uuid.UUID | None, job_id: uuid.UUID | None, req: Request, prompt_v: str, schema_v: str,
         input_refs: list[str], validate: Callable[[dict[str, Any]], Validated]) -> tuple[AIRun, Validated | None]:
    """Generate with one bounded retry on invalid output. Every attempt is recorded."""
    provider = get_provider()
    _check_budget(s, tenant_id, req)
    last_err = ""
    for attempt in range(2):
        run = AIRun(tenant_id=tenant_id, engagement_id=engagement_id, job_id=job_id, task_type=req.task_type, provider=provider.name,
                    model=provider.model, prompt_version=prompt_v, schema_version=schema_v, input_refs=input_refs,
                    status="failed")
        s.add(run)
        try:
            res: AIResult = provider.generate_structured(req)
        except ProviderError as e:
            run.error = str(e)
            s.flush()
            raise
        run.model, run.request_id = res.model, res.request_id
        run.input_tokens, run.output_tokens, run.latency_ms = res.input_tokens, res.output_tokens, res.latency_ms
        run.cost_usd = res.cost(get_settings())
        try:
            v = validate(res.data)
            run.status = "succeeded"
            s.flush()
            return run, v
        except InvalidOutput as e:
            run.status, run.error = "invalid_output", str(e)[:500]
            last_err = str(e)
            s.flush()
            if attempt == 0:
                req = Request(req.task_type, req.system,
                              req.user + f"\n\nYour previous output was rejected by validation: {last_err}. "
                                         "Fix it and use only provided ids and verbatim quotes.",
                              req.schema, req.context, req.max_output_tokens)
    return run, None


def _redact(text_: str) -> str:
    return redact_secrets(text_)


def review_evidence(s: Session, evidence_id: uuid.UUID, job_id: uuid.UUID | None = None) -> dict[str, Any]:
    ev = s.get(EvidenceItem, evidence_id)
    if ev is None:
        return {"status": "not_found"}  # out of scope for this tenant session (RLS) or deleted
    if ev.status not in ("extracted", "proposed", "expert_review", "needs_information"):
        return {"status": "skipped", "reason": f"evidence status {ev.status}"}
    # Retrieval is filtered by tenant (RLS + explicit) and evidence id BEFORE anything reaches the model.
    chunks = s.execute(select(EvidenceChunk).where(EvidenceChunk.evidence_id == ev.id,
                                                   EvidenceChunk.tenant_id == ev.tenant_id)
                       .order_by(EvidenceChunk.ordinal)).scalars().all()
    ctx_chunks, total = [], 0
    for c in chunks:
        if total + len(c.text) > MAX_CONTEXT_CHARS:
            break
        ctx_chunks.append({"handle": f"E{len(ctx_chunks) + 1}", "id": str(c.id), "page": c.page, "text": _redact(c.text)})
        total += len(c.text)
    rows = s.execute(select(EngagementControl, Control).join(Control, Control.id == EngagementControl.control_id)
                     .where(EngagementControl.engagement_id == ev.engagement_id,
                            EngagementControl.status != "not_applicable")).all()
    controls = [{"code": c.code, "title": c.title, "objective": c.objective, "evidence_examples": c.evidence_examples,
                 "keywords": c.keywords, "ec_id": str(ec.id)} for ec, c in rows]
    if not ctx_chunks:
        ev.status = "expert_review"
        audit.record(s, "ai.evidence_review.no_text", tenant_id=ev.tenant_id, actor_id=None, actor_type="worker",
                     target_type="evidence", target_id=ev.id)
        return {"status": "no_text", "detail": "No extractable text; routed to expert review"}
    today = date.today()
    req = Request("evidence_review", prompts.EVIDENCE_REVIEW_SYSTEM,
                  prompts.evidence_review_user(today.isoformat(), ev.title, controls, ctx_chunks),
                  schemas.EVIDENCE_REVIEW_SCHEMA, {"chunks": ctx_chunks, "controls": controls},
                  get_settings().ai_max_output_tokens)
    chunk_text = {c["handle"]: c["text"] for c in ctx_chunks}
    allowed = {c["code"] for c in controls}
    try:
        run, v = _run(s, ev.tenant_id, ev.engagement_id, job_id, req, prompts.EVIDENCE_REVIEW_PROMPT_V, schemas.EVIDENCE_REVIEW_V,
                      [str(ev.id)] + [c["id"] for c in ctx_chunks],
                      lambda d: validate_evidence_review(d, chunk_text, allowed, today))
    except OverBudget as e:
        audit.record(s, "ai.evidence_review.over_budget", tenant_id=ev.tenant_id, actor_id=None, actor_type="worker",
                     target_type="evidence", target_id=ev.id, outcome="denied", reason=str(e))
        ev.status = "expert_review"
        return {"status": "over_budget", "detail": str(e)}
    if v is None:
        ev.status = "expert_review"
        audit.record(s, "ai.evidence_review.invalid_output", tenant_id=ev.tenant_id, actor_id=None, actor_type="worker",
                     target_type="evidence", target_id=ev.id, outcome="failure", ai_run_id=str(run.id))
        return {"status": "invalid_output", "error": run.error}
    handle_to_chunk = {c["handle"]: c["id"] for c in ctx_chunks}
    out = dict(v.data)
    out["citations"] = [dict(c, chunk_uuid=handle_to_chunk[c["chunk_id"]]) for c in out["citations"]]
    # Supersede earlier unreviewed proposals for this evidence.
    for old in s.execute(select(AIArtifact).where(AIArtifact.subject_id == ev.id,
                                                  AIArtifact.review_state == "proposed")).scalars():
        old.review_state = "superseded"
    art = AIArtifact(tenant_id=ev.tenant_id, engagement_id=ev.engagement_id, ai_run_id=run.id,
                     task_type="evidence_review", subject_type="evidence", subject_id=ev.id, output=out,
                     confidence=out["confidence"], decision=out["decision"], validation_warnings=v.warnings)
    s.add(art)
    s.flush()
    ec_by_code = {c["code"]: uuid.UUID(c["ec_id"]) for c in controls}
    for cc in out["candidate_controls"]:
        ec_id = ec_by_code[cc["control_code"]]
        link = s.execute(select(EvidenceControlLink).where(EvidenceControlLink.evidence_id == ev.id,
                                                           EvidenceControlLink.engagement_control_id == ec_id)
                         ).scalar_one_or_none()
        if link is None:
            s.add(EvidenceControlLink(tenant_id=ev.tenant_id, evidence_id=ev.id, engagement_control_id=ec_id,
                                      status="proposed", rationale=cc["rationale"], source_artifact_id=art.id))
        elif link.status == "proposed":
            link.rationale, link.source_artifact_id = cc["rationale"], art.id
    if ev.status == "extracted":
        ev.status = "proposed"
    audit.record(s, "ai.evidence_review.proposed", tenant_id=ev.tenant_id, actor_id=None, actor_type="worker",
                 target_type="ai_artifact", target_id=art.id, evidence_id=str(ev.id), decision=out["decision"],
                 warnings=len(v.warnings))
    return {"status": "proposed", "artifact_id": str(art.id)}


def _approved_sources(s: Session, engagement_id: uuid.UUID, tenant_id: uuid.UUID, question: str,
                      limit: int = 8) -> list[EvidenceChunk]:
    """Full-text retrieval over APPROVED evidence of this engagement only (status filter applied before ranking)."""
    q = text("""
        SELECT c.id FROM evidence_chunks c JOIN evidence_items e ON e.id = c.evidence_id
        WHERE c.tenant_id = :t AND e.engagement_id = :eng AND e.status = 'approved'
          AND c.tsv @@ websearch_to_tsquery('english', :q)
        ORDER BY ts_rank(c.tsv, websearch_to_tsquery('english', :q)) DESC LIMIT :n""")
    words = " OR ".join(w for w in question.replace('"', " ").split() if len(w) > 3)[:500] or question[:200]
    ids = [r[0] for r in s.execute(q, {"t": tenant_id, "eng": engagement_id, "q": words, "n": limit})]
    if not ids:
        return []
    found = {c.id: c for c in s.execute(select(EvidenceChunk).where(EvidenceChunk.id.in_(ids))).scalars()}
    return [found[i] for i in ids if i in found]


def answer_questionnaire_item(s: Session, item_id: uuid.UUID, job_id: uuid.UUID | None = None) -> dict[str, Any]:
    item = s.get(QuestionnaireItem, item_id)
    if item is None:
        return {"status": "not_found"}
    if item.status == "approved":
        return {"status": "skipped", "reason": "already approved"}
    qn = s.get(Questionnaire, item.questionnaire_id)
    assert qn is not None
    srcs = _approved_sources(s, qn.engagement_id, item.tenant_id, item.question)
    titles = {e.id: e.title for e in s.execute(select(EvidenceItem).where(
        EvidenceItem.id.in_([c.evidence_id for c in srcs]))).scalars()} if srcs else {}
    ctx = [{"handle": f"E{i + 1}", "id": str(c.id), "page": c.page, "title": titles.get(c.evidence_id, ""),
            "text": _redact(c.text)} for i, c in enumerate(srcs)]
    req = Request("questionnaire_answer", prompts.QUESTIONNAIRE_SYSTEM, prompts.questionnaire_user(item.question, ctx),
                  schemas.QUESTIONNAIRE_SCHEMA, {"question": item.question, "chunks": ctx}, 1500)
    chunk_text = {c["handle"]: c["text"] for c in ctx}
    try:
        run, v = _run(s, item.tenant_id, qn.engagement_id, job_id, req, prompts.QUESTIONNAIRE_PROMPT_V, schemas.QUESTIONNAIRE_V,
                      [str(item.id)] + [c["id"] for c in ctx], lambda d: validate_questionnaire(d, chunk_text))
    except OverBudget as e:
        return {"status": "over_budget", "detail": str(e)}
    if v is None:
        item.status = "needs_info"
        return {"status": "invalid_output", "error": run.error}
    h2c = {c["handle"]: c["id"] for c in ctx}
    out = dict(v.data)
    out["citations"] = [dict(c, chunk_uuid=h2c[c["chunk_id"]]) for c in out["citations"]]
    art = AIArtifact(tenant_id=item.tenant_id, engagement_id=qn.engagement_id, ai_run_id=run.id,
                     task_type="questionnaire_answer", subject_type="questionnaire_item", subject_id=item.id,
                     output=out, confidence=out["confidence"], decision=out["answer_type"],
                     validation_warnings=v.warnings)
    s.add(art)
    s.flush()
    item.artifact_id = art.id
    item.answer = out["answer"]
    item.status = "needs_info" if out["answer_type"] == "insufficient_evidence" else "drafted"
    audit.record(s, "ai.questionnaire.drafted", tenant_id=item.tenant_id, actor_id=None, actor_type="worker",
                 target_type="questionnaire_item", target_id=item.id, answer_type=out["answer_type"])
    return {"status": item.status, "artifact_id": str(art.id)}
