"""Engagement analytics: audit-readiness score, effectiveness distribution, criterion/theme radar, owners,
risk exposure, exceptions, milestones and monthly trend. Everything derives from records (no AI)."""
from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AuditException,
    Control,
    ControlMapping,
    ControlTest,
    Engagement,
    EngagementControl,
    EvidenceControlLink,
    EvidenceItem,
    Framework,
    FrameworkRequirement,
    Milestone,
    RemediationTask,
    SignOff,
)

TYPE2_WEIGHTS = {"implementation": 0.30, "testing_coverage": 0.25, "operating_effectiveness": 0.25,
                 "evidence_completeness": 0.10, "remediation_health": 0.10}
TYPE1_WEIGHTS = {"implementation": 0.40, "evidence_completeness": 0.30, "expert_signoff": 0.20, "remediation_health": 0.10}
LABELS = {"implementation": "Control implementation", "testing_coverage": "Testing coverage",
          "operating_effectiveness": "Operating effectiveness", "evidence_completeness": "Evidence completeness",
          "remediation_health": "Exception & remediation health", "expert_signoff": "Expert sign-off"}
STATUSES = ["effective", "needs_attention", "ineffective", "not_tested", "not_implemented", "not_applicable"]
IMPLEMENTED = {"evidence_collected", "ready_for_signoff", "signed_off"}
SOC2_GROUPS = [("CC", "Security"), ("A1", "Availability"), ("PI", "Processing Integrity"), ("C1", "Confidentiality"), ("P", "Privacy")]
ISO_THEMES = {"A.5": "Organizational (A.5)", "A.6": "People (A.6)", "A.7": "Physical (A.7)", "A.8": "Technological (A.8)"}
AI_THEMES = {"A.2": "AI policies", "A.3": "AI organization", "A.4": "AI resources", "A.5": "AI impact", "A.6": "AI lifecycle",
             "A.7": "AI data", "A.8": "Information for parties", "A.9": "AI use", "A.10": "Third parties"}


def group_of(fw: str, ref: str) -> str:
    if fw == "SOC2":
        return next((g for p, g in SOC2_GROUPS if ref.startswith(p)), "Security")
    if fw == "ISO27001":
        return next((g for p, g in ISO_THEMES.items() if ref.startswith(p + ".")), "Clauses 4–10")
    if fw == "ISO42001":
        parts = ref.split(".")
        return AI_THEMES.get(".".join(parts[:2]), "AI governance")
    if fw == "PCIDSS":
        return f"PCI Req. {ref.split('.')[0]}"
    return fw


def rating(score: float) -> str:
    return "Ready" if score >= 90 else "At risk" if score >= 70 else "Critical"


def _pct(a: float, b: float) -> float | None:
    return round(100 * a / b, 1) if b else None


