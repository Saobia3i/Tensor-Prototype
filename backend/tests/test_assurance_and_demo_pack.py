"""Type II testing, exceptions, milestones, readiness analytics, spreadsheet import and the demo evidence pack."""
from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import drain

DEMO = Path(__file__).resolve().parents[2] / "demo"
EV = DEMO / "demo_evidence"
IM = DEMO / "imports"
pytestmark = pytest.mark.skipif(not EV.exists(), reason="run demo/generate_demo_pack.py first")


def upload_file(api, who, eng_id, path: Path):  # type: ignore[no-untyped-def]
    return api.upload(who, eng_id, path.name, path.read_bytes(), run=False)


def test_engagement_gets_default_milestones(api):
    eng = api.engagement("acme")
    ms = api.ok("lead", "GET", f"/v1/engagements/{eng['id']}/milestones")
    assert len(ms) == 7 and ms[0]["title"].startswith("Kick-off")
    api.ok("lead", "PATCH", f"/v1/milestones/{ms[0]['id']}", json={"completed": True})
    assert api.ok("lead", "GET", f"/v1/engagements/{eng['id']}/milestones")[0]["status"] == "done"
    assert api("client_a", "PATCH", f"/v1/milestones/{ms[1]['id']}", json={"completed": True}).status_code == 403


def test_full_demo_pack_through_pipeline(api):
    eng = api.engagement("acme", ["SOC2"])
    for f in sorted(EV.iterdir()):
        upload_file(api, "client_a", eng["id"], f)
    drain()
    evs = {e["filename"]: e for e in api.ok("lead", "GET", "/v1/evidence", params={"engagement_id": eng["id"]})}
    assert len(evs) == 16
    bad = {k: (v["status"], v["error"]) for k, v in evs.items() if v["status"] not in ("proposed", "expert_review")}
    assert not bad, bad
    detail = lambda name: api.ok("lead", "GET", f"/v1/evidence/{evs[name]['id']}")  # noqa: E731
    # PDF, DOCX, XLSX, CSV, JSON, TXT, MD all extracted with text
    for name in ("01_Information_Security_Policy_v3.pdf", "05_Change_Management_Procedure.docx",
                 "03_Q3_2026_User_Access_Review.xlsx", "16_Data_Retention_Policy.md"):
        assert detail(name)["chunks"], name
    mfa = detail("02_Okta_MFA_Enforcement_Export.json")["artifacts"][0]["output"]
    assert "TC-AC-02" in {c["control_code"] for c in mfa["candidate_controls"]}
    off = detail("14_Team_Offsite_Agenda.txt")["artifacts"][0]["output"]
    assert off["decision"] == "insufficient_evidence"
    inj = detail("15_Vendor_Contract_with_hidden_instructions.docx")["artifacts"][0]
    assert inj["output"]["suspicious_instructions_detected"] and any("prompt injection" in w for w in inj["warnings"])
    stale = detail("13_Penetration_Test_Report_2023.pdf")["artifacts"][0]
    assert any("stale" in w for w in stale["warnings"])
    # duplicate upload is caught
    dup = upload_file(api, "client_a", eng["id"], EV / "08_AWS_Backup_and_Restore_Test.txt")
    drain()
    assert api.ok("lead", "GET", f"/v1/evidence/{dup['id']}")["status"] == "duplicate"


