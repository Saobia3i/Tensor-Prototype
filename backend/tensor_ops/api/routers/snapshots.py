"""Free Readiness Snapshot workflow: intake -> deterministic draft -> named-signer sign-off -> send -> convert."""
from __future__ import annotations

import html
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import audit
from ...models import Membership, Partner, SignOff, Snapshot, Tenant, User
from ...security.authz import Principal, get_scoped
from ...services import snapshot as snap
from ...services.review import create_signoff
from ..deps import get_db, principal
from .engagements import EngagementIn, create_engagement
from .tenants import TenantIn, create_tenant

router = APIRouter(tags=["readiness snapshot"])


@router.get("/snapshot-questions")
def questions(frameworks: str = "SOC2", _: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    return snap.questions_for(s, [f for f in frameworks.split(",") if f])


class SnapshotIn(BaseModel):
    company_name: str = Field(min_length=2, max_length=200)
    contact_name: str = Field(min_length=1, max_length=200)
    contact_email: EmailStr
    employee_count: int = Field(ge=1, le=100000)
    industry: str | None = Field(default=None, max_length=80)
    frameworks: list[str] = Field(min_length=1, max_length=4)
    intake: dict[str, str] = Field(default_factory=dict)
    founding_client: bool = False
    pentest: bool = False
    soc2_type: str = Field(default="SOC2_TYPE1", pattern="^SOC2_TYPE[12]$")
    signer_id: uuid.UUID
    partner_id: uuid.UUID | None = None
    start_date: date | None = None


def _out(sn: Snapshot) -> dict:
    return {"id": str(sn.id), "tenant_id": str(sn.tenant_id), "contact_name": sn.contact_name,
            "contact_email": sn.contact_email, "frameworks": sn.frameworks, "intake": sn.intake, "result": sn.result,
            "status": sn.status, "signer_id": str(sn.signer_id) if sn.signer_id else None,
            "due_at": sn.due_at.isoformat() if sn.due_at else None, "founding_client": sn.founding_client,
            "created_at": sn.created_at.isoformat()}


@router.post("/snapshots", status_code=201)
def create(body: SnapshotIn, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    if not p.user.is_staff:
        raise HTTPException(status_code=403, detail="Forbidden")
    signer = s.get(User, body.signer_id)
    if not signer or not signer.can_sign or not signer.is_staff:
        raise HTTPException(status_code=422, detail="signer must be an authorised Tensor signer")
    bad = {k for k, v in body.intake.items() if v not in snap.ANSWER_GAP}
    if bad:
        raise HTTPException(status_code=422, detail=f"answers must be yes/partial/no/unknown: {sorted(bad)[:3]}")
    t = create_tenant(s, p, TenantIn(name=body.company_name, employee_count=body.employee_count, industry=body.industry,
                                     partner_id=body.partner_id, status="prospect"))
    if signer.id != p.id:
        s.add(Membership(user_id=signer.id, tenant_id=t.id, role="compliance_specialist"))
    partner_price = None
    if body.partner_id:
        partner = s.get(Partner, body.partner_id)
        partner_price = partner.price_per_client_year if partner else None
    result = snap.compute(s, company=body.company_name, frameworks=body.frameworks, employee_count=body.employee_count,
                          intake=body.intake, founding=body.founding_client, pentest=body.pentest, start=body.start_date,
                          signer_name=signer.name, signer_title=signer.expert_title, partner_price=partner_price,
                          soc2_type=body.soc2_type)
    sn = Snapshot(tenant_id=t.id, contact_name=body.contact_name, contact_email=body.contact_email.lower(),
                  frameworks=body.frameworks, intake=body.intake, result=result, status="drafted", signer_id=signer.id,
                  due_at=snap.business_days_from(datetime.now(timezone.utc), 3), founding_client=body.founding_client)
    s.add(sn)
    s.flush()
    audit.record(s, "snapshot.drafted", tenant_id=t.id, actor_id=p.id, target_type="snapshot", target_id=sn.id,
                 readiness=result["readiness_percent"])
    s.commit()
    return _out(sn)


@router.get("/snapshots")
def list_snapshots(p: Principal = Depends(principal), s: Session = Depends(get_db)) -> list[dict]:
    scope = p.tenants_with("snapshot.manage")
    if not scope:
        return []
    rows = s.execute(select(Snapshot, Tenant).join(Tenant, Tenant.id == Snapshot.tenant_id)
                     .where(Snapshot.tenant_id.in_(scope)).order_by(Snapshot.created_at.desc())).all()
    return [_out(sn) | {"company": t.name} for sn, t in rows]


@router.get("/snapshots/{sid}")
def get_snapshot(sid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    sn = get_scoped(s, p, Snapshot, sid, "snapshot.manage")
    so = s.execute(select(SignOff).where(SignOff.subject_id == sn.id)).scalars().first()
    return _out(sn) | {"signoff": {"signer": so.signer_name, "title": so.signer_title, "sha256": so.content_sha256,
                                   "signed_at": so.signed_at.isoformat()} if so else None}


@router.post("/snapshots/{sid}/sign")
def sign(sid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    sn = get_scoped(s, p, Snapshot, sid, "signoff.create")
    if sn.status != "drafted":
        raise HTTPException(status_code=409, detail=f"snapshot is {sn.status}")
    if sn.signer_id != p.id:
        raise HTTPException(status_code=403, detail="only the named signer can sign this snapshot")
    so = create_signoff(s, tenant_id=sn.tenant_id, engagement_id=None, subject_type="snapshot", subject_id=sn.id,
                        signer=p.user, statement="I have reviewed this readiness snapshot and the plan and quote in it.",
                        content=sn.result)
    sn.status = "signed"
    s.commit()
    return {"status": sn.status, "sha256": so.content_sha256}


class StatusIn(BaseModel):
    status: str = Field(pattern="^(sent|lost)$")


@router.post("/snapshots/{sid}/status")
def set_status(sid: uuid.UUID, body: StatusIn, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    sn = get_scoped(s, p, Snapshot, sid, "snapshot.manage")
    if body.status == "sent" and sn.status != "signed":
        raise HTTPException(status_code=409, detail="a snapshot must be signed by its named expert before it is sent")
    sn.status = body.status
    audit.record(s, f"snapshot.{body.status}", tenant_id=sn.tenant_id, actor_id=p.id, target_type="snapshot", target_id=sn.id)
    s.commit()
    return {"status": sn.status}


@router.post("/snapshots/{sid}/convert", status_code=201)
def convert(sid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> dict:
    """Prospect signs: activate the tenant and open the Audit-Ready engagement with the quoted value."""
    sn = get_scoped(s, p, Snapshot, sid, "engagement.manage")
    if sn.status not in ("signed", "sent"):
        raise HTTPException(status_code=409, detail="only signed snapshots can be converted")
    t = s.get(Tenant, sn.tenant_id)
    assert t is not None and sn.result is not None
    t.status = "active"
    q = sn.result["quote"]
    e = create_engagement(s, p, EngagementIn(
        tenant_id=t.id, name=f"{'/'.join(sn.frameworks)} Audit-Ready Year",
        service_type="founding_client" if sn.founding_client else ("white_label" if t.partner_id else "audit_ready_year"),
        frameworks=sn.frameworks, start_date=date.fromisoformat(sn.result["start_date"]),
        target_date=date.fromisoformat(sn.result["audit_ready_target"]), signer_id=sn.signer_id,
        contract_value_usd=q["tensor_total_year1"] or 0))
    sn.status = "won"
    audit.record(s, "snapshot.converted", tenant_id=t.id, actor_id=p.id, target_type="engagement", target_id=e.id)
    s.commit()
    return {"engagement_id": str(e.id)}


@router.get("/snapshots/{sid}/document", response_class=HTMLResponse)
def document(sid: uuid.UUID, p: Principal = Depends(principal), s: Session = Depends(get_db)) -> HTMLResponse:
    sn = get_scoped(s, p, Snapshot, sid, "snapshot.manage")
    r = sn.result or {}
    so = s.execute(select(SignOff).where(SignOff.subject_id == sn.id)).scalars().first()
    t = s.get(Tenant, sn.tenant_id)
    brand, color = "TensorInfoSec", "#b42318"
    if t and t.partner_id:
        pt = s.get(Partner, t.partner_id)
        if pt:
            brand, color = pt.brand_name, pt.brand_color
    e = html.escape
    q = r.get("quote", {})
    money = lambda v: "to be confirmed" if v is None else f"${v:,.0f}"  # noqa: E731
    rows = "".join(f"<tr><td>{e(g['control'])}</td><td>{e(g['title'])}</td><td>{e(g['answer'])}</td>"
                   f"<td>{e(g['severity'])}</td><td>Week {g['week']}</td></tr>" for g in r.get("gaps", []))
    plan = "".join(f"<tr><td>Week {w['week']}</td><td>{e(w['starts'])}</td><td>{e(w['focus'])}</td></tr>" for w in r.get("plan", []))
    lines = "".join(f"<tr><td>{e(x['item'])}</td><td style='text-align:right'>{money(x['amount'])}</td></tr>" for x in q.get("lines", []))
    signed = (f"<p><b>Signed by {e(so.signer_name)}</b>, {e(so.signer_title)} on {e(so.signed_at.date().isoformat())}"
              f"<br><code>SHA-256 {e(so.content_sha256)}</code></p>") if so else \
        f"<p style='background:#fff4e5;padding:8px'><b>DRAFT</b> — to be signed by {e((r.get('signer') or {}).get('name') or '')}</p>"
    body = f"""<!doctype html><html><head><meta charset="utf-8"><title>Readiness Snapshot — {e(r.get('company', ''))}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#1d2433}}h1{{color:{e(color)}}}
table{{border-collapse:collapse;width:100%;font-size:14px;margin-bottom:1rem}}td,th{{border-bottom:1px solid #ddd;padding:6px;text-align:left}}
.big{{font-size:44px;font-weight:700;color:{e(color)}}}</style></head><body>
<p>{e(brand)} · Readiness Snapshot</p><h1>{e(r.get('company', ''))}</h1>
<p>Frameworks: {e(', '.join(r.get('frameworks', [])))} · Controls in scope: {r.get('controls_in_scope')}</p>
<p class="big">{r.get('readiness_percent')}% ready</p><p>{r.get('high_gaps')} high-priority gaps · Audit-ready target
<b>{e(r.get('audit_ready_target', ''))}</b> · {e(r.get('client_effort', ''))}</p>{signed}
<h2>Gaps</h2><table><tr><th>Control</th><th>Gap</th><th>Your answer</th><th>Priority</th><th>Planned</th></tr>{rows}</table>
<h2>Dated 8-week plan</h2><table>{plan}</table>
<h2>All-in quote (year 1)</h2><table>{lines}<tr><td><b>Tensor total</b></td><td style='text-align:right'><b>{money(q.get('tensor_total_year1'))}</b></td></tr>
<tr><td>Independent auditor fee ({e(q.get('auditor_fee_status', ''))})</td><td style='text-align:right'>{money(sum(v for v in (q.get('auditor_fee_estimates') or {}).values() if v) if q.get('all_in_total_year1') else None)}</td></tr>
<tr><td><b>All-in total</b></td><td style='text-align:right'><b>{money(q.get('all_in_total_year1'))}</b></td></tr></table>
<p>{e(q.get('payment_terms', ''))}<br>{e(q.get('guarantee', ''))}<br>{e(q.get('note', ''))}</p>
<p style="color:#667;font-size:13px">{e(r.get('method', ''))}<br>{e(r.get('disclaimer', ''))}</p></body></html>"""
    return HTMLResponse(body, headers={"content-security-policy": "default-src 'none'; style-src 'unsafe-inline'"})