def engagement_analytics(s: Session, eng: Engagement, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    rows = s.execute(select(EngagementControl, Control).join(Control, Control.id == EngagementControl.control_id)
                     .where(EngagementControl.engagement_id == eng.id).order_by(Control.code)).all()
    refs: dict[uuid.UUID, list[tuple[str, str]]] = defaultdict(list)
    for cid, fw, ref in s.execute(select(ControlMapping.control_id, Framework.code, FrameworkRequirement.ref)
                                  .join(FrameworkRequirement, FrameworkRequirement.id == ControlMapping.requirement_id)
                                  .join(Framework, Framework.id == FrameworkRequirement.framework_id)
                                  .where(Framework.code.in_(eng.frameworks))).all():
        refs[cid].append((fw, ref))
    approved: dict[uuid.UUID, list[date]] = defaultdict(list)
    for ec_id, upd in s.execute(select(EvidenceControlLink.engagement_control_id, EvidenceControlLink.updated_at)
                                .join(EvidenceItem, EvidenceItem.id == EvidenceControlLink.evidence_id)
                                .where(EvidenceItem.engagement_id == eng.id, EvidenceItem.status == "approved",
                                       EvidenceControlLink.status == "approved")).all():
        approved[ec_id].append(upd.date() if upd else today)
    tests = s.execute(select(ControlTest).where(ControlTest.engagement_id == eng.id)
                      .order_by(ControlTest.test_date, ControlTest.created_at)).scalars().all()
    latest: dict[uuid.UUID, ControlTest] = {}
    for t in tests:
        latest[t.engagement_control_id] = t
    exceptions = s.execute(select(AuditException).where(AuditException.engagement_id == eng.id)
                           .order_by(AuditException.opened_on)).scalars().all()
    open_exc = [x for x in exceptions if x.status == "open"]
    tasks = s.execute(select(RemediationTask).where(RemediationTask.engagement_id == eng.id)).scalars().all()
    signed = {so.subject_id for so in s.execute(select(SignOff).where(
        SignOff.engagement_id == eng.id, SignOff.subject_type == "engagement_control")).scalars()}
    type2 = bool(tests)

    ctl_rows: list[dict[str, Any]] = []
    for ec, c in rows:
        t = latest.get(ec.id)
        implemented = ec.status in IMPLEMENTED or bool(approved.get(ec.id))
        if ec.status == "not_applicable":
            st = "not_applicable"
        elif t and t.result == "fail":
            st = "ineffective"
        elif implemented and type2:
            st = "not_tested" if t is None else ("effective" if t.result == "pass" else "needs_attention")
        elif implemented:
            st = "effective" if ec.id in signed else "needs_attention"  # readiness: signed by the expert = effective
        elif t is not None:
            st = "needs_attention"  # tested but no approved evidence on file
        else:
            st = "not_implemented"
        groups = sorted({group_of(fw, ref) for fw, ref in refs.get(c.id, [])})
        ctl_rows.append({
            "id": str(ec.id), "code": c.code, "title": c.title, "domain": c.domain, "status": st, "workflow_status": ec.status,
            "groups": groups, "refs": sorted(f"{fw} {ref}" for fw, ref in refs.get(c.id, [])),
            "owner": ec.owner_name or "Unassigned", "risk": ec.risk_rating, "self_assessment": ec.self_assessment,
            "approved_evidence": len(approved.get(ec.id, [])), "signed_off": ec.id in signed,
            "last_test": {"date": t.test_date.isoformat(), "result": t.result, "samples": t.samples_tested,
                          "deviations": t.deviations} if t else None,
            "open_exceptions": sum(1 for x in open_exc if x.engagement_control_id == ec.id),
        })
    scope = [r for r in ctl_rows if r["status"] != "not_applicable"]
    n = len(scope)
    impl = sum(1 for r in scope if r["approved_evidence"] or r["workflow_status"] in IMPLEMENTED)
    with_ev = sum(1 for r in scope if r["approved_evidence"])
    tested = [r for r in scope if r["last_test"]]
    eff_points = sum(1.0 if r["last_test"]["result"] == "pass" else 0.5 if r["last_test"]["result"] == "pass_with_exception"
                     else 0.0 for r in tested)
    overdue_exc = [x for x in open_exc if x.target_date and x.target_date < today]
    open_tasks = [t for t in tasks if t.status not in ("verified_closed", "cancelled")]
    overdue_tasks = [t for t in open_tasks if t.due_date and t.due_date < today]
    items = len(exceptions) + len(tasks)
    burden = len(overdue_exc) + len(overdue_tasks) + 0.5 * (len(open_exc) - len(overdue_exc) + len(open_tasks) - len(overdue_tasks))
    comp = {
        "implementation": _pct(impl, n) or 0.0,
        "evidence_completeness": _pct(with_ev, n) or 0.0,
        "remediation_health": round(100 * (1 - burden / items), 1) if items else 100.0,
    }
    if type2:
        comp["testing_coverage"] = _pct(len(tested), n) or 0.0
        comp["operating_effectiveness"] = _pct(eff_points, len(tested)) or 0.0
        weights = TYPE2_WEIGHTS
    else:
        comp["expert_signoff"] = _pct(sum(1 for r in scope if r["signed_off"]), n) or 0.0
        weights = TYPE1_WEIGHTS
    score = round(sum(comp[k] * w for k, w in weights.items()), 1)

    dist = Counter(r["status"] for r in ctl_rows)
    group_stats: dict[str, dict[str, int]] = defaultdict(lambda: Counter())  # type: ignore[assignment]
    for r in scope:
        for g in r["groups"] or ["Other"]:
            gs = group_stats[g]
            gs["controls"] += 1
            gs["implemented"] += 1 if (r["approved_evidence"] or r["workflow_status"] in IMPLEMENTED) else 0
            gs["effective"] += 1 if r["status"] == "effective" else 0
    radar = [{"group": g, "controls": v["controls"], "implemented_pct": _pct(v["implemented"], v["controls"]),
              "effective_pct": _pct(v["effective"], v["controls"]),
              "status": rating(100 * v["implemented"] / v["controls"]) if v["controls"] else "—"}
             for g, v in sorted(group_stats.items())]
    owners: dict[str, Counter] = defaultdict(Counter)
    for r in scope:
        o = owners[r["owner"]]
        o["controls"] += 1
        o["ineffective"] += r["status"] == "ineffective"
        o["not_tested"] += r["status"] in ("not_tested",)
        o["evidence_gaps"] += 0 if r["approved_evidence"] else 1
        o["open_exceptions"] += r["open_exceptions"]
    owner_rows = sorted(({"owner": k, **v, "total_open": v["ineffective"] + v["not_tested"] + v["evidence_gaps"] + v["open_exceptions"]}
                         for k, v in owners.items()), key=lambda x: -x["total_open"])
    risk: dict[str, Counter] = {k: Counter() for k in ("high", "medium", "low")}
    for r in scope:
        risk.setdefault(r["risk"], Counter())[r["status"]] += 1
    domains: dict[str, Counter] = defaultdict(Counter)
    for r in scope:
        d = domains[r["domain"]]
        d["controls"] += 1
        d["implemented"] += 1 if (r["approved_evidence"] or r["workflow_status"] in IMPLEMENTED) else 0
        d["ineffective"] += r["status"] == "ineffective"
        d["open_exceptions"] += r["open_exceptions"]
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    exc_summary = {sev: {"open": sum(1 for x in open_exc if x.severity == sev),
                         "overdue": sum(1 for x in overdue_exc if x.severity == sev),
                         "closed": sum(1 for x in exceptions if x.severity == sev and x.status != "open")}
                   for sev in ("critical", "high", "medium", "low")}
    top_exc = sorted(open_exc, key=lambda x: (sev_order.get(x.severity, 9), x.opened_on))[:5]
    code_of = {uuid.UUID(r["id"]): r["code"] for r in ctl_rows}
    milestones = s.execute(select(Milestone).where(Milestone.engagement_id == eng.id)
                           .order_by(Milestone.planned_date, Milestone.ordinal)).scalars().all()

    # Monthly trend (last 12 months)
    months = [(today.replace(day=1) - timedelta(days=31 * i)).replace(day=1) for i in range(11, -1, -1)]
    keys = [m.strftime("%Y-%m") for m in months]
    first_impl = sorted(min(v) for v in approved.values() if v)
    ev_by_month = Counter(d.strftime("%Y-%m") for v in approved.values() for d in v)
    tests_by_month = Counter(t.test_date.strftime("%Y-%m") for t in tests)
    exc_by_month = Counter(x.opened_on.strftime("%Y-%m") for x in exceptions)
    trend = []
    for k in keys:
        cum = sum(1 for d in first_impl if d.strftime("%Y-%m") <= k)
        trend.append({"month": k, "controls_implemented_cum": cum, "implemented_pct": _pct(cum, n) or 0.0,
                      "evidence_approvals": ev_by_month.get(k, 0), "tests": tests_by_month.get(k, 0),
                      "exceptions_opened": exc_by_month.get(k, 0)})
    return {
        "engagement_id": str(eng.id), "as_of": today.isoformat(), "mode": "type2" if type2 else "readiness",
        "readiness": {"score": score, "rating": rating(score),
                      "components": [{"key": k, "label": LABELS[k], "weight": w, "score": comp[k]} for k, w in weights.items()]},
        "kpis": {"controls_in_scope": n, "controls_implemented": impl, "implementation_pct": _pct(impl, n),
                 "testing_coverage_pct": _pct(len(tested), n), "controls_effective": dist.get("effective", 0),
                 "operating_effectiveness_pct": _pct(eff_points, len(tested)),
                 "high_risk_ineffective": sum(1 for r in scope if r["risk"] == "high" and r["status"] == "ineffective"),
                 "open_exceptions": len(open_exc), "overdue_exceptions": len(overdue_exc),
                 "avg_days_open": round(sum((today - x.opened_on).days for x in open_exc) / len(open_exc), 1) if open_exc else None,
                 "evidence_completeness_pct": _pct(with_ev, n), "open_remediation": len(open_tasks),
                 "overdue_remediation": len(overdue_tasks)},
        "status_distribution": [{"status": k, "count": dist.get(k, 0), "pct": _pct(dist.get(k, 0), len(ctl_rows))} for k in STATUSES],
        "radar": radar,
        "domains": [{"domain": k, **v, "implemented_pct": _pct(v["implemented"], v["controls"])} for k, v in sorted(domains.items())],
        "owners": owner_rows,
        "risk_exposure": [{"risk": k, **{st: v.get(st, 0) for st in STATUSES}, "total": sum(v.values()),
                           "effective_pct": _pct(v.get("effective", 0), sum(v.values()))} for k, v in risk.items()],
        "exceptions": {"by_severity": exc_summary, "top": [{
            "code": x.code, "control": code_of.get(x.engagement_control_id, "—"), "description": x.description,
            "severity": x.severity, "days_open": (today - x.opened_on).days,
            "days_past_target": max(0, (today - x.target_date).days) if x.target_date else 0,
            "owner": x.owner_name, "target_date": x.target_date.isoformat() if x.target_date else None,
            "audit_impact": x.audit_impact} for x in top_exc]},
        "milestones": [{"id": str(m.id), "title": m.title, "planned_date": m.planned_date.isoformat(),
                        "completed_on": m.completed_on.isoformat() if m.completed_on else None,
                        "days": (m.planned_date - today).days,
                        "status": "done" if m.completed_on else ("late" if m.planned_date < today else "planned")}
                       for m in milestones],
        "trend": trend,
        "controls": ctl_rows,
    }


def gap_summary(a: dict[str, Any]) -> list[str]:
    """Rule-based findings (no AI) for the executive report."""
    out = []
    k = a["kpis"]
    out.append(f"Readiness score {a['readiness']['score']} ({a['readiness']['rating']}). "
               f"{k['controls_implemented']} of {k['controls_in_scope']} in-scope controls are implemented with approved evidence or expert progress.")
    weakest = sorted([r for r in a["radar"] if r["implemented_pct"] is not None], key=lambda r: r["implemented_pct"])[:2]
    for w in weakest:
        out.append(f"Weakest area: {w['group']} at {w['implemented_pct']}% implemented ({w['controls']} controls).")
    if k["high_risk_ineffective"]:
        out.append(f"{k['high_risk_ineffective']} high-risk control(s) failed testing; prioritise remediation before the audit window.")
    if k["overdue_exceptions"]:
        out.append(f"{k['overdue_exceptions']} exception(s) are past their target date and may be reported as deviations.")
    late = [m for m in a["milestones"] if m["status"] == "late"]
    if late:
        out.append(f"Milestone behind plan: {late[0]['title']} (planned {late[0]['planned_date']}).")
    top_owner = a["owners"][0] if a["owners"] else None
    if top_owner and top_owner["total_open"]:
        out.append(f"Most outstanding actions: {top_owner['owner']} ({top_owner['total_open']} open items).")
    return out
