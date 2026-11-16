"""Client deliverables built ONLY from approved records. Every statement traces to a record or is labelled."""
from __future__ import annotations

import hashlib
import html
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Control,
    Engagement,
    EngagementControl,
    EvidenceControlLink,
    EvidenceItem,
    EvidenceRequest,
    Partner,
    RemediationTask,
    SignOff,
    Tenant,
    User,
)


def _brand(s: Session, tenant: Tenant) -> dict[str, str]:
    if tenant.partner_id:
        p = s.get(Partner, tenant.partner_id)
        if p:
            return {"name": p.brand_name, "color": p.brand_color, "delivered_by": f"Delivered by {p.brand_name}"}
    return {"name": "TensorInfoSec", "color": "#b42318", "delivered_by": "Delivered by TensorInfoSec"}


def _controls(s: Session, eng: Engagement) -> list[tuple[EngagementControl, Control]]:
    return list(s.execute(select(EngagementControl, Control).join(Control, Control.id == EngagementControl.control_id)
                          .where(EngagementControl.engagement_id == eng.id).order_by(Control.code)).all())


def _approved_evidence(s: Session, eng: Engagement) -> dict[uuid.UUID, list[dict[str, Any]]]:
    rows = s.execute(select(EvidenceControlLink, EvidenceItem)
                     .join(EvidenceItem, EvidenceItem.id == EvidenceControlLink.evidence_id)
                     .where(EvidenceItem.engagement_id == eng.id, EvidenceItem.status == "approved",
                            EvidenceControlLink.status == "approved")).all()
    users = {u.id: u.name for u in s.execute(select(User)).scalars()}
    out: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for ln, ev in rows:
        out.setdefault(ln.engagement_control_id, []).append({
            "evidence_id": str(ev.id), "title": ev.title, "version": ev.version, "sha256": ev.sha256,
            "source": ev.source, "period_start": ev.period_start.isoformat() if ev.period_start else None,
            "period_end": ev.period_end.isoformat() if ev.period_end else None,
            "collected_at": ev.collected_at.isoformat() if ev.collected_at else None,
            "approved_by": users.get(ln.decided_by, "unknown") if ln.decided_by else None,
        })
    return out


def _signoffs(s: Session, eng: Engagement) -> dict[uuid.UUID, dict[str, Any]]:
    out = {}
    for so in s.execute(select(SignOff).where(SignOff.engagement_id == eng.id,
                                              SignOff.subject_type == "engagement_control")
                        .order_by(SignOff.signed_at)).scalars():
        out[so.subject_id] = {"signer": so.signer_name, "title": so.signer_title,
                              "signed_at": so.signed_at.isoformat(), "sha256": so.content_sha256}
    return out


def build(s: Session, eng: Engagement, kind: str) -> dict[str, Any]:
    tenant = s.get(Tenant, eng.tenant_id)
    assert tenant is not None
    ctls = _controls(s, eng)
    ev = _approved_evidence(s, eng)
    sos = _signoffs(s, eng)
    base = {"kind": kind, "engagement": eng.name, "client": tenant.name, "frameworks": eng.frameworks,
            "generated_at": datetime.now(timezone.utc).isoformat(), "brand": _brand(s, tenant),
            "basis": "Built only from evidence approved by a named Tensor reviewer. Items marked RECOMMENDATION or "
                     "OPEN are not findings of fact.",
            "disclaimer": "Readiness deliverable. Not an audit opinion, SOC 2 report or ISO certificate."}
    if kind == "evidence_index":
        base["controls"] = [{
            "control": c.code, "title": c.title, "status": ec.status,
            "evidence": ev.get(ec.id, []), "signoff": sos.get(ec.id),
        } for ec, c in ctls if ec.status != "not_applicable"]
        base["not_applicable"] = [{"control": c.code, "rationale": ec.applicability_rationale}
                                  for ec, c in ctls if ec.status == "not_applicable"]
    elif kind == "gap_report":
        gaps = [{"control": c.code, "title": c.title, "status": ec.status, "label": "OPEN",
                 "recommendation": f"RECOMMENDATION: {c.guidance}", "evidence_examples": c.evidence_examples}
                for ec, c in ctls if ec.status != "not_applicable" and not ev.get(ec.id)]
        tasks = s.execute(select(RemediationTask).where(RemediationTask.engagement_id == eng.id,
                                                        RemediationTask.status.notin_(["verified_closed", "cancelled"]))
                          ).scalars().all()
        base["gaps"] = gaps
        base["open_remediation"] = [{"title": t.title, "status": t.status, "severity": t.severity,
                                     "due_date": t.due_date.isoformat() if t.due_date else None} for t in tasks]
    elif kind == "weekly_status":
        since = datetime.now(timezone.utc) - timedelta(days=7)
        total = sum(1 for ec, _ in ctls if ec.status != "not_applicable")
        with_ev = sum(1 for ec, _ in ctls if ev.get(ec.id))
        signed = sum(1 for ec, _ in ctls if ec.status == "signed_off")
        new_ev = s.execute(select(EvidenceItem).where(EvidenceItem.engagement_id == eng.id,
                                                      EvidenceItem.status == "approved",
                                                      EvidenceItem.updated_at >= since)).scalars().all()
        overdue = s.execute(select(EvidenceRequest).where(EvidenceRequest.engagement_id == eng.id,
                                                          EvidenceRequest.status == "open",
                                                          EvidenceRequest.due_date < date.today())).scalars().all()
        base["progress"] = {"controls_in_scope": total, "controls_with_approved_evidence": with_ev,
                            "controls_signed_off": signed,
                            "percent_evidenced": round(100 * with_ev / total) if total else 0,
                            "target_date": eng.target_date.isoformat() if eng.target_date else None}
        base["approved_this_week"] = [{"title": e.title, "evidence_id": str(e.id)} for e in new_ev]
        base["overdue_requests"] = [{"title": r.title, "due_date": r.due_date.isoformat() if r.due_date else None}
                                    for r in overdue]
        base["next_steps"] = [f"RECOMMENDATION: provide evidence for {c.code} — {c.title}"
                              for ec, c in ctls if ec.status != "not_applicable" and not ev.get(ec.id)][:8]
    else:
        raise ValueError("unknown report kind")
    return base


