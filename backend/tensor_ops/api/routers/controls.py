from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Control, ControlMapping, Framework, FrameworkRequirement
from ...security.authz import Principal
from ...services.pricing import PRICE_BOOK
from ..deps import get_db, principal

router = APIRouter(tags=["control library"])


@router.get("/frameworks")
def frameworks(_: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    return [{"code": f.code, "name": f.name, "version": f.version, "source_url": f.source_url,
             "license_note": f.license_note} for f in s.execute(select(Framework).order_by(Framework.code)).scalars()]


@router.get("/controls")
def controls(framework: str | None = None, _: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    maps: dict = {}
    for cid, fw, ref, para, status in s.execute(
            select(ControlMapping.control_id, Framework.code, FrameworkRequirement.ref, FrameworkRequirement.paraphrase,
                   ControlMapping.status)
            .join(FrameworkRequirement, FrameworkRequirement.id == ControlMapping.requirement_id)
            .join(Framework, Framework.id == FrameworkRequirement.framework_id)).all():
        maps.setdefault(cid, []).append({"framework": fw, "ref": ref, "topic": para, "mapping_status": status})
    out = []
    for c in s.execute(select(Control).order_by(Control.code)).scalars():
        m = maps.get(c.id, [])
        if framework and not any(x["framework"] == framework for x in m):
            continue
        out.append({"id": str(c.id), "code": c.code, "domain": c.domain, "title": c.title, "objective": c.objective,
                    "guidance": c.guidance, "evidence_examples": c.evidence_examples, "est_hours": c.est_hours,
                    "version": c.version, "mappings": m})
    return out


@router.get("/price-book")
def price_book(p: Principal = Depends(principal)) -> dict:
    return PRICE_BOOK if p.user.is_staff else {}
