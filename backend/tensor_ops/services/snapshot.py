"""Free Readiness Snapshot: deterministic gap scoring, a dated 8-week plan and an all-in quote.

Deterministic on purpose: the Snapshot is a sales document with a named signer, so every line must be traceable
to the prospect's own answers and Tensor's control library, not to model judgement.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..content.library import SNAPSHOT_QUESTIONS
from ..models import Control, ControlMapping, Framework, FrameworkRequirement
from .pricing import quote

ANSWER_GAP = {"yes": 0.0, "partial": 0.5, "no": 1.0, "unknown": 1.0}
WEEK_OF_DOMAIN = {
    "Governance": 1, "Risk": 1, "People": 2, "Access": 2, "Assets": 3, "Change": 3, "Infrastructure": 4, "Data": 4,
    "Operations": 5, "Resilience": 6, "Vendors": 6, "Payments": 5, "AI governance": 6,
}
MILESTONES = [
    (1, "Kick-off, scope and system inventory; policy set drafted from Tensor templates"),
    (2, "Identity and access: MFA, SSO, least privilege, joiner/leaver process"),
    (3, "Change management and endpoint security evidence"),
    (4, "Infrastructure, encryption and data handling evidence"),
    (5, "Monitoring, vulnerability management and incident response"),
    (6, "Continuity, vendors, risk assessment; tabletop exercise"),
    (7, "Evidence review and remediation close-out"),
    (8, "Expert sign-off, evidence index and hand-off to the independent auditor"),
]


def business_days_from(start: datetime, n: int) -> datetime:
    d = start
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def next_monday(d: date) -> date:
    return d + timedelta(days=(7 - d.weekday()) % 7 or 7)


def controls_for(s: Session, frameworks: list[str]) -> list[Control]:
    q = (select(Control).join(ControlMapping, ControlMapping.control_id == Control.id)
         .join(FrameworkRequirement, FrameworkRequirement.id == ControlMapping.requirement_id)
         .join(Framework, Framework.id == FrameworkRequirement.framework_id)
         .where(Framework.code.in_(frameworks)).distinct().order_by(Control.code))
    return list(s.execute(q).scalars())


def questions_for(s: Session, frameworks: list[str]) -> list[dict[str, str]]:
    seen, out = set(), []
    for c in controls_for(s, frameworks):
        if c.snapshot_question and c.snapshot_question not in seen:
            seen.add(c.snapshot_question)
            out.append({"key": c.snapshot_question, "question": SNAPSHOT_QUESTIONS[c.snapshot_question],
                        "domain": c.domain})
    return out


def compute(s: Session, *, company: str, frameworks: list[str], employee_count: int | None, intake: dict[str, str],
            founding: bool, pentest: bool, start: date | None, signer_name: str | None, signer_title: str | None,
            partner_price: float | None = None, soc2_type: str = "SOC2_TYPE1") -> dict[str, Any]:
    ctls = controls_for(s, frameworks)
    total_h = sum(c.est_hours for c in ctls) or 1.0
    gaps, gap_h = [], 0.0
    for c in ctls:
        ans = (intake.get(c.snapshot_question or "", "unknown") or "unknown").lower()
        g = ANSWER_GAP.get(ans, 1.0)
        gap_h += g * c.est_hours
        if g > 0:
            gaps.append({"control": c.code, "title": c.title, "domain": c.domain, "answer": ans,
                         "severity": "high" if g == 1.0 else "medium", "what_we_will_do": c.guidance,
                         "evidence_needed": c.evidence_examples, "week": WEEK_OF_DOMAIN.get(c.domain, 6)})
    gaps.sort(key=lambda g: (g["week"], g["severity"] != "high", g["control"]))
    readiness = round(100 * (1 - gap_h / total_h))
    start = start or next_monday(date.today())
    plan = []
    for wk, what in MILESTONES:
        wk_start = start + timedelta(weeks=wk - 1)
        plan.append({"week": wk, "starts": wk_start.isoformat(), "ends": (wk_start + timedelta(days=4)).isoformat(),
                     "focus": what, "gap_controls": [g["control"] for g in gaps if g["week"] == wk]})
    audit_ready = start + timedelta(weeks=8) - timedelta(days=3)
    client_hours = round(3 * 8)  # plan: about three hours a week from one engineer for eight weeks
    return {
        "company": company,
        "frameworks": frameworks,
        "readiness_percent": readiness,
        "controls_in_scope": len(ctls),
        "gaps": gaps,
        "high_gaps": sum(1 for g in gaps if g["severity"] == "high"),
        "plan": plan,
        "start_date": start.isoformat(),
        "audit_ready_target": audit_ready.isoformat(),
        "client_effort": f"About {client_hours} hours from one engineer over 8 weeks (about 3 hours a week).",
        "quote": quote(employee_count, frameworks, founding=founding, pentest=pentest, soc2_type=soc2_type,
                       partner_price=partner_price),
        "signer": {"name": signer_name, "title": signer_title},
        "method": "Scored from the prospect's intake answers against Tensor's control library "
                  "(yes = in place, partial = half gap, no/unknown = full gap), weighted by Tensor's effort estimates.",
        "disclaimer": "This snapshot is a readiness estimate, not an audit opinion. Only an independent audit firm "
                      "can issue a SOC 2 report or ISO certificate.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
