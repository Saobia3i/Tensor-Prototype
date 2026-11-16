"""Plan-specific modules: Readiness Snapshot + pricing, questionnaires, policies, metrics."""
from __future__ import annotations

import uuid

from .conftest import ACCESS_REVIEW, MFA_POLICY, drain

YES_ALL = {"mfa_enforced": "yes", "sso": "yes", "code_review": "yes", "encryption": "yes"}


def snapshot(api, **over):  # type: ignore[no-untyped-def]
    body = {"company_name": f"Prospect {uuid.uuid4().hex[:5]}", "contact_name": "Jane", "contact_email": "jane@prospect.example",
            "employee_count": 30, "frameworks": ["SOC2"], "intake": YES_ALL, "signer_id": str(api.ids["expert"])} | over
    return api.ok("lead", "POST", "/v1/snapshots", json=body)


def test_pricing_follows_the_business_plan():
    from tensor_ops.services.pricing import quote
    assert quote(30, ["SOC2"])["tensor_total_year1"] == 7500
    assert quote(51, ["SOC2"])["tensor_total_year1"] == 10500
    assert quote(150, ["SOC2"])["tensor_total_year1"] == 10500
    assert quote(151, ["SOC2"])["custom_quote_required"] is True
    assert quote(30, ["SOC2"], founding=True)["tensor_total_year1"] == 5000
    assert quote(30, ["SOC2", "ISO27001"])["tensor_total_year1"] == 11500
    assert quote(30, ["SOC2", "ISO42001"], pentest=True)["tensor_total_year1"] == 17500
    assert quote(30, ["SOC2"], partner_price=4500)["tensor_total_year1"] == 4500
    q = quote(30, ["SOC2"])
    assert q["all_in_total_year1"] is None and "to be confirmed" in q["auditor_fee_status"]  # never invent auditor fees


def test_snapshot_draft_sign_send_convert(api):
    sn = snapshot(api, founding_client=True)
    r = sn["result"]
    assert r["quote"]["tensor_total_year1"] == 5000 and r["signer"]["name"].startswith("Nadia")
    assert 0 <= r["readiness_percent"] <= 100 and r["gaps"] and len(r["plan"]) == 8
    assert r["plan"][0]["starts"] < r["audit_ready_target"]
    assert sn["due_at"]  # 3-business-day SLA
    # cannot send unsigned; only the named signer can sign
    assert api("lead", "POST", f"/v1/snapshots/{sn['id']}/status", json={"status": "sent"}).status_code == 409
    assert api("lead", "POST", f"/v1/snapshots/{sn['id']}/sign").status_code == 403
    api.ok("expert", "POST", f"/v1/snapshots/{sn['id']}/sign")
    doc = api("lead", "GET", f"/v1/snapshots/{sn['id']}/document").text
    assert "Signed by Nadia" in doc and "to be confirmed" in doc and "not an audit opinion" in doc
    api.ok("lead", "POST", f"/v1/snapshots/{sn['id']}/status", json={"status": "sent"})
    conv = api.ok("lead", "POST", f"/v1/snapshots/{sn['id']}/convert")
    eng = api.ok("lead", "GET", f"/v1/engagements/{conv['engagement_id']}")
    assert eng["contract_value_usd"] == 5000 and eng["service_type"] == "founding_client"
    assert eng["signer"]["name"].startswith("Nadia") and eng["open_requests"] > 20


def test_snapshot_more_gaps_means_lower_readiness(api):
    good = snapshot(api, intake={k: "yes" for k in ["has_security_policy", "mfa_enforced", "code_review", "sso",
                                                     "least_privilege", "access_reviews", "encryption", "backups_tested"]})
    bad = snapshot(api, intake={})
    assert good["result"]["readiness_percent"] > bad["result"]["readiness_percent"]
    assert bad["result"]["high_gaps"] > good["result"]["high_gaps"]


