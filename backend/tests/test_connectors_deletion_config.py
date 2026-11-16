"""GitHub connector (mocked HTTP), verified deletion, and fail-closed configuration."""
from __future__ import annotations

import json
import logging
import uuid

import httpx
import pytest
from sqlalchemy import select, text

from tensor_ops.config import ConfigError, Settings
from tensor_ops.connectors.github import GitHubConnector
from tensor_ops.connectors.service import set_factory_override
from tensor_ops.db import admin_session
from tensor_ops.ingestion.storage import evidence_key, get_store
from tensor_ops.models import Connector, EvidenceChunk, Job

from .conftest import MFA_POLICY, drain

TOKEN = "ghp_" + "A" * 36


def gh_transport(state: dict) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        state.setdefault("paths", []).append(req.url.path)
        if state.get("revoked"):
            return httpx.Response(401, json={"message": "Bad credentials"})
        if state.get("rate_limited"):
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1"})
        p = req.url.path
        if p == "/":
            return httpx.Response(200, headers={"x-oauth-scopes": state.get("scopes", "read:org, repo")})
        if p == "/orgs/acme-co":
            return httpx.Response(200, json={"login": "acme-co", "two_factor_requirement_enabled": True})
        if p == "/orgs/acme-co/members":
            return httpx.Response(200, json=[])
        if p == "/orgs/acme-co/repos":
            if req.url.params.get("page") == "2":
                return httpx.Response(200, json=[{"name": "infra", "default_branch": "main", "private": True}])
            return httpx.Response(200, json=[{"name": "api", "default_branch": "main", "private": True}],
                                  headers={"link": '<https://api.github.com/orgs/acme-co/repos?page=2>; rel="next"'})
        if p == "/repos/acme-co/api/branches/main/protection":
            return httpx.Response(200, json={"required_pull_request_reviews": {"required_approving_review_count": 1},
                                             "enforce_admins": {"enabled": True}})
        if p == "/repos/acme-co/infra/branches/main/protection":
            return httpx.Response(404, json={"message": "Branch not protected"})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def setup_connector(api, state):  # type: ignore[no-untyped-def]
    set_factory_override(lambda c, tok: GitHubConnector(c.config["org"], tok, transport=gh_transport(state)))
    eng = api.engagement("acme")
    c = api.ok("lead", "POST", f"/v1/engagements/{eng['id']}/connectors", json={"provider": "github", "config": {"org": "acme-co"}})
    return eng, c


def test_connector_requires_customer_approval_then_syncs_read_only_evidence(api, caplog):
    caplog.set_level(logging.DEBUG)
    state: dict = {}
    eng, c = setup_connector(api, state)
    assert api("lead", "POST", f"/v1/connectors/{c['id']}/authorize", json={"token": TOKEN}).status_code == 409
    assert api("lead", "POST", f"/v1/connectors/{c['id']}/customer-approve").status_code == 403  # staff can't approve for client
    api.ok("client_a", "POST", f"/v1/connectors/{c['id']}/customer-approve")
    res = api.ok("lead", "POST", f"/v1/connectors/{c['id']}/authorize", json={"token": TOKEN})
    assert res["status"] == "active" and res["has_credential"]
    job = api.ok("lead", "POST", f"/v1/connectors/{c['id']}/sync")["job_id"]
    again = api.ok("lead", "POST", f"/v1/connectors/{c['id']}/sync")["job_id"]
    assert job == again  # idempotent: one snapshot per connector per day
    drain()
    evs = api.ok("lead", "GET", "/v1/evidence", params={"engagement_id": eng["id"]})
    gh = [e for e in evs if e["source"] == "connector:github"]
    assert len(gh) == 1 and gh[0]["status"] == "proposed"
    detail = api.ok("lead", "GET", f"/v1/evidence/{gh[0]['id']}")
    doc = json.loads("".join(ch["text"] for ch in detail["chunks"]))
    assert doc["two_factor_requirement_enabled"] is True and doc["repositories_reviewed"] == 2
    assert doc["repositories_with_branch_protection"] == 1
    codes = {x["control_code"] for x in detail["artifacts"][0]["output"]["candidate_controls"]}
    assert {"TC-AC-02", "TC-CM-01"} <= codes
    # secret never stored in the connector row, in plaintext anywhere, or in logs
    with admin_session() as s:
        row = s.get(Connector, uuid.UUID(c["id"]))
        assert TOKEN not in json.dumps(row.config) and row.secret_ref
        assert s.execute(text("select count(*) from secret_records where ciphertext like :t"),
                         {"t": f"%{TOKEN}%"}).scalar_one() == 0
    assert TOKEN not in caplog.text
    assert all(not p.startswith("/repos/") or p.endswith("/protection") for p in state["paths"])  # read-only calls


