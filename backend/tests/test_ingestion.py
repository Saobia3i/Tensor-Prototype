"""Evidence ingestion: quarantine, validation, malware scanning, hashing, dedupe, sandboxed parsing, idempotency."""
from __future__ import annotations

import hashlib
import io
import logging
import uuid
import zipfile

import pytest

from tensor_ops.ingestion.scanner import EICAR
from tensor_ops.ingestion.storage import get_store

from .conftest import ACCESS_REVIEW, MFA_POLICY, drain, make_docx, make_pdf


def test_pdf_happy_path_with_provenance(api):
    eng = api.engagement("acme")
    data = make_pdf(["Quarterly access review", ACCESS_REVIEW[:90], "Signed by CTO"])
    ev = api.upload("client_a", eng["id"], "review.pdf", data)
    assert ev["status"] == "proposed", ev
    assert ev["sha256"] == hashlib.sha256(data).hexdigest()
    assert ev["detected_type"] == "pdf" and ev["page_count"] == 1
    assert ev["chunks"] and ev["chunks"][0]["page"] == 1 and ev["chunks"][0]["char_start"] == 0
    assert ev["scan_result"].startswith("clean")
    assert ev["retention_until"]


def test_docx_and_versioning(api):
    eng = api.engagement("acme")
    v1 = api.upload("client_a", eng["id"], "policy.docx", make_docx(["Access policy v1", MFA_POLICY]))
    v2 = api.upload("client_a", eng["id"], "policy.docx", make_docx(["Access policy v2", MFA_POLICY, "Updated."]),
                    supersedes_id=v1["id"])
    assert v2["version"] == 2 and v2["lineage_id"] == v1["lineage_id"]
    assert [v["version"] for v in v2["versions"]] == [1, 2]


def test_eicar_is_rejected_and_never_parsed(api):
    eng = api.engagement("acme")
    ev = api.upload("client_a", eng["id"], "evil.txt", b"hello " + EICAR)
    assert ev["status"] == "rejected_security"
    assert ev["chunks"] == []
    assert "malware" in (ev["error"] or "")


@pytest.mark.parametrize("name,data,reason", [
    ("fake.pdf", b"this is not a pdf at all", "does not match"),
    ("image.txt", b"\x89PNG\r\n\x1a\n" + b"\x00" * 50, "does not match"),
    ("bin.txt", b"\x00\x01\x02binary", "binary"),
    ("prog.exe", b"MZ plain text pretending", "does not match"),
])
def test_type_validation_uses_content_not_name(api, name, data, reason):
    eng = api.engagement("acme")
    ev = api.upload("client_a", eng["id"], name, data)
    assert ev["status"] == "rejected_security" and reason in ev["error"], ev["error"]


def test_zip_bomb_like_docx_rejected(api):
    eng = api.engagement("acme")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "A" * (210 * 1024 * 1024))
    ev = api.upload("client_a", eng["id"], "bomb.docx", buf.getvalue())
    assert ev["status"] == "rejected_security" and "expands" in ev["error"]


def test_malformed_docx_fails_safely(api):
    eng = api.engagement("acme")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<<<not xml")
    ev = api.upload("client_a", eng["id"], "broken.docx", buf.getvalue())
    assert ev["status"] == "failed" and "parser failed" in ev["error"]


def test_parser_timeout_is_contained(api, monkeypatch):
    from tensor_ops.ingestion import extract
    from tensor_ops.services import evidence as svc

    def boom(path, kind):  # noqa: ANN001
        raise extract.ExtractionError("parser timeout")
    monkeypatch.setattr(svc, "run_extraction", boom)
    eng = api.engagement("acme")
    ev = api.upload("client_a", eng["id"], "slow.txt", b"slow document content")
    assert ev["status"] == "failed" and ev["error"] == "parser timeout"


def test_real_sandbox_kills_runaway_parser(monkeypatch):
    """The child process runs with a CPU limit and wall-clock timeout."""
    from tensor_ops.config import get_settings
    from tensor_ops.ingestion.extract import ExtractionError, run_extraction
    s = get_settings()
    object.__setattr__(s, "extract_timeout_s", 1)
    try:
        with pytest.raises(ExtractionError):
            run_extraction("/dev/zero", "text")  # endless read -> killed by limits/timeout
    finally:
        object.__setattr__(s, "extract_timeout_s", 30)