def test_snapshot_validation_and_white_label_price(api):
    r = api("lead", "POST", "/v1/snapshots", json={"company_name": "X Co", "contact_name": "a", "contact_email": "a@x.example",
                                                   "employee_count": 10, "frameworks": ["SOC2"], "intake": {"mfa_enforced": "maybe"},
                                                   "signer_id": str(api.ids["expert"])})
    assert r.status_code == 422
    assert api("lead", "POST", "/v1/snapshots", json={"company_name": "X Co", "contact_name": "a", "contact_email": "a@x.example",
                                                      "employee_count": 10, "frameworks": ["SOC2"], "intake": {},
                                                      "signer_id": str(api.ids["engineer"])}).status_code == 422
    partners = api.ok("lead", "GET", "/v1/partners")
    sn = snapshot(api, partner_id=partners[0]["id"])
    assert sn["result"]["quote"]["tensor_total_year1"] == 4500
    assert "Northwind" in api("lead", "GET", f"/v1/snapshots/{sn['id']}/document").text
    assert api("client_a", "POST", "/v1/snapshots", json={}).status_code in (403, 422)


def test_snapshot_questions_cover_selected_frameworks(api):
    soc2 = api.ok("lead", "GET", "/v1/snapshot-questions", params={"frameworks": "SOC2"})
    ai = api.ok("lead", "GET", "/v1/snapshot-questions", params={"frameworks": "SOC2,ISO42001"})
    assert {q["key"] for q in ai} - {q["key"] for q in soc2} == {"ai_policy", "ai_impact_assessment",
                                                                  "ai_data_governance", "ai_monitoring"}


# ------------------------------------------------------------------ questionnaires
def _approved_evidence(api, eng, text, name):  # type: ignore[no-untyped-def]
    ev = api.upload("client_a", eng["id"], name, text.encode())
    api.ok("expert", "POST", f"/v1/ai-artifacts/{ev['artifacts'][0]['id']}/review",
           json={"decision": "approve", "expected_version": 1})
    return ev


def test_questionnaire_grounded_in_approved_evidence_only(api):
    eng = api.engagement("acme")
    _approved_evidence(api, eng, MFA_POLICY, "mfa.txt")
    unapproved = api.upload("client_a", eng["id"], "pending.txt",
                            b"Disaster recovery restore test performed 2026-05-01 with full database restore.")
    assert unapproved["status"] == "proposed"
    qn = api.ok("lead", "POST", f"/v1/engagements/{eng['id']}/questionnaires", json={
        "name": "BigCo vendor review", "requester": "BigCo",
        "questions": ["Question", "Is multi-factor authentication enforced for workforce accounts?",
                      "Do you test disaster recovery restore of the database?"]})
    api.ok("lead", "POST", f"/v1/questionnaires/{qn['id']}/draft")
    drain()
    q = api.ok("lead", "GET", f"/v1/questionnaires/{qn['id']}")
    mfa, dr = q["items"]
    assert len(q["items"]) == 2  # header row dropped
    assert mfa["status"] == "drafted" and mfa["artifact"]["output"]["citations"]
    assert dr["status"] == "needs_info"  # only unapproved evidence covers it -> not used
    assert dr["artifact"]["output"]["follow_up_question"]
    # export blocked until every answer is approved and signed
    assert api("lead", "GET", f"/v1/questionnaires/{qn['id']}/export").status_code == 409
    api.ok("expert", "POST", f"/v1/ai-artifacts/{mfa['artifact']['id']}/review", json={"decision": "approve", "expected_version": 1})
    assert api("expert", "POST", f"/v1/questionnaires/{qn['id']}/approve").status_code == 409
    api.ok("expert", "POST", f"/v1/questionnaire-items/{dr['id']}/answer", json={"answer": "=HYPERLINK(\"x\") Yes, annually."})
    api.ok("expert", "POST", f"/v1/questionnaires/{qn['id']}/approve")
    csv_text = api("client_a", "GET", f"/v1/questionnaires/{qn['id']}/export").text
    assert "'=HYPERLINK" in csv_text  # formula injection neutralised


def test_questionnaire_never_cites_other_tenants(api):
    acme = api.engagement("acme")
    _approved_evidence(api, acme, ACCESS_REVIEW, "acme-ar.txt")
    globex = api.engagement("globex")
    qn = api.ok("lead", "POST", f"/v1/engagements/{globex['id']}/questionnaires", json={
        "name": "Probe", "questions": ["Do you perform quarterly access reviews of admin accounts?"]})
    api.ok("lead", "POST", f"/v1/questionnaires/{qn['id']}/draft")
    drain()
    item = api.ok("lead", "GET", f"/v1/questionnaires/{qn['id']}")["items"][0]
    assert item["status"] == "needs_info" and item["artifact"]["output"]["citations"] == []