def test_revoked_token_stops_sync_and_flags_connector(api):
    state: dict = {}
    eng, c = setup_connector(api, state)
    api.ok("client_a", "POST", f"/v1/connectors/{c['id']}/customer-approve")
    api.ok("lead", "POST", f"/v1/connectors/{c['id']}/authorize", json={"token": TOKEN})
    state["revoked"] = True
    job = api.ok("lead", "POST", f"/v1/connectors/{c['id']}/sync")["job_id"]
    drain()
    with admin_session() as s:
        j = s.get(Job, uuid.UUID(job))
        assert j.status == "dead" and j.attempts == 1  # no endless retries on 401
    lst = api.ok("lead", "GET", f"/v1/engagements/{eng['id']}/connectors")
    assert lst[0]["status"] == "error" and "revoked" in lst[0]["last_error"]


def test_rate_limit_is_retried_with_backoff(api):
    state: dict = {}
    eng, c = setup_connector(api, state)
    api.ok("client_a", "POST", f"/v1/connectors/{c['id']}/customer-approve")
    api.ok("lead", "POST", f"/v1/connectors/{c['id']}/authorize", json={"token": TOKEN})
    state["rate_limited"] = True
    job = api.ok("lead", "POST", f"/v1/connectors/{c['id']}/sync")["job_id"]
    from tensor_ops.jobs.queue import claim, run_job
    assert run_job(claim(["connector_sync"])) == "queued"
    state["rate_limited"] = False
    drain()
    with admin_session() as s:
        assert s.get(Job, uuid.UUID(job)).status == "succeeded"


def test_write_scoped_token_refused(api):
    state: dict = {"scopes": "admin:org, repo"}
    eng, c = setup_connector(api, state)
    api.ok("client_a", "POST", f"/v1/connectors/{c['id']}/customer-approve")
    res = api.ok("lead", "POST", f"/v1/connectors/{c['id']}/authorize", json={"token": TOKEN})
    assert res["status"] == "error" and "read-only" in res["result"]["error"]


def test_client_can_revoke_and_secret_is_destroyed(api):
    state: dict = {}
    eng, c = setup_connector(api, state)
    api.ok("client_a", "POST", f"/v1/connectors/{c['id']}/customer-approve")
    api.ok("lead", "POST", f"/v1/connectors/{c['id']}/authorize", json={"token": TOKEN})
    with admin_session() as s:
        ref = s.get(Connector, uuid.UUID(c["id"])).secret_ref
    out = api.ok("client_a", "POST", f"/v1/connectors/{c['id']}/revoke")
    assert out["status"] == "revoked" and not out["has_credential"]
    with admin_session() as s:
        assert s.execute(text("select count(*) from secret_records where ref=:r"), {"r": ref}).scalar_one() == 0
    assert api("lead", "POST", f"/v1/connectors/{c['id']}/sync").status_code == 409


def test_connector_rejects_bad_org_and_foreign_links():
    with pytest.raises(ValueError):
        GitHubConnector("../evil", TOKEN)
    from tensor_ops.connectors.base import ConnectorScopeError
    gh = GitHubConnector("acme-co", TOKEN, transport=gh_transport({}))
    with pytest.raises(ConnectorScopeError):
        gh._get("https://evil.example/steal")


# ------------------------------------------------------------------ deletion
def test_evidence_deletion_two_person_rule_and_verification(api):
    eng = api.engagement("acme")
    ev = api.upload("client_a", eng["id"], "del.txt", (uuid.uuid4().hex + MFA_POLICY).encode())
    d = api.ok("client_a", "POST", "/v1/deletion-requests", json={"tenant_id": str(api.ids["acme"]),
                                                                  "scope": "evidence_item", "target_id": ev["id"]})
    assert api("client_b", "POST", f"/v1/deletion-requests/{d['id']}/approve").status_code == 404
    assert api("client_a", "POST", f"/v1/deletion-requests/{d['id']}/approve").status_code == 403  # no permission
    api.ok("lead", "POST", f"/v1/deletion-requests/{d['id']}/approve")
    drain()
    reqs = api.ok("lead", "GET", f"/v1/tenants/{api.ids['acme']}/deletion-requests")
    done = next(r for r in reqs if r["id"] == d["id"])
    assert done["status"] == "completed" and done["result"]["verified"] is True
    after = api.ok("lead", "GET", f"/v1/evidence/{ev['id']}")
    assert after["status"] == "deleted" and after["chunks"] == [] and after["title"] == "[deleted]"
    assert all(a["output"].get("purged") for a in after["artifacts"])
    assert not get_store().exists(evidence_key(api.ids["acme"], ev["id"]))