def test_iso_gap_assessment_import_dry_run_then_apply(api):
    eng = api.engagement("acme", ["ISO27001"])
    f = IM / "ACME_ISO27001_Gap_Assessment.xlsx"
    files = {"file": (f.name, f.read_bytes())}
    log = api.ok("lead", "POST", f"/v1/engagements/{eng['id']}/import", params={"kind": "assessment"}, files=files)
    assert log["sheet"] == "Requirements" and "applied" not in log
    assert log["duplicates"] == ["A.8.5"] and "A.8.99" in log["unmatched"]
    assert log["skipped_not_assessed"] == 1 and any("Maybe" in i["issue"] for i in log["invalid"])
    assert log["controls_affected"] > 5
    before = {c["code"]: c["status"] for c in api.ok("lead", "GET", f"/v1/engagements/{eng['id']}/controls")}
    out = api.ok("lead", "POST", f"/v1/engagements/{eng['id']}/import", params={"kind": "assessment", "apply": "true"},
                 files={"file": (f.name, f.read_bytes())})
    assert out["applied"] and out["remediation_tasks_created"] >= 3
    after = {c["code"]: c["status"] for c in api.ok("lead", "GET", f"/v1/engagements/{eng['id']}/controls")}
    assert before == after  # a self-assessment never changes Tensor's evidence-based status
    a = api.ok("lead", "GET", f"/v1/engagements/{eng['id']}/analytics")
    ctl = {c["code"]: c for c in a["controls"]}
    assert ctl["TC-AC-02"]["self_assessment"] == "partially"  # duplicate rows: weakest answer kept
    assert ctl["TC-AC-02"]["owner"] == "IT Operations Manager" and ctl["TC-AC-02"]["risk"] == "high"
    assert ctl["TC-PH-01"]["self_assessment"] == "na" and ctl["TC-PH-01"]["workflow_status"] != "not_applicable"


def test_client_can_import_self_assessment_but_creates_no_tasks(api):
    eng = api.engagement("acme", ["SOC2"])
    f = IM / "ACME_SOC2_Gap_Assessment.csv"
    out = api.ok("client_a", "POST", f"/v1/engagements/{eng['id']}/import",
                 params={"kind": "assessment", "apply": "true"}, files={"file": (f.name, f.read_bytes())})
    assert out["applied"] and out["remediation_tasks_created"] == 0 and out["columns"]["owner"] == 4
    assert api("client_a", "POST", f"/v1/engagements/{eng['id']}/import", params={"kind": "tests", "apply": "true"},
               files={"file": (f.name, f.read_bytes())}).status_code == 403
    assert api("client_b", "POST", f"/v1/engagements/{eng['id']}/import", params={"kind": "assessment"},
               files={"file": (f.name, f.read_bytes())}).status_code == 404


def test_type2_test_log_creates_exceptions_and_switches_readiness_model(api):
    eng = api.engagement("acme", ["SOC2"])
    pre = api.ok("lead", "GET", f"/v1/engagements/{eng['id']}/analytics")
    assert pre["mode"] == "readiness" and {c["key"] for c in pre["readiness"]["components"]} >= {"expert_signoff"}
    f = IM / "ACME_SOC2_Type2_Test_Log.xlsx"
    out = api.ok("expert", "POST", f"/v1/engagements/{eng['id']}/import", params={"kind": "tests", "apply": "true"},
                 files={"file": (f.name, f.read_bytes())})
    assert out["tests_created"] == 9 and out["exceptions_created"] == 3
    tests = api.ok("lead", "GET", f"/v1/engagements/{eng['id']}/tests")
    assert any(t["control"] == "TC-HR-01" and t["result"] == "pass" for t in tests)  # blank result computed from deviations
    exc = api.ok("lead", "GET", f"/v1/engagements/{eng['id']}/exceptions")
    assert sorted(x["code"] for x in exc) == ["EXC-001", "EXC-002", "EXC-003"]
    assert {x["severity"] for x in exc} <= {"critical", "high", "medium", "low"}
    a = api.ok("lead", "GET", f"/v1/engagements/{eng['id']}/analytics")
    assert a["mode"] == "type2" and abs(sum(c["weight"] for c in a["readiness"]["components"]) - 1) < 1e-9
    assert 0 <= a["readiness"]["score"] <= 100 and a["readiness"]["rating"] in ("Ready", "At risk", "Critical")
    assert sum(d["count"] for d in a["status_distribution"]) == len(a["controls"])
    assert {"Security", "Availability", "Confidentiality"} <= {r["group"] for r in a["radar"]}
    assert len(a["trend"]) == 12 and a["kpis"]["open_exceptions"] == 3
    sev = [x["severity"] for x in a["exceptions"]["top"]]
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    assert sev == sorted(sev, key=order.get)
    ineffective = [c for c in a["controls"] if c["status"] == "ineffective"]
    assert ineffective and all(c["last_test"]["result"] == "fail" for c in ineffective)


