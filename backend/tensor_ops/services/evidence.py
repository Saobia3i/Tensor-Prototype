"""Evidence ingestion pipeline: quarantine -> validate -> scan -> hash -> dedupe -> sandboxed extract -> chunk."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..ingestion.extract import ExtractionError, chunk_pages, run_extraction
from ..ingestion.scanner import ScanError, get_scanner
from ..ingestion.storage import evidence_key, get_store, quarantine_key
from ..ingestion.validate import ValidationError, validate_file
from ..jobs.queue import enqueue
from ..models import EvidenceChunk, EvidenceItem, Tenant
from .state_machines import EVIDENCE, check


def hash_token(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def set_status(ev: EvidenceItem, target: str) -> None:
    check(EVIDENCE, ev.status, target, "evidence")
    ev.status = target
    ev.status_version += 1


def create_upload_session(s: Session, *, tenant_id: uuid.UUID, engagement_id: uuid.UUID, user_id: uuid.UUID,
                          title: str, filename: str, declared_mime: str | None, size_bytes: int,
                          request_id: uuid.UUID | None, supersedes: EvidenceItem | None,
                          period_start: date | None = None, period_end: date | None = None) -> tuple[EvidenceItem, str]:
    st = get_settings()
    if size_bytes <= 0 or size_bytes > st.max_upload_bytes:
        from fastapi import HTTPException
        raise HTTPException(status_code=413, detail=f"size must be 1..{st.max_upload_bytes} bytes")
    token = secrets.token_urlsafe(32)
    tenant = s.get(Tenant, tenant_id)
    ev = EvidenceItem(
        tenant_id=tenant_id, engagement_id=engagement_id, request_id=request_id, title=title[:300],
        original_filename=filename[:300], declared_mime=declared_mime, size_bytes=size_bytes, object_key="",
        uploaded_by=user_id, upload_token_hash=hash_token(token), period_start=period_start, period_end=period_end,
        upload_expires_at=datetime.now(timezone.utc) + timedelta(seconds=st.upload_session_ttl_s),
        retention_until=date.today() + timedelta(days=tenant.retention_days if tenant else 730),
    )
    if supersedes is not None:
        ev.lineage_id, ev.version = supersedes.lineage_id, supersedes.version + 1
    s.add(ev)
    s.flush()
    ev.object_key = quarantine_key(tenant_id, ev.id)
    return ev, token


def receive_upload(s: Session, ev: EvidenceItem, token: str, data: bytes, user_id: uuid.UUID) -> uuid.UUID:
    from fastapi import HTTPException
    if ev.status != "awaiting_upload" or not ev.upload_token_hash:
        raise HTTPException(status_code=409, detail="upload session already used")
    if not secrets.compare_digest(ev.upload_token_hash, hash_token(token)):
        raise HTTPException(status_code=403, detail="invalid upload token")
    if ev.upload_expires_at and ev.upload_expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="upload session expired")
    if ev.uploaded_by != user_id:
        raise HTTPException(status_code=403, detail="upload session belongs to another user")
    if len(data) != ev.size_bytes or len(data) > get_settings().max_upload_bytes:
        raise HTTPException(status_code=413, detail="uploaded size does not match the session")
    get_store().put(quarantine_key(ev.tenant_id, ev.id), data)
    ev.upload_token_hash = None
    ev.collected_at = datetime.now(timezone.utc)
    set_status(ev, "quarantined")
    job = enqueue(s, "ingest_evidence", ev.tenant_id, {"evidence_id": str(ev.id)},
                  idempotency_key=f"ingest:{ev.id}", requested_by=user_id)
    audit.record(s, "evidence.uploaded", tenant_id=ev.tenant_id, actor_id=user_id, target_type="evidence",
                 target_id=ev.id, size=len(data))
    return job


def ingest_system_document(s: Session, *, tenant_id: uuid.UUID, engagement_id: uuid.UUID, title: str,
                           filename: str, data: bytes, source: str, source_ref: str | None,
                           actor_id: uuid.UUID | None) -> EvidenceItem:
    """Evidence produced by Tensor itself (connector snapshots, approved policies). Still goes through the pipeline."""
    tenant = s.get(Tenant, tenant_id)
    ev = EvidenceItem(tenant_id=tenant_id, engagement_id=engagement_id, title=title, original_filename=filename,
                      size_bytes=len(data), object_key="", source=source, source_ref=source_ref, uploaded_by=actor_id,
                      collected_at=datetime.now(timezone.utc),
                      retention_until=date.today() + timedelta(days=tenant.retention_days if tenant else 730))
    s.add(ev)
    s.flush()
    ev.object_key = quarantine_key(tenant_id, ev.id)
    get_store().put(ev.object_key, data)
    set_status(ev, "quarantined")
    enqueue(s, "ingest_evidence", tenant_id, {"evidence_id": str(ev.id)}, idempotency_key=f"ingest:{ev.id}")
    return ev


def process_ingest(s: Session, evidence_id: uuid.UUID) -> dict[str, Any]:
    ev = s.get(EvidenceItem, evidence_id)
    if ev is None:
        return {"status": "not_found"}
    if ev.status not in ("quarantined", "failed", "scanning"):
        return {"status": "skipped", "reason": ev.status}  # idempotent on retry
    if ev.status != "scanning":
        set_status(ev, "scanning")
    store = get_store()
    qkey, ekey = quarantine_key(ev.tenant_id, ev.id), evidence_key(ev.tenant_id, ev.id)
    data = store.get(qkey) if store.exists(qkey) else store.get(ekey)
    st = get_settings()
    try:
        kind = validate_file(ev.original_filename, data, st.max_upload_bytes)
    except ValidationError as e:
        ev.error = f"validation: {e}"
        set_status(ev, "rejected_security")
        audit.record(s, "evidence.rejected", tenant_id=ev.tenant_id, actor_id=None, actor_type="worker",
                     target_type="evidence", target_id=ev.id, outcome="denied", reason=str(e))
        return {"status": "rejected", "reason": str(e)}
    try:
        result = get_scanner().scan(data)
    except ScanError:
        raise  # transaction rolls back: file stays quarantined (fail closed) and the job retries with backoff
    ev.scan_result = result.detail[:200]
    ev.detected_type = kind
    ev.sha256 = hashlib.sha256(data).hexdigest()
    if not result.clean:
        ev.error = "malware detected"
        set_status(ev, "rejected_security")
        audit.record(s, "evidence.malware_detected", tenant_id=ev.tenant_id, actor_id=None, actor_type="worker",
                     target_type="evidence", target_id=ev.id, outcome="denied", signature=result.detail[:100])
        return {"status": "malware"}
    dup = s.execute(select(EvidenceItem.id).where(
        EvidenceItem.tenant_id == ev.tenant_id, EvidenceItem.engagement_id == ev.engagement_id,
        EvidenceItem.sha256 == ev.sha256, EvidenceItem.id != ev.id,
        EvidenceItem.status.notin_(["deleted", "duplicate", "rejected_security", "superseded", "awaiting_upload"]),
    ).limit(1)).scalar_one_or_none()
    if dup:
        ev.error = f"duplicate_of:{dup}"
        set_status(ev, "duplicate")
        store.delete(qkey)
        return {"status": "duplicate", "duplicate_of": str(dup)}
    if store.exists(qkey):
        store.move(qkey, ekey)
    ev.object_key = ekey
    try:
        out = run_extraction(store.local_path(ekey), kind)
    except ExtractionError as e:
        ev.error = str(e)[:300]
        set_status(ev, "failed")
        audit.record(s, "evidence.extraction_failed", tenant_id=ev.tenant_id, actor_id=None, actor_type="worker",
                     target_type="evidence", target_id=ev.id, outcome="failure", reason=str(e)[:100])
        return {"status": "extraction_failed", "reason": str(e)}
    s.query(EvidenceChunk).filter(EvidenceChunk.evidence_id == ev.id).delete()
    chunks = chunk_pages(out["pages"])
    for c in chunks:
        s.add(EvidenceChunk(tenant_id=ev.tenant_id, evidence_id=ev.id, ordinal=c.ordinal, page=c.page,
                            char_start=c.char_start, char_end=c.char_end, text=c.text,
                            extraction_method=out["method"]))
    ev.page_count = len(out["pages"])
    ev.error = None
    set_status(ev, "extracted")
    audit.record(s, "evidence.extracted", tenant_id=ev.tenant_id, actor_id=None, actor_type="worker",
                 target_type="evidence", target_id=ev.id, chunks=len(chunks), sha256=ev.sha256)
    enqueue(s, "ai_review_evidence", ev.tenant_id, {"evidence_id": str(ev.id)},
            idempotency_key=f"ai_review:{ev.id}:auto")
    return {"status": "extracted", "chunks": len(chunks)}
