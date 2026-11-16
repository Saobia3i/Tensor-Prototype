"""Phase 1 acceptance: tenant A cannot read/list/download tenant B records by changing IDs or parameters."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from tensor_ops.ai.tasks import review_evidence
from tensor_ops.db import admin_session, new_session
from tensor_ops.models import EvidenceItem, Membership

from .conftest import MFA_POLICY, drain


def test_cross_tenant_idor_returns_404(api):
    eng = api.engagement("acme")
    ev = api.upload("client_a", eng["id"], "mfa.txt", MFA_POLICY.encode())
    # Globex client admin probes Acme objects by ID
    for path in (f"/v1/engagements/{eng['id']}", f"/v1/evidence/{ev['id']}", f"/v1/engagements/{eng['id']}/controls",
                 f"/v1/engagements/{eng['id']}/requests", f"/v1/engagements/{eng['id']}/trust-ledger",
                 f"/v1/tenants/{api.ids['acme']}", f"/v1/engagements/{eng['id']}/reports"):
        r = api("client_b", "GET", path)
        assert r.status_code == 404, (path, r.status_code, r.text)
    assert api("client_b", "POST", f"/v1/evidence/{ev['id']}/download").status_code == 404
    assert api("client_b", "POST", f"/v1/evidence/{ev['id']}/ai-review").status_code == 404
    assert api("client_b", "GET", "/v1/audit-events", params={"tenant_id": str(api.ids["acme"])}).status_code == 404


def test_list_endpoints_never_include_other_tenants(api):
    api.engagement("acme")
    engs = api.ok("client_b", "GET", "/v1/engagements")
    assert engs and all(e["tenant_id"] == str(api.ids["globex"]) for e in engs) or engs == []
    tenants = api.ok("client_b", "GET", "/v1/tenants")
    assert {t["id"] for t in tenants} == {str(api.ids["globex"])}
    # explicitly asking for another tenant is refused, not silently filtered
    assert api("client_b", "GET", "/v1/engagements", params={"tenant_id": str(api.ids["acme"])}).status_code == 404


def test_client_supplied_tenant_or_engagement_ids_are_not_trusted(api):
    eng = api.engagement("acme")
    r = api("client_b", "POST", "/v1/evidence/upload-sessions", json={
        "engagement_id": eng["id"], "title": "xx", "filename": "x.txt", "size_bytes": 3})
    assert r.status_code == 404
    r = api("client_b", "POST", "/v1/engagements", json={"tenant_id": str(api.ids["acme"]), "name": "evil",
                                                         "frameworks": ["SOC2"]})
    assert r.status_code == 404


def test_rls_blocks_rows_even_if_application_filter_is_missing(api):
    eng = api.engagement("acme")
    ev = api.upload("client_a", eng["id"], "rls.txt", ("RLS check " + MFA_POLICY).encode())
    with new_session(tenant_ids=[api.ids["globex"]]) as s:
        assert s.get(EvidenceItem, uuid.UUID(ev["id"])) is None
        tenants = {r[0] for r in s.execute(text("select distinct tenant_id from evidence_items"))}
        assert api.ids["acme"] not in tenants
        assert s.execute(text("select count(*) from evidence_chunks where tenant_id = :t"),
                         {"t": api.ids["acme"]}).scalar_one() == 0
    with new_session(tenant_ids=[]) as s:  # no scope at all -> no tenant rows
        assert s.execute(text("select count(*) from tenants")).scalar_one() == 0


def test_rls_rejects_cross_tenant_writes(api):
    import pytest
    from sqlalchemy.exc import ProgrammingError
    eng = api.engagement("acme")
    with new_session(tenant_ids=[api.ids["globex"]]) as s:
        s.add(EvidenceItem(tenant_id=api.ids["acme"], engagement_id=uuid.UUID(eng["id"]), title="x",
                           original_filename="x", object_key="k"))
        with pytest.raises(ProgrammingError):
            s.flush()


def test_background_job_scoped_to_its_tenant_cannot_read_other_tenant(api):
    eng = api.engagement("acme")
    ev = api.upload("client_a", eng["id"], "job.txt", ("job scope " + MFA_POLICY).encode(), run=True)
    with new_session(tenant_ids=[api.ids["globex"]]) as s:
        assert review_evidence(s, uuid.UUID(ev["id"]))["status"] == "not_found"


def test_auditor_sees_only_approved_evidence_and_expiry_is_enforced(api):
    eng = api.engagement("acme")
    ev = api.upload("client_a", eng["id"], "pending.txt", ("pending " + MFA_POLICY).encode())
    assert ev["status"] == "proposed"
    assert api("auditor", "GET", f"/v1/evidence/{ev['id']}").status_code == 403
    listed = api.ok("auditor", "GET", "/v1/evidence", params={"engagement_id": eng["id"]})
    assert all(x["status"] == "approved" for x in listed)
    # expire the auditor's membership -> no access at all
    with admin_session() as s:
        m = s.execute(select(Membership).where(Membership.user_id == api.ids["auditor"])).scalar_one()
        m.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        s.commit()
    try:
        assert api("auditor", "GET", f"/v1/engagements/{eng['id']}").status_code == 404
    finally:
        with admin_session() as s:
            m = s.execute(select(Membership).where(Membership.user_id == api.ids["auditor"])).scalar_one()
            m.expires_at = datetime.now(timezone.utc) + timedelta(days=30)
            s.commit()


def test_partner_admin_only_sees_partner_clients(api):
    tenants = api.ok("partner", "GET", "/v1/tenants")
    assert str(api.ids["globex"]) in {t["id"] for t in tenants}
    assert len({t["partner_id"] for t in tenants}) == 1 and None not in {t["partner_id"] for t in tenants}
    assert str(api.ids["acme"]) not in {t["id"] for t in tenants}
    eng = api.engagement("acme")
    assert api("partner", "GET", f"/v1/engagements/{eng['id']}").status_code == 404


def test_download_tokens_bound_to_user_and_single_use(api):
    eng = api.engagement("acme")
    ev = api.upload("client_a", eng["id"], "dl.txt", ("download " + MFA_POLICY).encode())
    link = api.ok("client_a", "POST", f"/v1/evidence/{ev['id']}/download")["download_url"]
    assert api("lead", "GET", link).status_code == 404  # another user cannot use it
    r = api("client_a", "GET", link)
    assert r.status_code == 200 and b"download" in r.content
    assert api("client_a", "GET", link).status_code == 410  # single use


def test_unauthenticated_and_bad_tokens(client):
    assert client.get("/v1/engagements").status_code == 401
    assert client.get("/v1/engagements", headers={"authorization": "Bearer nope"}).status_code == 401
    import jwt
    forged = jwt.encode({"sub": str(uuid.uuid4()), "aud": "tensor-ops", "iss": "tensor-dev",
                         "exp": 9999999999}, "wrong-secret", algorithm="HS256")
    assert client.get("/v1/me", headers={"authorization": f"Bearer {forged}"}).status_code == 401


def test_jobs_endpoint_scoped(api):
    eng = api.engagement("acme")
    sess = api.ok("client_a", "POST", "/v1/evidence/upload-sessions", json={
        "engagement_id": eng["id"], "title": "j", "filename": "j.txt", "size_bytes": 5})
    job = api.ok("client_a", "PUT", f"{sess['upload_url']}?token={sess['upload_token']}", content=b"hello")["job_id"]
    assert api("client_b", "GET", f"/v1/jobs/{job}").status_code == 404
    assert api.ok("client_a", "GET", f"/v1/jobs/{job}")["kind"] == "ingest_evidence"
    drain()
