"""Job handlers. Each runs inside an RLS session scoped to the job's tenant only."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..ai.tasks import answer_questionnaire_item, review_evidence
from ..connectors import service as connectors
from ..models import Connector, Job
from ..services import retention
from ..services.evidence import process_ingest
from .queue import PermanentError, handler


@handler("ingest_evidence")
def _ingest(s: Session, job: Job) -> dict[str, Any]:
    return process_ingest(s, uuid.UUID(job.payload["evidence_id"]))


@handler("ai_review_evidence")
def _review(s: Session, job: Job) -> dict[str, Any]:
    return review_evidence(s, uuid.UUID(job.payload["evidence_id"]), job.id)


@handler("ai_answer_questionnaire_item")
def _qa(s: Session, job: Job) -> dict[str, Any]:
    return answer_questionnaire_item(s, uuid.UUID(job.payload["item_id"]), job.id)


@handler("connector_sync")
def _sync(s: Session, job: Job) -> dict[str, Any]:
    c = s.get(Connector, uuid.UUID(job.payload["connector_id"]))
    if c is None:
        raise PermanentError("connector not found in job tenant")
    return connectors.sync(s, c)


@handler("retention_sweep")
def _sweep(s: Session, job: Job) -> dict[str, Any]:
    return retention.sweep()


@handler("execute_deletion")
def _delete(s: Session, job: Job) -> dict[str, Any]:
    return retention.execute_deletion(uuid.UUID(job.payload["deletion_request_id"]))