def test_duplicate_upload_detected(api):
    eng = api.engagement("acme")
    data = ("dup " + uuid.uuid4().hex + MFA_POLICY).encode()
    a = api.upload("client_a", eng["id"], "a.txt", data)
    b = api.upload("client_a", eng["id"], "b.txt", data)
    assert a["status"] == "proposed" and b["status"] == "duplicate"
    assert b["error"] == f"duplicate_of:{a['id']}"


def test_upload_session_rules(api):
    eng = api.engagement("acme")
    big = api("client_a", "POST", "/v1/evidence/upload-sessions", json={
        "engagement_id": eng["id"], "title": "big", "filename": "big.pdf", "size_bytes": 10 ** 9})
    assert big.status_code == 413
    sess = api.ok("client_a", "POST", "/v1/evidence/upload-sessions", json={
        "engagement_id": eng["id"], "title": "t", "filename": "t.txt", "size_bytes": 4})
    url = sess["upload_url"]
    assert api("client_a", "PUT", f"{url}?token=wrong-token-value", content=b"abcd").status_code == 403
    assert api("lead", "PUT", f"{url}?token={sess['upload_token']}", content=b"abcd").status_code == 403  # other user
    assert api("client_a", "PUT", f"{url}?token={sess['upload_token']}", content=b"abcdef").status_code == 413
    assert api("client_a", "PUT", f"{url}?token={sess['upload_token']}", content=b"abcd").status_code == 202
    assert api("client_a", "PUT", f"{url}?token={sess['upload_token']}", content=b"abcd").status_code == 409  # reuse
    drain()


def test_scanner_outage_fails_closed_then_retries(api, monkeypatch):
    from tensor_ops.ingestion.scanner import DevScanner, ScanError
    from tensor_ops.services import evidence as svc
    calls = {"n": 0}

    class Flaky(DevScanner):
        def scan(self, data):  # noqa: ANN001
            calls["n"] += 1
            if calls["n"] == 1:
                raise ScanError("clamd down")
            return super().scan(data)
    monkeypatch.setattr(svc, "get_scanner", lambda: Flaky())
    eng = api.engagement("acme")
    sess = api.ok("client_a", "POST", "/v1/evidence/upload-sessions", json={
        "engagement_id": eng["id"], "title": "retry", "filename": "r.txt", "size_bytes": len(MFA_POLICY)})
    api.ok("client_a", "PUT", f"{sess['upload_url']}?token={sess['upload_token']}", content=MFA_POLICY.encode())
    from tensor_ops.jobs.queue import claim, run_job
    jid = claim(["ingest_evidence"])
    assert run_job(jid) == "queued"  # failed attempt, re-queued with backoff
    ev = api.ok("client_a", "GET", f"/v1/evidence/{sess['evidence_id']}")
    assert ev["status"] == "quarantined" and ev["chunks"] == []  # rolled back: nothing parsed while unscanned
    drain()
    ev = api.ok("client_a", "GET", f"/v1/evidence/{sess['evidence_id']}")
    assert ev["status"] in ("proposed", "extracted") and calls["n"] == 2


def test_ingest_retry_is_idempotent(api):
    from tensor_ops.db import new_session
    from tensor_ops.services.evidence import process_ingest
    eng = api.engagement("acme")
    ev = api.upload("client_a", eng["id"], "idem.txt", ("idempotent " + MFA_POLICY).encode())
    with new_session(tenant_ids=[api.ids["acme"]]) as s:
        assert process_ingest(s, uuid.UUID(ev["id"]))["status"] == "skipped"
    again = api.ok("client_a", "GET", f"/v1/evidence/{ev['id']}")
    assert len(again["chunks"]) == len(ev["chunks"])


def test_document_text_never_logged(api, caplog):
    secret_phrase = "ZEBRA-CONFIDENTIAL-" + uuid.uuid4().hex
    caplog.set_level(logging.DEBUG)
    eng = api.engagement("acme")
    api.upload("client_a", eng["id"], "conf.txt", f"{secret_phrase} {MFA_POLICY}".encode())
    assert secret_phrase not in caplog.text


def test_storage_rejects_path_traversal():
    store = get_store()
    for bad in ("../etc/passwd", f"{uuid.uuid4()}/evidence/../../x", "/abs/path"):
        with pytest.raises(ValueError):
            store.put(bad, b"x")
