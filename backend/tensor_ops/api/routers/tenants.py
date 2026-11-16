"""Tenants (client organisations), partners and memberships."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import audit
from ...db import widen_scope
from ...models import Membership, Partner, Tenant, User
from ...security.authz import ALL_ROLES, CLIENT_ROLES, Principal
from ..deps import get_db, principal

router = APIRouter(tags=["tenants"])


def tenant_out(t: Tenant, role: str | None = None) -> dict:
    return {"id": str(t.id), "name": t.name, "status": t.status, "partner_id": str(t.partner_id) if t.partner_id else None,
            "employee_count": t.employee_count, "industry": t.industry, "retention_days": t.retention_days,
            "is_internal": t.is_internal, "my_role": role}


class TenantIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    employee_count: int | None = Field(default=None, ge=1, le=1_000_000)
    industry: str | None = Field(default=None, max_length=80)
    partner_id: uuid.UUID | None = None
    status: str = Field(default="active", pattern="^(prospect|active)$")
    retention_days: int = Field(default=730, ge=30, le=3650)


def create_tenant(s: Session, p: Principal, body: TenantIn) -> Tenant:
    """Delivery staff create client tenants; the creator becomes delivery lead (least privilege: no global access)."""
    if not p.user.is_staff:
        raise HTTPException(status_code=403, detail="Only Tensor staff can create client organisations")
    t = Tenant(id=uuid.uuid4(), name=body.name, employee_count=body.employee_count, industry=body.industry,
               partner_id=body.partner_id, status=body.status, retention_days=body.retention_days)
    widen_scope(s, t.id)
    s.add(t)
    s.flush()
    s.add(Membership(user_id=p.id, tenant_id=t.id, role="delivery_lead"))
    if body.partner_id:
        if s.get(Partner, body.partner_id) is None:
            raise HTTPException(status_code=422, detail="unknown partner")
        for u in s.execute(select(User).where(User.partner_id == body.partner_id, User.active.is_(True))).scalars():
            s.add(Membership(user_id=u.id, tenant_id=t.id, role="partner_admin"))
    audit.record(s, "tenant.created", tenant_id=t.id, actor_id=p.id, target_type="tenant", target_id=t.id)
    return t


@router.get("/tenants")
def list_tenants(p: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    if not p.roles:
        return []
    rows = s.execute(select(Tenant).where(Tenant.id.in_(p.tenant_ids)).order_by(Tenant.name)).scalars()
    return [tenant_out(t, p.role_in(t.id)) for t in rows]


@router.post("/tenants", status_code=201)
def post_tenant(body: TenantIn, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    t = create_tenant(s, p, body)
    s.commit()
    return tenant_out(t, "delivery_lead")


@router.get("/tenants/{tenant_id}")
def get_tenant(tenant_id: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    p.require(tenant_id, "engagement.read")
    t = s.get(Tenant, tenant_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Not found")
    return tenant_out(t, p.role_in(t.id))


class MemberIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    role: str
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


@router.get("/tenants/{tenant_id}/members")
def members(tenant_id: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    p.require(tenant_id, "membership.read")
    rows = s.execute(select(Membership, User).join(User, User.id == Membership.user_id)
                     .where(Membership.tenant_id == tenant_id)).all()
    return [{"id": str(m.id), "user_id": str(u.id), "email": u.email, "name": u.name, "role": m.role,
             "status": m.status, "expires_at": m.expires_at.isoformat() if m.expires_at else None,
             "can_sign": u.can_sign, "expert_title": u.expert_title} for m, u in rows]


@router.post("/tenants/{tenant_id}/members", status_code=201)
def add_member(tenant_id: uuid.UUID, body: MemberIn, p: Principal = Depends(principal),
               s: Session = Depends(get_db)) -> dict:
    if body.role not in ALL_ROLES:
        raise HTTPException(status_code=422, detail="unknown role")
    if p.can(tenant_id, "membership.manage"):
        p.require(tenant_id, "membership.manage")
    else:
        p.require(tenant_id, "membership.manage_client")
        if body.role not in CLIENT_ROLES:
            raise HTTPException(status_code=403, detail="client admins can only add client users")
    email = body.email.lower()
    u = s.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if u is None:
        u = User(email=email, name=body.name, is_staff=False)
        s.add(u)
        s.flush()
    if body.role in {"delivery_lead", "compliance_specialist", "security_engineer", "quality_reviewer"} and not u.is_staff:
        raise HTTPException(status_code=422, detail="staff roles require a Tensor staff account")
    expires = None
    if body.role == "auditor":
        expires = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days or 30)
    elif body.expires_in_days:
        expires = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
    m = s.execute(select(Membership).where(Membership.user_id == u.id, Membership.tenant_id == tenant_id)).scalar_one_or_none()
    if m:
        m.role, m.expires_at, m.status = body.role, expires, "active"
    else:
        m = Membership(user_id=u.id, tenant_id=tenant_id, role=body.role, expires_at=expires)
        s.add(m)
    audit.record(s, "membership.granted", tenant_id=tenant_id, actor_id=p.id, target_type="user", target_id=u.id,
                 role=body.role)
    s.commit()
    return {"id": str(m.id), "user_id": str(u.id), "role": m.role}


@router.delete("/tenants/{tenant_id}/members/{membership_id}", status_code=204)
def remove_member(tenant_id: uuid.UUID, membership_id: uuid.UUID, p: Principal = Depends(principal),
                  s: Session = Depends(get_db)) -> None:
    m = s.get(Membership, membership_id)
    if m is None or m.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Not found")
    if not p.can(tenant_id, "membership.manage"):
        p.require(tenant_id, "membership.manage_client")
        if m.role not in CLIENT_ROLES:
            raise HTTPException(status_code=403, detail="Forbidden")
    m.status = "revoked"
    audit.record(s, "membership.revoked", tenant_id=tenant_id, actor_id=p.id, target_type="user", target_id=m.user_id)
    s.commit()


class PartnerIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    brand_name: str = Field(min_length=2, max_length=200)
    brand_color: str = Field(default="#1f2937", pattern=r"^#[0-9a-fA-F]{6}$")
    contact_email: EmailStr | None = None
    price_per_client_year: float = Field(default=4500, ge=0)


@router.get("/partners")
def partners(p: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    if not p.user.is_staff:
        raise HTTPException(status_code=403, detail="Forbidden")
    return [{"id": str(x.id), "name": x.name, "brand_name": x.brand_name, "brand_color": x.brand_color,
             "price_per_client_year": x.price_per_client_year} for x in s.execute(select(Partner)).scalars()]


@router.post("/partners", status_code=201)
def create_partner(body: PartnerIn, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    if not p.user.is_platform_owner:
        raise HTTPException(status_code=403, detail="Only the platform owner can add partners")
    x = Partner(**body.model_dump())
    s.add(x)
    s.flush()
    audit.record(s, "partner.created", tenant_id=None, actor_id=p.id, target_type="partner", target_id=x.id)
    s.commit()
    return {"id": str(x.id)}


@router.get("/staff")
def staff(p: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    """Tensor staff directory (for assigning owners/signers). Staff only."""
    if not p.user.is_staff:
        raise HTTPException(status_code=403, detail="Forbidden")
    return [{"id": str(u.id), "name": u.name, "email": u.email, "can_sign": u.can_sign, "expert_title": u.expert_title}
            for u in s.execute(select(User).where(User.is_staff.is_(True), User.active.is_(True))).scalars()]
