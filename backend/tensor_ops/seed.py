"""Load reference data (frameworks, controls, mappings) and optional synthetic demo data.

Run with the owner role: `python -m tensor_ops.seed [--demo]`. Demo data is synthetic only.
"""
from __future__ import annotations

import sys
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .content.library import CONTROLS, FRAMEWORKS, REQUIREMENTS
from .db import admin_session
from .models import Control, ControlMapping, Framework, FrameworkRequirement, Membership, Partner, Tenant, User


def load_reference(s: Session) -> None:
    fw_by_code: dict[str, Framework] = {}
    for f in FRAMEWORKS:
        fw = s.execute(select(Framework).where(Framework.code == f["code"])).scalar_one_or_none()
        if fw is None:
            fw = Framework(**f)
            s.add(fw)
        else:
            for k, v in f.items():
                setattr(fw, k, v)
        fw_by_code[f["code"]] = fw
    s.flush()
    req_by_key: dict[tuple[str, str], FrameworkRequirement] = {}
    for code, reqs in REQUIREMENTS.items():
        fw = fw_by_code[code]
        existing = {r.ref: r for r in s.execute(select(FrameworkRequirement).where(
            FrameworkRequirement.framework_id == fw.id)).scalars()}
        for ref, para in reqs.items():
            r = existing.get(ref) or FrameworkRequirement(framework_id=fw.id, ref=ref, paraphrase=para)
            r.paraphrase = para
            s.add(r)
            req_by_key[(code, ref)] = r
    s.flush()
    for c in CONTROLS:
        ctl = s.execute(select(Control).where(Control.code == c["code"])).scalar_one_or_none()
        fields = dict(domain=c["domain"], title=c["title"], objective=c["objective"], guidance=c["guidance"],
                      evidence_examples=c["evidence"], keywords=c["keywords"], est_hours=c["hours"],
                      snapshot_question=c["q"])
        if ctl is None:
            ctl = Control(code=c["code"], **fields)
            s.add(ctl)
        else:
            changed = any(getattr(ctl, k) != v for k, v in fields.items())
            for k, v in fields.items():
                setattr(ctl, k, v)
            if changed:
                ctl.version += 1
        s.flush()
        have = {m.requirement_id for m in s.execute(select(ControlMapping).where(
            ControlMapping.control_id == ctl.id)).scalars()}
        for fw_code, refs in c["map"].items():
            for ref in refs:
                req = req_by_key[(fw_code, ref)]
                if req.id not in have:
                    s.add(ControlMapping(control_id=ctl.id, requirement_id=req.id,
                                         rationale=f"Tensor draft mapping of {c['code']} to {fw_code} {ref}"))


def load_demo(s: Session) -> dict[str, uuid.UUID]:
    """Synthetic users and tenants for local development. Never real customer data."""
    if s.execute(select(User).where(User.email == "lead@tensor.example")).scalar_one_or_none():
        return {}
    partner = Partner(name="Northwind vCISO LLC (synthetic)", brand_name="Northwind Security", brand_color="#0f4c81",
                      contact_email="ops@northwind.example")
    s.add(partner)
    s.flush()
    users = {
        "owner": User(email="owner@tensor.example", name="Platform Owner", is_staff=True, is_platform_owner=True),
        "lead": User(email="lead@tensor.example", name="Abrar J. (Delivery Lead)", is_staff=True, can_sign=True,
                     expert_title="Founder & Lead Compliance Expert"),
        "expert": User(email="expert@tensor.example", name="Nadia R. (Compliance Specialist)", is_staff=True,
                       can_sign=True, expert_title="CISA, Senior Compliance Specialist"),
        "engineer": User(email="engineer@tensor.example", name="Rafi H. (Security Engineer)", is_staff=True),
        "qa": User(email="qa@tensor.example", name="Quality Reviewer", is_staff=True, can_sign=True,
                   expert_title="Quality Reviewer"),
        "client_a": User(email="cto@acme.example", name="Acme CTO (client admin)"),
        "client_b": User(email="ceo@globex.example", name="Globex CEO (client admin)"),
        "partner": User(email="vciso@northwind.example", name="Northwind vCISO", partner_id=partner.id),
        "auditor": User(email="auditor@cpa.example", name="Partner Auditor (read-only)"),
    }
    s.add_all(users.values())
    s.flush()
    acme = Tenant(name="Acme Analytics (synthetic)", employee_count=42, industry="B2B SaaS")
    globex = Tenant(name="Globex Payments (synthetic)", employee_count=95, industry="Fintech", partner_id=partner.id)
    tensor = Tenant(name="TensorInfoSec (tenant zero)", employee_count=8, industry="Security services", is_internal=True)
    s.add_all([acme, globex, tensor])
    s.flush()
    for t in (acme, globex, tensor):
        for key, role in [("lead", "delivery_lead"), ("expert", "compliance_specialist"),
                          ("engineer", "security_engineer"), ("qa", "quality_reviewer")]:
            s.add(Membership(user_id=users[key].id, tenant_id=t.id, role=role))
    s.add(Membership(user_id=users["client_a"].id, tenant_id=acme.id, role="client_admin"))
    s.add(Membership(user_id=users["client_b"].id, tenant_id=globex.id, role="client_admin"))
    s.add(Membership(user_id=users["partner"].id, tenant_id=globex.id, role="partner_admin"))
    from datetime import datetime, timezone
    s.add(Membership(user_id=users["auditor"].id, tenant_id=acme.id, role="auditor",
                     expires_at=datetime.now(timezone.utc) + timedelta(days=60)))
    return {k: u.id for k, u in users.items()} | {"acme": acme.id, "globex": globex.id, "tensor": tensor.id}


def main(argv: list[str]) -> None:
    with admin_session() as s:
        load_reference(s)
        if "--demo" in argv:
            ids = load_demo(s)
            s.commit()
            if ids:
                from .services.demo import build_demo_engagements
                build_demo_engagements(ids)
        s.commit()
    print("seed complete")


if __name__ == "__main__":
    main(sys.argv[1:])
