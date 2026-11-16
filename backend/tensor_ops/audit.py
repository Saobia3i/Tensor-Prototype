"""Append-only business and security audit events."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from .logging import correlation_id, redact_value
from .models import AuditEvent


def record(
    session: Session,
    action: str,
    *,
    tenant_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    target_type: str | None = None,
    target_id: object | None = None,
    outcome: str = "success",
    actor_type: str = "user",
    **meta: Any,
) -> None:
    session.add(AuditEvent(
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_type=actor_type,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        outcome=outcome,
        correlation_id=correlation_id.get(),
        meta=redact_value(meta),  # minimal, safe metadata only
    ))
