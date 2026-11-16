"""Deny-by-default, tenant-scoped authorization.

Tenant scope always comes from the authenticated user's active memberships. A caller with no membership in a
tenant gets 404 for that tenant's objects (no existence leak); a member lacking the permission gets 403.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Membership, User

STAFF_ROLES = {"delivery_lead", "compliance_specialist", "security_engineer", "quality_reviewer"}
CLIENT_ROLES = {"client_admin", "client_contributor"}
ALL_ROLES = STAFF_ROLES | CLIENT_ROLES | {"auditor", "partner_admin"}
REVIEWERS = {"delivery_lead", "compliance_specialist", "security_engineer", "quality_reviewer"}
_ALL = ALL_ROLES

PERMISSIONS: dict[str, set[str]] = {
    "engagement.read": _ALL,
    "engagement.manage": {"delivery_lead"},
    "control.assess": {"delivery_lead", "compliance_specialist", "security_engineer"},
    "evidence.read": STAFF_ROLES | CLIENT_ROLES | {"partner_admin"},
    "evidence.read_approved": _ALL,  # auditors see approved evidence only
    "evidence.upload": STAFF_ROLES | CLIENT_ROLES,
    "evidence.review": REVIEWERS,
    "ai.run": STAFF_ROLES,
    "ai.review": REVIEWERS,
    "request.read": STAFF_ROLES | CLIENT_ROLES | {"partner_admin"},
    "request.manage": STAFF_ROLES,
    "remediation.read": STAFF_ROLES | CLIENT_ROLES | {"partner_admin"},
    "remediation.manage": STAFF_ROLES,
    "remediation.update_progress": STAFF_ROLES | CLIENT_ROLES,
    "remediation.verify": {"delivery_lead", "compliance_specialist", "security_engineer"},
    "signoff.create": {"delivery_lead", "compliance_specialist", "security_engineer", "quality_reviewer"},
    "signoff.read": _ALL,
    "report.generate": STAFF_ROLES,
    "report.read_draft": STAFF_ROLES | {"partner_admin"},
    "report.read_released": _ALL,
    "report.approve": {"delivery_lead", "compliance_specialist", "quality_reviewer"},
    "report.release": {"delivery_lead"},
    "questionnaire.read": STAFF_ROLES | {"client_admin", "partner_admin"},
    "questionnaire.manage": STAFF_ROLES,
    "snapshot.manage": {"delivery_lead", "compliance_specialist"},
    "policy.read": STAFF_ROLES | CLIENT_ROLES | {"partner_admin"},
    "policy.manage": STAFF_ROLES,
    "policy.client_approve": {"client_admin"},
    "connector.read": STAFF_ROLES | {"client_admin"},
    "connector.manage": {"delivery_lead", "security_engineer"},
    "connector.customer_approve": {"client_admin"},
    "time.log": STAFF_ROLES,
    "metrics.read": {"delivery_lead", "quality_reviewer"},
    "audit.read": {"delivery_lead", "quality_reviewer", "client_admin"},
    "membership.read": {"delivery_lead", "client_admin", "partner_admin"},
    "membership.manage": {"delivery_lead"},
    "membership.manage_client": {"client_admin"},
    "deletion.request": {"delivery_lead", "client_admin"},
    "deletion.approve": {"delivery_lead"},
}

PRIVILEGED = {"signoff.create", "report.release", "deletion.approve", "membership.manage", "connector.manage"}


@dataclass
class Principal:
    user: User
    roles: dict[uuid.UUID, str] = field(default_factory=dict)
    mfa: bool = False
    require_mfa: bool = True

    @property
    def id(self) -> uuid.UUID:
        return self.user.id

    @property
    def tenant_ids(self) -> set[uuid.UUID]:
        return set(self.roles)

    def tenants_with(self, perm: str) -> set[uuid.UUID]:
        allowed = PERMISSIONS.get(perm)
        if allowed is None:
            return set()
        return {t for t, r in self.roles.items() if r in allowed}

    def can(self, tenant_id: uuid.UUID, perm: str) -> bool:
        role = self.roles.get(tenant_id)
        return role is not None and role in PERMISSIONS.get(perm, set())

    def require(self, tenant_id: uuid.UUID | None, perm: str) -> None:
        if tenant_id is None or tenant_id not in self.roles:
            raise HTTPException(status_code=404, detail="Not found")
        if not self.can(tenant_id, perm):
            raise HTTPException(status_code=403, detail="Forbidden")
        if perm in PRIVILEGED and self.require_mfa and not self.mfa:
            raise HTTPException(status_code=403, detail="Step-up authentication (MFA) required")

    def role_in(self, tenant_id: uuid.UUID) -> str | None:
        return self.roles.get(tenant_id)


def load_roles(session: Session, user: User) -> dict[uuid.UUID, str]:
    now = datetime.now(timezone.utc)
    rows = session.execute(
        select(Membership).where(Membership.user_id == user.id, Membership.status == "active")
    ).scalars()
    return {m.tenant_id: m.role for m in rows if m.expires_at is None or m.expires_at > now}


def get_scoped(session: Session, principal: Principal, model: type, obj_id: uuid.UUID, perm: str):  # type: ignore[no-untyped-def]
    """Load a tenant-owned object and enforce permission. 404 if missing or out of scope."""
    obj = session.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Not found")
    principal.require(obj.tenant_id, perm)
    return obj
