"""Security questionnaires: import, AI drafting grounded in approved evidence, expert approval, export."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import audit
from ...jobs.queue import enqueue
from ...models import AIArtifact, Engagement, Questionnaire, QuestionnaireItem, SignOff
from ...security.authz import Principal, get_scoped
from ...services.review import create_signoff
from ..deps import get_db, limit, principal

router = APIRouter(tags=["questionnaires"])
MAX_Q = 500


def _parse(filename: str, data: bytes) -> list[str]:
    name = filename.lower()
    if name.endswith(".xlsx"):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        qs = []
        for row in wb.worksheets[0].iter_rows(max_row=MAX_Q + 1, values_only=True):
            v = next((str(c).strip() for c in row if c not in (None, "")), "")
            if v:
                qs.append(v)
        return qs
    text = data.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    return [r[0].strip() for r in rows if r and r[0].strip()][: MAX_Q + 1]


class QIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    requester: str = Field(default="", max_length=200)
    due_date: date | None = None
    questions: list[str] = Field(min_length=1, max_length=MAX_Q)


def _create(s: Session, p: Principal, e: Engagement, name: str, requester: str, due: date | None, qs: list[str]) -> Questionnaire:
    qs = [q.strip()[:2000] for q in qs if q.strip()]
    if qs and qs[0].lower() in ("question", "questions"):
        qs = qs[1:]
    if not qs or len(qs) > MAX_Q:
        raise HTTPException(status_code=422, detail=f"1..{MAX_Q} questions required")
    qn = Questionnaire(tenant_id=e.tenant_id, engagement_id=e.id, name=name, requester=requester, due_date=due)
    s.add(qn)
    s.flush()
    for i, q in enumerate(qs):
        s.add(QuestionnaireItem(tenant_id=e.tenant_id, questionnaire_id=qn.id, ordinal=i + 1, question=q))
    audit.record(s, "questionnaire.created", tenant_id=e.tenant_id, actor_id=p.id, target_type="questionnaire",
                 target_id=qn.id, questions=len(qs))
    return qn


@router.post("/engagements/{eid}/questionnaires", status_code=201)
def create_q(eid: uuid.UUID, body: QIn, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    e = get_scoped(s, p, Engagement, eid, "questionnaire.manage")
    qn = _create(s, p, e, body.name, body.requester, body.due_date, body.questions)
    s.commit()
    return {"id": str(qn.id)}


@router.post("/engagements/{eid}/questionnaires/upload", status_code=201)
async def upload_q(eid: uuid.UUID, name: str = Form(...), requester: str = Form(""), file: UploadFile = File(...),
                   p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    e = get_scoped(s, p, Engagement, eid, "questionnaire.manage")
    data = await file.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="questionnaire file too large")
    qn = _create(s, p, e, name, requester, None, _parse(file.filename or "q.csv", data))
    s.commit()
    return {"id": str(qn.id)}


@router.get("/engagements/{eid}/questionnaires")
def list_q(eid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    e = get_scoped(s, p, Engagement, eid, "questionnaire.read")
    out = []
    for qn in s.execute(select(Questionnaire).where(Questionnaire.engagement_id == e.id)
                        .order_by(Questionnaire.created_at.desc())).scalars():
        counts = dict(s.execute(select(QuestionnaireItem.status, func.count())
                                .where(QuestionnaireItem.questionnaire_id == qn.id)
                                .group_by(QuestionnaireItem.status)).all())
        out.append({"id": str(qn.id), "name": qn.name, "requester": qn.requester, "status": qn.status,
                    "due_date": qn.due_date.isoformat() if qn.due_date else None, "counts": counts,
                    "created_at": qn.created_at.isoformat()})
    return out


@router.get("/questionnaires/{qid}")
def get_q(qid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    qn = get_scoped(s, p, Questionnaire, qid, "questionnaire.read")
    items = s.execute(select(QuestionnaireItem).where(QuestionnaireItem.questionnaire_id == qn.id)
                      .order_by(QuestionnaireItem.ordinal)).scalars().all()
    staff = p.can(qn.tenant_id, "questionnaire.manage")
    arts = {a.id: a for a in s.execute(select(AIArtifact).where(
        AIArtifact.id.in_([i.artifact_id for i in items if i.artifact_id]))).scalars()} if staff else {}
    so = s.execute(select(SignOff).where(SignOff.subject_id == qn.id)).scalars().first()
    return {"id": str(qn.id), "name": qn.name, "requester": qn.requester, "status": qn.status,
            "engagement_id": str(qn.engagement_id),
            "signoff": {"signer": so.signer_name, "signed_at": so.signed_at.isoformat(), "sha256": so.content_sha256} if so else None,
            "items": [{"id": str(i.id), "ordinal": i.ordinal, "question": i.question,
                       "answer": i.answer if (staff or i.status == "approved") else None, "status": i.status,
                       "artifact": ({"id": str(arts[i.artifact_id].id), "review_state": arts[i.artifact_id].review_state,
                                     "version": arts[i.artifact_id].version, "output": arts[i.artifact_id].output,
                                     "warnings": arts[i.artifact_id].validation_warnings}
                                    if i.artifact_id in arts else None)} for i in items]}


@router.post("/questionnaires/{qid}/draft", status_code=202, dependencies=[Depends(limit("ai_jobs", 30))])
def draft_q(qid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    qn = get_scoped(s, p, Questionnaire, qid, "questionnaire.manage")
    p.require(qn.tenant_id, "ai.run")
    items = s.execute(select(QuestionnaireItem).where(QuestionnaireItem.questionnaire_id == qn.id,
                                                      QuestionnaireItem.status.in_(["unanswered", "needs_info"]))).scalars().all()
    n = 0
    for it in items:
        enqueue(s, "ai_answer_questionnaire_item", qn.tenant_id, {"item_id": str(it.id)},
                idempotency_key=f"qa:{it.id}:{it.updated_at.timestamp() if it.updated_at else 0}", requested_by=p.id)
        n += 1
    qn.status = "in_review"
    s.commit()
    return {"queued": n}


class ManualAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=3000)


@router.post("/questionnaire-items/{iid}/answer")
def manual_answer(iid: uuid.UUID, body: ManualAnswer, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    it = get_scoped(s, p, QuestionnaireItem, iid, "questionnaire.manage")
    if it.artifact_id:
        a = s.get(AIArtifact, it.artifact_id)
        if a and a.review_state == "proposed":
            a.review_state = "superseded"
    it.answer, it.status = body.answer, "approved"
    audit.record(s, "questionnaire.answered_manually", tenant_id=it.tenant_id, actor_id=p.id,
                 target_type="questionnaire_item", target_id=it.id)
    s.commit()
    return {"id": str(it.id), "status": it.status}


@router.post("/questionnaires/{qid}/approve")
def approve_q(qid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    qn = get_scoped(s, p, Questionnaire, qid, "signoff.create")
    items = s.execute(select(QuestionnaireItem).where(QuestionnaireItem.questionnaire_id == qn.id)
                      .order_by(QuestionnaireItem.ordinal)).scalars().all()
    pending = [i.ordinal for i in items if i.status != "approved"]
    if pending:
        raise HTTPException(status_code=409, detail=f"{len(pending)} answers are not approved (e.g. #{pending[0]})")
    so = create_signoff(s, tenant_id=qn.tenant_id, engagement_id=qn.engagement_id, subject_type="questionnaire",
                        subject_id=qn.id, signer=p.user, statement="I have reviewed every answer in this questionnaire.",
                        content=[[i.ordinal, i.question, i.answer] for i in items])
    qn.status = "approved"
    s.commit()
    return {"status": qn.status, "signoff_sha256": so.content_sha256}


def _csv_safe(v: str) -> str:
    """Neutralise spreadsheet formula injection in exported cells."""
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


@router.get("/questionnaires/{qid}/export")
def export_q(qid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> Response:
    qn = get_scoped(s, p, Questionnaire, qid, "questionnaire.read")
    if qn.status not in ("approved", "delivered"):
        raise HTTPException(status_code=409, detail="questionnaire must be approved and signed before export")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["#", "Question", "Answer"])
    for i in s.execute(select(QuestionnaireItem).where(QuestionnaireItem.questionnaire_id == qn.id)
                       .order_by(QuestionnaireItem.ordinal)).scalars():
        w.writerow([i.ordinal, _csv_safe(i.question), _csv_safe(i.answer or "")])
    audit.record(s, "questionnaire.exported", tenant_id=qn.tenant_id, actor_id=p.id, target_type="questionnaire",
                 target_id=qn.id)
    s.commit()
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"content-disposition": 'attachment; filename="questionnaire.csv"'})
