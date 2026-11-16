from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Tenant
from ...security.authz import PERMISSIONS, Principal
from ..deps import get_db, principal

router = APIRouter(tags=["me"])


@router.get("/me")
def me(p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    tenants = {t.id: t for t in s.execute(select(Tenant).where(Tenant.id.in_(p.tenant_ids))).scalars()} if p.roles else {}
    return {
        "id": str(p.user.id), "email": p.user.email, "name": p.user.name, "is_staff": p.user.is_staff,
        "is_platform_owner": p.user.is_platform_owner, "can_sign": p.user.can_sign, "expert_title": p.user.expert_title,
        "mfa": p.mfa,
        "memberships": [{
            "tenant_id": str(t), "tenant_name": tenants[t].name if t in tenants else None,
            "tenant_status": tenants[t].status if t in tenants else None, "role": r,
            "permissions": sorted(k for k, roles in PERMISSIONS.items() if r in roles),
        } for t, r in p.roles.items()],
    }
