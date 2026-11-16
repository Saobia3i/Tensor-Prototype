from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...models import Engagement
from ...security.authz import Principal, get_scoped
from ...services.metrics import engagement_metrics, portfolio
from ..deps import get_db, principal

router = APIRouter(tags=["metrics"])


@router.get("/metrics/portfolio")
def get_portfolio(p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    scope = p.tenants_with("metrics.read")
    return portfolio(s, scope) if scope else {"engagements": []}


@router.get("/engagements/{eid}/metrics")
def get_engagement_metrics(eid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    e = get_scoped(s, p, Engagement, eid, "metrics.read")
    return engagement_metrics(s, e)
