"""Worker process: `python -m tensor_ops.jobs.worker [--kinds ingest_evidence,ai_review_evidence]`.

Run separate pools per job kind in production (ingestion, AI, connectors) with their own concurrency limits.
"""
from __future__ import annotations

import argparse
import logging
import signal
import time
from datetime import datetime, timedelta, timezone

from ..config import get_settings
from ..db import admin_session
from ..logging import configure_logging
from . import handlers  # noqa: F401  (registers handlers)
from .queue import claim, enqueue, run_job

log = logging.getLogger("tensor.worker")
_stop = False


def _handle_stop(*_: object) -> None:
    global _stop
    _stop = True


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--kinds", default="")
    p.add_argument("--poll", type=float, default=1.0)
    a = p.parse_args()
    configure_logging()
    get_settings()
    kinds = [k for k in a.kinds.split(",") if k] or None
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    last_sweep = datetime.min.replace(tzinfo=timezone.utc)
    log.info("worker started", extra={"fields": {"kinds": kinds}})
    while not _stop:
        if (kinds is None or "retention_sweep" in kinds) and datetime.now(timezone.utc) - last_sweep > timedelta(hours=6):
            with admin_session() as s:
                enqueue(s, "retention_sweep", None, {}, idempotency_key=f"sweep:{datetime.now(timezone.utc):%Y-%m-%dT%H}")
                s.commit()
            last_sweep = datetime.now(timezone.utc)
        jid = claim(kinds)
        if jid is None:
            time.sleep(a.poll)
            continue
        status = run_job(jid)
        log.info("job finished", extra={"fields": {"job_id": str(jid), "status": status}})


if __name__ == "__main__":
    main()