def test_requester_cannot_approve_own_deletion(api):
    eng = api.engagement("acme")
    ev = api.upload("client_a", eng["id"], "own.txt", (uuid.uuid4().hex + MFA_POLICY).encode())
    d = api.ok("lead", "POST", "/v1/deletion-requests", json={"tenant_id": str(api.ids["acme"]),
                                                              "scope": "evidence_item", "target_id": ev["id"]})
    assert api("lead", "POST", f"/v1/deletion-requests/{d['id']}/approve").status_code == 403


def test_tenant_purge_removes_all_rows_but_keeps_audit_trail(api):
    t = api.ok("lead", "POST", "/v1/tenants", json={"name": "Shortlived Co"})
    tid = t["id"]
    api.token("lead")  # roles reload per request
    eng = api.ok("lead", "POST", "/v1/engagements", json={"tenant_id": tid, "name": "temp", "frameworks": ["SOC2"]})
    api.upload("lead", eng["id"], "t.txt", (uuid.uuid4().hex + MFA_POLICY).encode())
    d = api.ok("lead", "POST", "/v1/deletion-requests", json={"tenant_id": tid, "scope": "tenant", "target_id": tid})
    from tensor_ops.models import DeletionRequest
    with admin_session() as s:  # second person approves (expert is not a member; simulate platform approval)
        s.get(DeletionRequest, uuid.UUID(d["id"])).status = "approved"
        s.get(DeletionRequest, uuid.UUID(d["id"])).approved_by = api.ids["owner"]
        s.commit()
    from tensor_ops.services.retention import execute_deletion
    res = execute_deletion(uuid.UUID(d["id"]))
    assert res["verified"] is True
    with admin_session() as s:
        for table in ("tenants", "engagements", "evidence_items", "evidence_chunks", "signoffs", "memberships"):
            col = "id" if table == "tenants" else "tenant_id"
            assert s.execute(text(f"select count(*) from {table} where {col}=:t"), {"t": tid}).scalar_one() == 0
        assert s.execute(text("select count(*) from audit_events where tenant_id=:t"), {"t": tid}).scalar_one() > 0


def test_retention_sweep_expires_and_purges(api):
    from datetime import date, timedelta

    from tensor_ops.services.retention import sweep
    eng = api.engagement("acme")
    ev = api.upload("client_a", eng["id"], "old.txt", (uuid.uuid4().hex + MFA_POLICY).encode())
    with admin_session() as s:
        s.execute(text("UPDATE evidence_items SET retention_until=:d WHERE id=:i"),
                  {"d": date.today() - timedelta(days=1), "i": ev["id"]})
        s.commit()
    assert sweep()["purged"] >= 1
    with admin_session() as s:
        assert s.execute(select(EvidenceChunk).where(EvidenceChunk.evidence_id == uuid.UUID(ev["id"]))).first() is None


# ------------------------------------------------------------------ config
def test_config_fails_closed_outside_local():
    with pytest.raises(ConfigError):
        Settings(env="production").validate()
    with pytest.raises(ConfigError, match="dev malware scanner"):
        Settings(env="production", oidc_issuer="i", oidc_audience="a", oidc_jwks_url="https://x/jwks",
                 secrets_key="k").validate()
    with pytest.raises(ConfigError, match="DEV_JWT"):
        Settings(env="staging", oidc_issuer="i", oidc_audience="a", oidc_jwks_url="https://x/jwks", secrets_key="k",
                 dev_jwt_secret="x", scanner="clamav").validate()
    Settings(env="production", oidc_issuer="i", oidc_audience="a", oidc_jwks_url="https://x/jwks", secrets_key="k",
             scanner="clamav").validate()


def test_dev_login_unavailable_outside_local(api, monkeypatch):
    from tensor_ops.api.routers import dev
    with pytest.raises(Exception) as e:
        dev._local_only(Settings(env="production"))
    assert getattr(e.value, "status_code", None) == 404


def test_security_headers_and_request_ids(client):
    r = client.get("/healthz", headers={"x-request-id": "abc123"})
    assert r.headers["x-request-id"] == "abc123" and r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/readyz").json()["status"] == "ready"
    assert "/v1/evidence/upload-sessions" in client.get("/openapi.json").json()["paths"]