def test_questionnaire_csv_upload(api):
    eng = api.engagement("acme")
    files = {"file": ("q.csv", b"Question\nDo you encrypt data at rest?\nDo you have an incident response plan?\n", "text/csv")}
    qn = api.ok("lead", "POST", f"/v1/engagements/{eng['id']}/questionnaires/upload", data={"name": "CSV import"}, files=files)
    assert len(api.ok("lead", "GET", f"/v1/questionnaires/{qn['id']}")["items"]) == 2


# ------------------------------------------------------------------ policies
def test_policy_template_to_evidence(api):
    eng = api.engagement("acme")
    keys = {t["key"] for t in api.ok("lead", "GET", "/v1/policy-templates")}
    assert {"information-security", "access-control", "incident-response", "ai-policy"} <= keys
    p = api.ok("lead", "POST", f"/v1/engagements/{eng['id']}/policies", json={
        "template_key": "access-control", "parameters": {"security_owner": "Jane CTO"}})
    assert "identity_provider" in p["missing_parameters"] and "TO CONFIRM" in p["body_markdown"]
    assert "Acme Analytics" in p["body_markdown"]
    assert api("client_a", "POST", f"/v1/policies/{p['id']}/client-approve").status_code == 409
    body = p["body_markdown"].replace("[IDENTITY PROVIDER — TO CONFIRM]", "Google Workspace") \
        .replace("[CLOUD PROVIDER — TO CONFIRM]", "AWS").replace("[CODE HOST — TO CONFIRM]", "GitHub") \
        .replace("[TICKETING TOOL — TO CONFIRM]", "Linear")
    p = api.ok("lead", "PATCH", f"/v1/policies/{p['id']}", json={"version": 1, "body_markdown": body})
    assert api("lead", "POST", f"/v1/policies/{p['id']}/publish").status_code == 409  # client must approve first
    assert api("lead", "POST", f"/v1/policies/{p['id']}/client-approve").status_code == 403  # staff cannot approve for client
    api.ok("client_a", "POST", f"/v1/policies/{p['id']}/client-approve")
    pub = api.ok("lead", "POST", f"/v1/policies/{p['id']}/publish")
    drain()
    ev = api.ok("lead", "GET", f"/v1/evidence/{pub['evidence_id']}")
    assert ev["source"] == "policy" and ev["status"] == "proposed"
    assert "TC-AC-01" in {c["control_code"] for c in ev["artifacts"][0]["output"]["candidate_controls"]}
    assert api("lead", "PATCH", f"/v1/policies/{p['id']}", json={"version": 2, "body_markdown": body + " x"}).status_code == 409


def test_policy_template_key_cannot_traverse(api):
    eng = api.engagement("acme")
    assert api("lead", "POST", f"/v1/engagements/{eng['id']}/policies",
               json={"template_key": "../../etc/passwd"}).status_code == 422


# ------------------------------------------------------------------ metrics
def test_delivery_metrics_track_hours_cost_and_ai_acceptance(api):
    eng = api.engagement("acme", value=7500)
    _approved_evidence(api, eng, MFA_POLICY + " metrics", "m.txt")
    ctl = api.ok("lead", "GET", f"/v1/engagements/{eng['id']}/controls")[0]
    api.ok("engineer", "POST", "/v1/time-entries", json={"engagement_id": eng["id"], "minutes": 600,
                                                         "activity": "evidence_collection", "ai_assisted": True,
                                                         "engagement_control_id": ctl["id"]})
    api.ok("expert", "POST", "/v1/time-entries", json={"engagement_id": eng["id"], "minutes": 120, "activity": "review"})
    assert api("client_a", "POST", "/v1/time-entries", json={"engagement_id": eng["id"], "minutes": 5,
                                                             "activity": "other"}).status_code == 403
    m = api.ok("lead", "GET", f"/v1/engagements/{eng['id']}/metrics")
    assert m["hours_engineer"] == 10 and m["hours_expert"] == 2 and m["hours_ai_assisted"] == 10
    assert m["labour_cost_usd"] == 10 * 9 + 2 * 75
    assert m["ai_acceptance_rate"] == 1.0 and m["contribution_usd"] < 7500
    assert m["target_hours_year1"] == 100 and m["target_hours_year2"] == 65
    pf = api.ok("lead", "GET", "/v1/metrics/portfolio")
    assert pf["engagements"] and "snapshot_funnel" in pf
    assert api("client_a", "GET", f"/v1/engagements/{eng['id']}/metrics").status_code == 403