def test_exception_closure_rules(api):
    eng = api.engagement("acme", ["SOC2"])
    ctl = {c["code"]: c for c in api.ok("lead", "GET", f"/v1/engagements/{eng['id']}/controls")}["TC-HR-04"]
    r = api.ok("expert", "POST", f"/v1/engagements/{eng['id']}/tests", json={
        "engagement_control_id": ctl["id"], "test_date": "2026-06-13", "samples_tested": 10, "deviations": 4})
    assert r["result"] == "fail" and r["exception"]
    assert api("expert", "POST", f"/v1/engagements/{eng['id']}/tests", json={
        "engagement_control_id": ctl["id"], "test_date": "2026-06-13", "samples_tested": 2, "deviations": 5}).status_code == 422
    x = api.ok("lead", "GET", f"/v1/engagements/{eng['id']}/exceptions")[0]
    assert api("client_a", "PATCH", f"/v1/exceptions/{x['id']}", json={"status": "closed", "comment": "done now"}).status_code == 403
    assert api("lead", "PATCH", f"/v1/exceptions/{x['id']}", json={"status": "closed"}).status_code == 422
    assert api("lead", "PATCH", f"/v1/exceptions/{x['id']}", json={"status": "closed", "comment": "retested"}).status_code == 409
    task = next(t for t in api.ok("lead", "GET", f"/v1/engagements/{eng['id']}/remediation") if "test exception" in t["title"])
    for v, st in ((1, "in_progress"), (2, "ready_for_review"), (3, "verified_closed")):
        api.ok("engineer", "PATCH", f"/v1/remediation-tasks/{task['id']}", json={"version": v, "status": st})
    api.ok("lead", "PATCH", f"/v1/exceptions/{x['id']}", json={"status": "closed", "comment": "Retested 10/10 clean"})
    assert api.ok("lead", "GET", f"/v1/engagements/{eng['id']}/exceptions")[0]["status"] == "closed"


def test_controls_csv_export_and_executive_summary(api):
    eng = api.engagement("acme", ["SOC2"])
    csv_text = api("client_a", "GET", f"/v1/engagements/{eng['id']}/controls.csv").text
    assert csv_text.startswith("Control,Title") and "TC-AC-02" in csv_text
    es = api.ok("client_a", "GET", f"/v1/engagements/{eng['id']}/executive-summary")
    assert es["findings"][0].startswith("Readiness score") and "Not an audit opinion" in es["basis"]
    assert api("client_b", "GET", f"/v1/engagements/{eng['id']}/analytics").status_code == 404
    pr = api.ok("lead", "GET", "/v1/metrics/readiness")
    assert pr and all(0 <= x["score"] <= 100 for x in pr)


def test_owner_and_risk_editable_by_staff(api):
    eng = api.engagement("acme")
    c = api.ok("lead", "GET", f"/v1/engagements/{eng['id']}/controls")[0]
    api.ok("expert", "PATCH", f"/v1/engagement-controls/{c['id']}", json={"version": c["version"], "owner_name": "HR Manager", "risk_rating": "high"})
    assert api("expert", "PATCH", f"/v1/engagement-controls/{c['id']}", json={"version": c["version"] + 1, "risk_rating": "extreme"}).status_code == 422


def test_bad_spreadsheet_is_rejected_cleanly(api):
    eng = api.engagement("acme")
    r = api("lead", "POST", f"/v1/engagements/{eng['id']}/import", params={"kind": "assessment"},
            files={"file": ("x.xlsx", b"PK\x03\x04 not really a workbook")})
    assert r.status_code == 422
    log = api.ok("lead", "POST", f"/v1/engagements/{eng['id']}/import", params={"kind": "assessment"},
                 files={"file": ("x.csv", b"foo,bar\n1,2\n")})
    assert log["errors"]
