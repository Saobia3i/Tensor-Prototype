"""Connector lifecycle: authorize (customer-approved, read-only), validate, sync to evidence, revoke."""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from .. import audit
from ..jobs.queue import PermanentError
from ..models import Connector
from ..services.evidence import ingest_system_document
from ..services.state_machines import CONNECTOR, check
from .base import ConnectorAuthError, ConnectorImpl, ConnectorRateLimited, ConnectorScopeError
from .github import GitHubConnector
from .secrets import SecretStore

PROVIDERS = {"github": {"label": "GitHub", "config_fields": ["org"], "mode": "read-only"}}

# Test hook: build an implementation with an injected HTTP transport.
_factory_override: Callable[[Connector, str], ConnectorImpl] | None = None


def set_factory_override(fn: Callable[[Connector, str], ConnectorImpl] | None) -> None:
    global _factory_override
    _factory_override = fn


def build_impl(c: Connector, token: str) -> ConnectorImpl:
    if _factory_override:
        return _factory_override(c, token)
    if c.provider == "github":
        return GitHubConnector(c.config["org"], token)
    raise ValueError("unknown provider")


def transition(c: Connector, target: str) -> None:
    check(CONNECTOR, c.status, target, "connector")
    c.status = target


def authorize(s: Session, c: Connector, token: str, actor_id: uuid.UUID) -> dict[str, Any]:
    if c.customer_approved_by is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="a client admin must approve this connector's read-only scope first")
    store = SecretStore(s)
    if c.secret_ref:
        store.delete(c.tenant_id, c.secret_ref)
    c.secret_ref = store.put(c.tenant_id, token)
    try:
        info = build_impl(c, token).validate_connection()
    except (ConnectorAuthError, ConnectorScopeError) as e:
        c.last_error = str(e)
        if c.status != "error":
            transition(c, "error")
        audit.record(s, "connector.validation_failed", tenant_id=c.tenant_id, actor_id=actor_id,
                     target_type="connector", target_id=c.id, outcome="failure", reason=str(e))
        return {"status": c.status, "error": str(e)}
    c.scopes = [x.strip() for x in str(info.get("token_scopes", "")).split(",") if x.strip()]
    c.last_error = None
    if c.status != "active":
        transition(c, "active")
    audit.record(s, "connector.authorized", tenant_id=c.tenant_id, actor_id=actor_id, target_type="connector",
                 target_id=c.id, provider=c.provider)
    return {"status": c.status, "info": {k: v for k, v in info.items() if k != "token_scopes"}}


def sync(s: Session, c: Connector) -> dict[str, Any]:
    if c.status not in ("active", "degraded"):
        raise PermanentError(f"connector is {c.status}")
    if not c.secret_ref:
        raise PermanentError("connector has no credential")
    token = SecretStore(s).get(c.tenant_id, c.secret_ref)
    try:
        out = build_impl(c, token).sync(c.checkpoint or {})
    except ConnectorAuthError as e:
        transition(c, "error")
        c.last_error = str(e)
        audit.record(s, "connector.auth_failed", tenant_id=c.tenant_id, actor_id=None, actor_type="worker",
                     target_type="connector", target_id=c.id, outcome="failure")
        s.commit()
        raise PermanentError(str(e)) from e
    except ConnectorScopeError as e:
        if c.status == "active":
            transition(c, "degraded")
        c.last_error = str(e)
        s.commit()
        raise PermanentError(str(e)) from e
    except ConnectorRateLimited:
        raise  # retried with backoff by the queue
    data = json.dumps(out.document, indent=2, sort_keys=True).encode()
    ev = ingest_system_document(s, tenant_id=c.tenant_id, engagement_id=c.engagement_id, title=out.title,
                                filename=out.filename, data=data, source=f"connector:{c.provider}",
                                source_ref=out.source_ref, actor_id=None)
    c.checkpoint, c.last_sync_at, c.last_error = out.checkpoint, datetime.now(timezone.utc), None
    if c.status == "degraded":
        transition(c, "active")
    audit.record(s, "connector.synced", tenant_id=c.tenant_id, actor_id=None, actor_type="worker",
                 target_type="connector", target_id=c.id, evidence_id=str(ev.id))
    return {"status": "synced", "evidence_id": str(ev.id)}


def revoke(s: Session, c: Connector, actor_id: uuid.UUID) -> None:
    if c.secret_ref:
        SecretStore(s).delete(c.tenant_id, c.secret_ref)
        c.secret_ref = None
    transition(c, "revoked")
    audit.record(s, "connector.revoked", tenant_id=c.tenant_id, actor_id=actor_id, target_type="connector",
                 target_id=c.id)


def sync_idempotency_key(c: Connector) -> str:
    return f"connector_sync:{c.id}:{date.today().isoformat()}"
