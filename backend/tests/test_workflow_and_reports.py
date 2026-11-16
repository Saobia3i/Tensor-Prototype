"""State machines, expert sign-off, append-only records, remediation and client deliverables."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tensor_ops.db import admin_session, new_session

from .conftest import ACCESS_REVIEW, MFA_POLICY


def approved_control(api, tenant="acme"):  # type: ignore[no-untyped-def]
    eng = api.engagement(tenant)
    who = "client_a" if tenant == "acme" else "client_b"
    ev = api.upload(who, eng["id"], f"{uuid.uuid4().hex[:5]}.txt", (uuid.uuid4().hex + " " + MFA_POLICY).encode())
    api.ok("expert", "POST", f"/v1/ai-artifacts/{ev['artifacts'][0]['id']}/review",
           json={"decision": "approve", "expected_version": 1})
    ctl = {c["code"]: c for c in api.ok("lead", "GET", f"/v1/engagements/{eng['id']}/controls")}["TC-AC-02"]
    return eng, ev, ctl


def test_engagement_creates_controls_and_requests_for_frameworks(api):
    soc2 = api.engagement("acme", ["SOC2"])
    both = api.engagement("acme", ["SOC2", "ISO42001"])
    n1 = len(api.ok("lead", "GET", f"/v1/engagements/{soc2['id']}/controls"))
    n2 = len(api.ok("lead", "GET", f"/v1/engagements/{both['id']}/controls"))
    assert n1 >= 25 and n2 == n1 + 4
    reqs = api.ok("client_a", "GET", f"/v1/engagements/{soc2['id']}/requests")
    assert len(reqs) == n1 and all(r["due_date"] for r in reqs)
    ctl = api.ok("lead", "GET", f"/v1/engagements/{soc2['id']}/controls")[0]
    assert ctl["framework_refs"] and all(r.startswith("SOC2 ") for r in ctl["framework_refs"])


def test_illegal_control_transitions_rejected(api):
    eng = api.engagement("acme")
    ctl = api.ok("lead", "GET", f"/v1/engagements/{eng['id']}/controls")[0]
    r = api("expert", "PATCH", f"/v1/engagement-controls/{ctl['id']}", json={"version": 1, "status": "ready_for_signoff"})
    assert r.status_code == 409
    r = api("expert", "PATCH", f"/v1/engagement-controls/{ctl['id']}", json={"version": 1, "status": "signed_off"})
    assert r.status_code == 409
    r = api("expert", "PATCH", f"/v1/engagement-controls/{ctl['id']}", json={"version": 1, "status": "not_applicable"})
    assert r.status_code == 422  # rationale required
    assert api("client_a", "PATCH", f"/v1/engagement-controls/{ctl['id']}",
               json={"version": 1, "status": "in_progress"}).status_code == 403


def test_signoff_requires_evidence_named_signer_and_mfa(api):
    eng, ev, ctl = approved_control(api)
    api.ok("expert", "PATCH", f"/v1/engagement-controls/{ctl['id']}", json={"version": ctl["version"], "status": "ready_for_signoff"})
    assert api("engineer", "POST", f"/v1/engagement-controls/{ctl['id']}/signoff", json={}).status_code == 403  # not a signer role/perm
    assert api("lead", "POST", f"/v1/engagement-controls/{ctl['id']}/signoff", json={}).status_code == 403  # not the named signer
    assert api("expert", "POST", f"/v1/engagement-controls/{ctl['id']}/signoff", json={}, mfa=False).status_code == 403
    so = api.ok("expert", "POST", f"/v1/engagement-controls/{ctl['id']}/signoff", json={})
    assert len(so["content_sha256"]) == 64
    ledger = api.ok("client_a", "GET", f"/v1/engagements/{eng['id']}/trust-ledger")
    assert ledger["signoffs"][0]["signer"].startswith("Nadia") and ledger["named_signer"]["title"]


def test_signoffs_and_audit_events_are_append_only_in_database(api):
    eng, ev, ctl = approved_control(api)
    with new_session(tenant_ids=[api.ids["acme"]]) as s:
        with pytest.raises(DBAPIError):
            s.execute(text("UPDATE audit_events SET action='tampered' WHERE tenant_id=:t"), {"t": api.ids["acme"]})
        s.rollback()
        with pytest.raises(DBAPIError):
            s.execute(text("DELETE FROM review_decisions WHERE tenant_id=:t"), {"t": api.ids["acme"]})
        s.rollback()
    with admin_session() as s:  # even the owner role is blocked by the trigger
        with pytest.raises(DBAPIError):
            s.execute(text("UPDATE audit_events SET action='tampered'"))
        s.rollback()


def test_evidence_access_and_review_are_audited(api):
    eng, ev, ctl = approved_control(api)
    events = api.ok("lead", "GET", "/v1/audit-events", params={"tenant_id": str(api.ids["acme"])})
    actions = {e["action"] for e in events if e["target_id"] in (ev["id"], ev["artifacts"][0]["id"])}
    assert {"evidence.uploaded", "evidence.extracted", "evidence.viewed", "review.approve"} <= actions
    assert all(e["correlation_id"] or e["actor"] in ("worker", "system") for e in events[:20])


def test_engagement_cannot_be_audit_ready_until_all_controls_signed(api):
    eng = api.engagement("acme")
    api.ok("lead", "PATCH", f"/v1/engagements/{eng['id']}", json={"version": 1, "status": "in_progress"})
    r = api("lead", "PATCH", f"/v1/engagements/{eng['id']}", json={"version": 2, "status": "audit_ready"})
    assert r.status_code == 409 and "not signed off" in r.text


def test_remediation_only_experts_verify(api):
    eng = api.engagement("acme")
    ctl = api.ok("lead", "GET", f"/v1/engagements/{eng['id']}/controls")[0]
    t = api.ok("lead", "POST", "/v1/remediation-tasks", json={
        "engagement_id": eng["id"], "title": "Enforce MFA on AWS root", "engagement_control_id": ctl["id"],
        "owner_user_id": str(api.ids["client_a"]), "severity": "high"})
    api.ok("client_a", "PATCH", f"/v1/remediation-tasks/{t['id']}", json={"version": 1, "status": "in_progress"})
    api.ok("client_a", "PATCH", f"/v1/remediation-tasks/{t['id']}", json={"version": 2, "status": "ready_for_review"})
    assert api("client_a", "PATCH", f"/v1/remediation-tasks/{t['id']}",
               json={"version": 3, "status": "verified_closed"}).status_code == 403
    api.ok("engineer", "PATCH", f"/v1/remediation-tasks/{t['id']}", json={"version": 3, "status": "verified_closed"})
    assert api("lead", "PATCH", f"/v1/remediation-tasks/{t['id']}", json={"version": 4, "status": "open"}).status_code == 409
    bad = api("lead", "POST", "/v1/remediation-tasks", json={"engagement_id": eng["id"], "title": "x owner",
                                                             "owner_user_id": str(api.ids["client_b"])})
    assert bad.status_code == 422  # owner must belong to this client


# ------------------------------------------------------------------ reports
def test_reports_use_only_approved_evidence_and_need_independent_approval(api):
    eng, ev, ctl = approved_control(api)
    rejected = api.upload("client_a", eng["id"], "rej.txt", (uuid.uuid4().hex + " " + ACCESS_REVIEW).encode())
    api.ok("expert", "POST", f"/v1/ai-artifacts/{rejected['artifacts'][0]['id']}/review",
           json={"decision": "reject", "comment": "Not signed by an approver", "expected_version": 1})
    r = api.ok("engineer", "POST", f"/v1/engagements/{eng['id']}/reports", json={"kind": "evidence_index"})
    html = api("lead", "GET", f"/v1/reports/{r['id']}").text
    assert ev["title"] in html and rejected["title"] not in html and "DRAFT" in html
    # client cannot see drafts
    assert api("client_a", "GET", f"/v1/reports/{r['id']}").status_code == 403
    assert all(x["status"] == "released" for x in api.ok("client_a", "GET", f"/v1/engagements/{eng['id']}/reports"))
    # author cannot approve own report; another reviewer can
    assert api("engineer", "POST", f"/v1/reports/{r['id']}/approve").status_code == 403  # no approve permission
    lead_r = api.ok("lead", "POST", f"/v1/engagements/{eng['id']}/reports", json={"kind": "evidence_index"})
    assert api("lead", "POST", f"/v1/reports/{lead_r['id']}/approve").status_code == 403  # author
    api.ok("qa", "POST", f"/v1/reports/{r['id']}/approve")
    assert api("lead", "POST", f"/v1/reports/{r['id']}/release", mfa=False).status_code == 403
    api.ok("lead", "POST", f"/v1/reports/{r['id']}/release")
    assert "RELEASED" not in api("client_a", "GET", f"/v1/reports/{r['id']}").text  # released copy has no draft banner
    ledger = api.ok("client_a", "GET", f"/v1/engagements/{eng['id']}/trust-ledger")
    assert ledger["released_reports"] and any(x["subject_type"] == "report" for x in ledger["signoffs"])


def test_report_with_stale_evidence_cannot_be_released(api):
    eng, ev, ctl = approved_control(api)
    r = api.ok("engineer", "POST", f"/v1/engagements/{eng['id']}/reports", json={"kind": "evidence_index"})
    api.ok("qa", "POST", f"/v1/reports/{r['id']}/approve")
    with admin_session() as s:  # evidence expires between approval and release
        s.execute(text("UPDATE evidence_items SET status='expired' WHERE id=:i"), {"i": ev["id"]})
        s.commit()
    rr = api("lead", "POST", f"/v1/reports/{r['id']}/release")
    assert rr.status_code == 409 and "regenerate" in rr.text


def test_gap_and_weekly_reports_label_recommendations(api):
    eng, ev, ctl = approved_control(api)
    gap = api.ok("lead", "POST", f"/v1/engagements/{eng['id']}/reports", json={"kind": "gap_report"})
    html = api("lead", "GET", f"/v1/reports/{gap['id']}").text
    assert "RECOMMENDATION:" in html and "OPEN" in html and "TC-AC-02" not in html.split("Open gaps")[1].split("Open remediation")[0]
    wk = api.ok("lead", "POST", f"/v1/engagements/{eng['id']}/reports", json={"kind": "weekly_status"})
    assert "approved evidence" in api("lead", "GET", f"/v1/reports/{wk['id']}").text


def test_white_label_reports_carry_partner_brand(api):
    eng, ev, ctl = approved_control(api, "globex")
    r = api.ok("lead", "POST", f"/v1/engagements/{eng['id']}/reports", json={"kind": "evidence_index"})
    html = api("partner", "GET", f"/v1/reports/{r['id']}").text
    assert "Northwind Security" in html and "#0f4c81" in html


def test_report_download_link_expires(api):
    from datetime import datetime, timedelta, timezone
    eng, ev, ctl = approved_control(api)
    r = api.ok("lead", "POST", f"/v1/engagements/{eng['id']}/reports", json={"kind": "gap_report"})
    link = api.ok("lead", "POST", f"/v1/reports/{r['id']}/download")["download_url"]
    with admin_session() as s:
        s.execute(text("UPDATE download_tokens SET expires_at=:t"), {"t": datetime.now(timezone.utc) - timedelta(seconds=1)})
        s.commit()
    assert api("lead", "GET", link).status_code == 410