def referenced_evidence(content: dict[str, Any]) -> dict[str, str]:
    refs: dict[str, str] = {}
    for c in content.get("controls", []):
        for e in c.get("evidence", []):
            refs[e["evidence_id"]] = e["sha256"]
    return refs


def stale_references(s: Session, content: dict[str, Any]) -> list[str]:
    """Evidence cited by a report that is no longer approved (rejected, superseded, expired, deleted)."""
    refs = referenced_evidence(content)
    if not refs:
        return []
    rows = {str(e.id): e for e in s.execute(select(EvidenceItem).where(
        EvidenceItem.id.in_([uuid.UUID(k) for k in refs]))).scalars()}
    return [k for k, sha in refs.items() if k not in rows or rows[k].status != "approved" or rows[k].sha256 != sha]


def digest(content: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()


def render_html(content: dict[str, Any], status: str, sha: str) -> str:
    e = html.escape
    b = content["brand"]
    title = {"evidence_index": "Evidence Index", "gap_report": "Gap Report", "weekly_status": "Weekly Status"}[content["kind"]]
    parts = [f"""<!doctype html><html><head><meta charset="utf-8"><title>{e(title)} — {e(content['client'])}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#1d2433}}
h1{{color:{e(b['color'])}}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{border-bottom:1px solid #ddd;
padding:6px;text-align:left;vertical-align:top}}.draft{{background:#fff4e5;border:1px solid #f0b46d;padding:8px}}
code{{font-size:11px;word-break:break-all}}.muted{{color:#667}}</style></head><body>"""]
    if status != "released":
        parts.append(f'<p class="draft"><b>{e(status.upper())}</b> — not approved for client delivery.</p>')
    parts.append(f"<h1>{e(title)}</h1><p><b>{e(content['client'])}</b> · {e(content['engagement'])} · "
                 f"{e(', '.join(content['frameworks']))}</p><p class='muted'>{e(content['basis'])}</p>")
    if content["kind"] == "evidence_index":
        parts.append("<table><tr><th>Control</th><th>Status</th><th>Approved evidence</th><th>Expert sign-off</th></tr>")
        for c in content["controls"]:
            evs = "<br>".join(f"{e(x['title'])} v{x['version']} <code>{e(x['sha256'] or '')[:16]}…</code>"
                              f" <span class='muted'>approved by {e(x['approved_by'] or '')}</span>" for x in c["evidence"]) \
                or "<span class='muted'>OPEN — no approved evidence</span>"
            so = c["signoff"]
            sot = f"{e(so['signer'])}, {e(so['title'])}<br><span class='muted'>{e(so['signed_at'][:10])}</span>" if so else "—"
            parts.append(f"<tr><td><b>{e(c['control'])}</b> {e(c['title'])}</td><td>{e(c['status'])}</td>"
                         f"<td>{evs}</td><td>{sot}</td></tr>")
        parts.append("</table>")
    elif content["kind"] == "gap_report":
        parts.append(f"<h2>Open gaps ({len(content['gaps'])})</h2><table><tr><th>Control</th><th>Status</th><th>Recommendation</th></tr>")
        for g in content["gaps"]:
            parts.append(f"<tr><td><b>{e(g['control'])}</b> {e(g['title'])}</td><td>{e(g['label'])} · {e(g['status'])}</td>"
                         f"<td>{e(g['recommendation'])}</td></tr>")
        parts.append("</table><h2>Open remediation</h2><ul>")
        parts += [f"<li>{e(t['title'])} — {e(t['status'])}, due {e(t['due_date'] or 'n/a')}</li>" for t in content["open_remediation"]]
        parts.append("</ul>")
    else:
        p = content["progress"]
        parts.append(f"<h2>Progress</h2><p>{p['controls_with_approved_evidence']} of {p['controls_in_scope']} controls have "
                     f"approved evidence ({p['percent_evidenced']}%); {p['controls_signed_off']} signed off. "
                     f"Target audit-ready date: {e(p['target_date'] or 'not set')}.</p><h2>Approved this week</h2><ul>")
        parts += [f"<li>{e(x['title'])}</li>" for x in content["approved_this_week"]] or ["<li>None</li>"]
        parts.append("</ul><h2>Overdue requests</h2><ul>")
        parts += [f"<li>{e(x['title'])} (due {e(x['due_date'] or '')})</li>" for x in content["overdue_requests"]] or ["<li>None</li>"]
        parts.append("</ul><h2>Next steps</h2><ul>")
        parts += [f"<li>{e(x)}</li>" for x in content["next_steps"]]
        parts.append("</ul>")
    parts.append(f"<hr><p class='muted'>{e(b['delivered_by'])} · {e(content['disclaimer'])}<br>Content SHA-256: "
                 f"<code>{e(sha)}</code> · generated {e(content['generated_at'][:19])} UTC</p></body></html>")
    return "".join(parts)
