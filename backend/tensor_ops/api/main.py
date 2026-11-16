"""FastAPI application: Tensor Operations Console API."""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text

from ..config import get_settings
from ..db import new_session
from ..jobs import handlers  # noqa: F401  (register job handlers)
from ..logging import configure_logging, correlation_id
from .routers import (
    ai,
    assurance,
    connectors,
    controls,
    deletion,
    dev,
    engagements,
    evidence,
    me,
    metrics,
    policies,
    questionnaires,
    reports,
    snapshots,
    tenants,
    work,
)

log = logging.getLogger("tensor.api")


def create_app() -> FastAPI:
    settings = get_settings()  # validates config; fails closed outside local
    configure_logging()
    app = FastAPI(title="Tensor Operations Console API", version="1.0.0",
                  description="Internal AI-assisted delivery platform for TensorInfoSec managed compliance. "
                              "AI proposes; named experts decide.")

    @app.middleware("http")
    async def correlation(request: Request, call_next):  # type: ignore[no-untyped-def]
        cid = request.headers.get("x-request-id") or uuid.uuid4().hex
        cid = cid[:64] if cid.replace("-", "").isalnum() else uuid.uuid4().hex
        token = correlation_id.set(cid)
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error")
            response = JSONResponse({"detail": "Internal error", "request_id": cid}, status_code=500)
        finally:
            correlation_id.reset(token)
        response.headers["x-request-id"] = cid
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["referrer-policy"] = "no-referrer"
        response.headers["x-frame-options"] = "DENY"
        response.headers.setdefault("cache-control", "no-store")
        return response

    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                           allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
                           allow_headers=["authorization", "content-type", "x-request-id", "idempotency-key"])

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"])
    def readyz() -> JSONResponse:
        try:
            with new_session() as s:
                s.execute(text("select 1"))
            return JSONResponse({"status": "ready"})
        except Exception:  # noqa: BLE001
            return JSONResponse({"status": "db_unavailable"}, status_code=503)

    for r in (me, dev, assurance, tenants, engagements, controls, evidence, ai, work, questionnaires, snapshots, policies,
              reports, connectors, metrics, deletion):
        app.include_router(r.router, prefix="/v1")

    static = settings.static_dir
    if static and Path(static).is_dir():
        root = Path(static).resolve()

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            f = (root / path).resolve()
            if path and root in f.parents and f.is_file():
                return FileResponse(f, headers={"cache-control": "public, max-age=3600"})
            return FileResponse(root / "index.html", headers={"cache-control": "no-store"})

    return app


app = create_app()
