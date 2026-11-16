"""Delivery economics: the year-1 'map everything' data that justifies year-2 automation (100 h -> 65 h)."""
from __future__ import annotations

import statistics
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AIArtifact, AIRun, Control, Engagement, EngagementControl, ReviewDecision, Snapshot, TimeEntry


def engagement_metrics(s: Session, eng: Engagement) -> dict[str, Any]:
    st = get_settings()
    rows = s.execute(select(TimeEntry.role_class, TimeEntry.ai_assisted, func.sum(TimeEntry.minutes))
                     .where(TimeEntry.engagement_id == eng.id)
                     .group_by(TimeEntry.role_class, TimeEntry.ai_assisted)).all()
    eng_min = sum(m for rc, _, m in rows if rc == "engineer")
    exp_min = sum(m for rc, _, m in rows if rc == "expert")
    ai_min = sum(m for _, ai, m in rows if ai)
    total_h = (eng_min + exp_min) / 60
    ai_cost = float(s.execute(select(func.coalesce(func.sum(AIRun.cost_usd), 0.0))
                              .where(AIRun.engagement_id == eng.id)).scalar_one())
    labour = eng_min / 60 * st.engineer_rate_usd + exp_min / 60 * st.expert_rate_usd
    decided = s.execute(select(ReviewDecision.decision, ReviewDecision.review_seconds)
                        .join(AIArtifact, AIArtifact.id == ReviewDecision.artifact_id)
                        .where(AIArtifact.engagement_id == eng.id)).all()
    n = len(decided)
    by_control = s.execute(select(Control.code, Control.est_hours, func.coalesce(func.sum(TimeEntry.minutes), 0))
                           .join(EngagementControl, EngagementControl.control_id == Control.id)
                           .outerjoin(TimeEntry, TimeEntry.engagement_control_id == EngagementControl.id)
                           .where(EngagementControl.engagement_id == eng.id)
                           .group_by(Control.code, Control.est_hours).order_by(Control.code)).all()
    secs = [x for _, x in decided if x]
    return {
        "engagement_id": str(eng.id),
        "hours_total": round(total_h, 1),
        "hours_engineer": round(eng_min / 60, 1),
        "hours_expert": round(exp_min / 60, 1),
        "hours_ai_assisted": round(ai_min / 60, 1),
        "target_hours_year1": st.target_hours_year1,
        "target_hours_year2": st.target_hours_year2,
        "labour_cost_usd": round(labour, 2),
        "ai_cost_usd": round(ai_cost, 4),
        "contract_value_usd": eng.contract_value_usd,
        "contribution_usd": round(eng.contract_value_usd - labour - ai_cost, 2),
        "contribution_margin": round((eng.contract_value_usd - labour - ai_cost) / eng.contract_value_usd, 3)
        if eng.contract_value_usd else None,
        "ai_reviews": n,
        "ai_acceptance_rate": round(sum(1 for d, _ in decided if d == "approve") / n, 3) if n else None,
        "ai_edit_rate": round(sum(1 for d, _ in decided if d == "edit") / n, 3) if n else None,
        "ai_reject_rate": round(sum(1 for d, _ in decided if d == "reject") / n, 3) if n else None,
        "median_review_seconds": statistics.median(secs) if secs else None,
        "hours_by_control": [{"control": c, "estimate_h": e, "actual_h": round(m / 60, 2)} for c, e, m in by_control],
    }


def portfolio(s: Session, tenant_ids: set[uuid.UUID]) -> dict[str, Any]:
    engs = s.execute(select(Engagement).where(Engagement.tenant_id.in_(tenant_ids),
                                              Engagement.service_type != "snapshot")).scalars().all()
    per = [engagement_metrics(s, e) | {"name": e.name, "status": e.status} for e in engs]
    active = [p for p in per if p["hours_total"] > 0]
    snaps = s.execute(select(Snapshot.status, func.count()).where(Snapshot.tenant_id.in_(tenant_ids))
                      .group_by(Snapshot.status)).all()
    return {
        "engagements": per,
        "avg_hours_per_engagement": round(sum(p["hours_total"] for p in active) / len(active), 1) if active else None,
        "total_contract_value_usd": sum(p["contract_value_usd"] for p in per),
        "total_contribution_usd": round(sum(p["contribution_usd"] for p in per), 2),
        "total_ai_cost_usd": round(sum(p["ai_cost_usd"] for p in per), 4),
        "snapshot_funnel": {st: n for st, n in snaps},
        "targets": {"hours_year1": get_settings().target_hours_year1, "hours_year2": get_settings().target_hours_year2},
    }
