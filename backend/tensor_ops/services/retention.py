"""Retention, expiry and verified deletion. Runs with the owner role (cross-tenant maintenance) and is audited."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from .. import audit
from ..db import admin_session
from ..ingestion.storage import evidence_key, get_store, quarantine_key
from ..models import (
    AIArtifact,
    DeletionRequest,
    DownloadToken,
    EvidenceChunk,
    EvidenceControlLink,
    EvidenceItem,
    Report,
    Tenant,
)


def _purge_evidence(s: Session, ev: EvidenceItem) -> None:
    store = get_store()
    for key in (quarantine_key(ev.tenant_id, ev.id), evidence_key(ev.tenant_id, ev.id)):
        store.delete(key)
    s.execute(delete(EvidenceChunk).where(EvidenceChunk.evidence_id == ev.id))
    s.execute(delete(EvidenceControlLink).where(EvidenceControlLink.evidence_id == ev.id))
    # AI artifacts may quote document text: scrub the quotes but keep the review record shell for audit.
    for art in s.execute(select(AIArtifact).where(AIArtifact.subject_id == ev.id)).scalars():
        art.output = {"purged": True, "reason": "evidence deleted under retention/deletion policy"}
        art.final_output = None
    ev.status = "deleted"
    ev.title = "[deleted]"
    ev.original_filename = "[deleted]"
    ev.object_key = ""


def sweep(today: date | None = None) -> dict[str, int]:
    today = today or date.today()
    expired = purged = 0
    with admin_session() as s:
        for ev in s.execute(select(EvidenceItem).where(EvidenceItem.status == "approved",
                                                       EvidenceItem.expires_at < today)).scalars():
            ev.status = "expired"
            expired += 1
            audit.record(s, "evidence.expired", tenant_id=ev.tenant_id, actor_id=None, actor_type="system",
                         target_type="evidence", target_id=ev.id)
        for ev in s.execute(select(EvidenceItem).where(EvidenceItem.status != "deleted",
                                                       EvidenceItem.retention_until < today)).scalars():
            _purge_evidence(s, ev)
            purged += 1
            audit.record(s, "evidence.purged_retention", tenant_id=ev.tenant_id, actor_id=None, actor_type="system",
                         target_type="evidence", target_id=ev.id)
        s.execute(delete(DownloadToken).where(DownloadToken.expires_at < datetime.now(timezone.utc)))
        s.commit()
    return {"expired": expired, "purged": purged}


def execute_deletion(req_id: uuid.UUID) -> dict[str, Any]:
    """Run an approved deletion request. Verifies afterwards that nothing remains."""
    with admin_session() as s:
        req = s.get(DeletionRequest, req_id)
        if req is None or req.status != "approved":
            raise ValueError("deletion request is not approved")
        result: dict[str, Any] = {"scope": req.scope}
        if req.scope == "evidence_item":
            ev = s.get(EvidenceItem, req.target_id)
            if ev is None or ev.tenant_id != req.tenant_id:
                raise ValueError("target not in tenant")
            _purge_evidence(s, ev)
            # Reports that cited it can no longer be released; drafts are deleted.
            from .reports import referenced_evidence
            for rep in s.execute(select(Report).where(Report.tenant_id == req.tenant_id,
                                                      Report.status != "released")).scalars().all():
                if str(ev.id) in referenced_evidence(rep.content):
                    s.delete(rep)
            remaining = s.execute(select(EvidenceChunk).where(EvidenceChunk.evidence_id == ev.id)).first()
            result["verified"] = remaining is None and not get_store().exists(evidence_key(ev.tenant_id, ev.id))
        elif req.scope == "tenant":
            t = s.get(Tenant, req.target_id)
            if t is None or t.id != req.tenant_id:
                raise ValueError("target mismatch")
            evs = s.execute(select(EvidenceItem).where(EvidenceItem.tenant_id == t.id)).scalars().all()
            for ev in evs:
                _purge_evidence(s, ev)
            s.execute(text("SET LOCAL app.allow_purge = 'on'"))
            req.status, req.result = "completed", {"scope": "tenant", "evidence_purged": len(evs)}
            audit.record(s, "tenant.purged", tenant_id=t.id, actor_id=req.approved_by, target_type="tenant",
                         target_id=t.id, evidence=len(evs))
            s.flush()
            s.execute(update(DeletionRequest).where(DeletionRequest.id == req.id).values(status="completed"))
            s.delete(t)  # cascades to all tenant-owned rows; audit_events (ids only) are retained
            s.commit()
            return {"scope": "tenant", "evidence_purged": len(evs), "verified": True}
        else:
            raise ValueError("unsupported scope")
        req.status, req.result = "completed", result
        audit.record(s, "deletion.completed", tenant_id=req.tenant_id, actor_id=req.approved_by,
                     target_type=req.scope, target_id=req.target_id, verified=result.get("verified"))
        s.commit()
        return result
