"""Server-enforced state machines. An LLM suggestion never moves state; only these transitions do."""
from __future__ import annotations

from fastapi import HTTPException

EVIDENCE = {
    "awaiting_upload": {"quarantined", "deleted"},
    "quarantined": {"scanning", "rejected_security", "deleted"},
    "scanning": {"extracted", "rejected_security", "failed", "duplicate", "deleted"},
    "duplicate": {"deleted"},
    "failed": {"scanning", "deleted"},
    "extracted": {"proposed", "expert_review", "deleted"},
    "proposed": {"expert_review", "approved", "rejected", "needs_information", "deleted"},
    "expert_review": {"approved", "rejected", "needs_information", "deleted"},
    "needs_information": {"expert_review", "superseded", "deleted"},
    "approved": {"expired", "superseded", "deleted"},
    "rejected": {"superseded", "deleted"},
    "rejected_security": {"deleted"},
    "expired": {"superseded", "deleted"},
    "superseded": {"deleted"},
    "deleted": set(),
}
# Transitions only a human reviewer may make (never a worker).
EVIDENCE_HUMAN_ONLY = {"approved", "rejected", "needs_information"}

ENGAGEMENT_CONTROL = {
    "not_started": {"in_progress", "not_applicable"},
    "in_progress": {"evidence_collected", "gap", "not_applicable"},
    "gap": {"in_progress", "evidence_collected"},
    "evidence_collected": {"ready_for_signoff", "in_progress", "gap"},
    "ready_for_signoff": {"signed_off", "in_progress", "gap"},
    "signed_off": {"in_progress"},  # reopening is allowed and audited; the sign-off record stays
    "not_applicable": {"in_progress"},
}

REMEDIATION = {
    "open": {"in_progress", "cancelled"},
    "in_progress": {"blocked", "ready_for_review", "open"},
    "blocked": {"in_progress"},
    "ready_for_review": {"verified_closed", "in_progress"},
    "verified_closed": set(),
    "cancelled": set(),
}
REMEDIATION_STAFF_ONLY = {"verified_closed", "cancelled"}

CONNECTOR = {
    "pending_authorization": {"active", "error", "revoked"},
    "active": {"degraded", "revoked", "error"},
    "degraded": {"active", "revoked", "error"},
    "error": {"active", "revoked"},
    "revoked": set(),
}

ENGAGEMENT = {
    "planning": {"in_progress", "cancelled"},
    "in_progress": {"audit_ready", "on_hold", "cancelled"},
    "on_hold": {"in_progress", "cancelled"},
    "audit_ready": {"in_audit", "in_progress"},
    "in_audit": {"maintaining", "in_progress"},
    "maintaining": {"renewal_due", "closed"},
    "renewal_due": {"maintaining", "closed"},
    "closed": set(),
    "cancelled": set(),
}

REPORT = {"draft": {"approved"}, "approved": {"released", "draft"}, "released": set()}


def check(machine: dict[str, set[str]], current: str, target: str, what: str) -> None:
    if target not in machine.get(current, set()):
        raise HTTPException(status_code=409, detail=f"Illegal {what} transition: {current} -> {target}")
