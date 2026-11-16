"""Test harness: a fresh PostgreSQL schema (with RLS) per session, synthetic users, in-process API client."""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("TENSOR_ENV", "test")
os.environ.setdefault("TENSOR_DATABASE_URL", "postgresql+psycopg://tensor_app:devpass@/tensor_test?host=/tmp")
os.environ.setdefault("TENSOR_ADMIN_DATABASE_URL", "postgresql+psycopg://tensor_owner:devpass@/tensor_test?host=/tmp")
os.environ["TENSOR_STORAGE_DIR"] = tempfile.mkdtemp(prefix="tensor-objects-")
os.environ.setdefault("TENSOR_AI_PROVIDER", "mock")

import io  # noqa: E402
import uuid  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from tensor_ops.config import get_settings  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session", autouse=True)
def database() -> dict[str, uuid.UUID]:
    eng = create_engine(get_settings().admin_database_url)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE"))
        c.execute(text("CREATE SCHEMA public"))
    eng.dispose()
    cfg = Config(os.path.join(BACKEND, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND, "migrations"))
    command.upgrade(cfg, "head")
    from tensor_ops.db import admin_session
    from tensor_ops.seed import load_demo, load_reference
    with admin_session() as s:
        load_reference(s)
        ids = load_demo(s)
        s.commit()
    return ids


@pytest.fixture(scope="session")
def ids(database: dict[str, uuid.UUID]) -> dict[str, uuid.UUID]:
    return database


@pytest.fixture(scope="session")
def client(database) -> TestClient:  # type: ignore[no-untyped-def]
    from tensor_ops.api.main import app
    return TestClient(app)


class Api:
    def __init__(self, client: TestClient, ids: dict[str, uuid.UUID]) -> None:
        self.c, self.ids, self._tok = client, ids, {}

    def token(self, who: str, mfa: bool = True) -> str:
        key = f"{who}:{mfa}"
        if key not in self._tok:
            r = self.c.post("/v1/dev/login", json={"user_id": str(self.ids[who]), "mfa": mfa})
            assert r.status_code == 200, r.text
            self._tok[key] = r.json()["access_token"]
        return self._tok[key]

    def __call__(self, who: str, method: str, path: str, mfa: bool = True, **kw: Any):  # type: ignore[no-untyped-def]
        h = kw.pop("headers", {}) | {"authorization": f"Bearer {self.token(who, mfa)}"}
        return self.c.request(method, path, headers=h, **kw)

    def ok(self, who: str, method: str, path: str, status: int | None = None, **kw: Any) -> Any:
        r = self(who, method, path, **kw)
        assert r.status_code < 300 if status is None else r.status_code == status, f"{r.status_code} {r.text}"
        try:
            return r.json()
        except ValueError:
            return r.text

    # ---- helpers
    def engagement(self, tenant: str = "acme", frameworks: list[str] | None = None, who: str = "lead",
                   value: float = 7500) -> dict:
        return self.ok(who, "POST", "/v1/engagements", json={
            "tenant_id": str(self.ids[tenant]), "name": f"Test {uuid.uuid4().hex[:6]}",
            "frameworks": frameworks or ["SOC2"], "signer_id": str(self.ids["expert"]),
            "contract_value_usd": value})

    def upload(self, who: str, engagement_id: str, filename: str, data: bytes, title: str | None = None,
               run: bool = True, **extra: Any) -> dict:
        sess = self.ok(who, "POST", "/v1/evidence/upload-sessions", json={
            "engagement_id": engagement_id, "title": title or filename, "filename": filename,
            "size_bytes": len(data), **extra})
        self.ok(who, "PUT", f"{sess['upload_url']}?token={sess['upload_token']}", content=data)
        if run:
            drain()
        return self.ok("lead", "GET", f"/v1/evidence/{sess['evidence_id']}")  # staff view includes AI artifacts


def drain() -> int:
    from tensor_ops.jobs.queue import run_pending
    return run_pending(200, ignore_delay=True)


@pytest.fixture(scope="session")
def api(client: TestClient, ids: dict[str, uuid.UUID]) -> Api:
    return Api(client, ids)


@pytest.fixture(autouse=True)
def _reset_overrides():  # type: ignore[no-untyped-def]
    yield
    from tensor_ops.ai.provider import set_provider_override
    from tensor_ops.connectors.service import set_factory_override
    set_provider_override(None)
    set_factory_override(None)
    from tensor_ops.api.deps import limiter
    limiter._hits.clear()


def make_pdf(lines: list[str]) -> bytes:
    """Minimal valid single-page PDF with Helvetica text (synthetic test fixture)."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = "BT /F1 11 Tf 50 780 Td 14 TL " + " ".join(f"({esc(ln)}) '" for ln in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{o}\nendobj\n".encode())
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def make_docx(paragraphs: list[str]) -> bytes:
    import docx
    d = docx.Document()
    for p in paragraphs:
        d.add_paragraph(p)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


MFA_POLICY = ("Access Control Standard. Multi-factor authentication (MFA) is enforced for all workforce accounts in "
              "Google Workspace and the AWS console since 2026-01-15. Two-factor is required for the GitHub organisation.")
ACCESS_REVIEW = ("Quarterly access review completed on 2026-09-30 by the CTO. All admin accounts were reviewed and two "
                 "stale accounts removed. Access review sign-off attached.")
