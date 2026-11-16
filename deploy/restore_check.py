"""Restore verification: dump the database, restore it into a scratch database, and verify that evidence IDs,
SHA-256 hashes and stored objects still agree (pack 08, critical test 10).

    python deploy/restore_check.py --pg-host /tmp --source tensor --storage backend/var/objects

Requires pg_dump/pg_restore and a role that can create databases (run by the operator, not the app).
Exit code 0 = restore verified.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess  # noqa: S404
import sys
import tempfile

import psycopg


def q(dsn: str, sql: str) -> list[tuple]:
    with psycopg.connect(dsn) as c:
        return c.execute(sql).fetchall()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pg-host", default="localhost")
    ap.add_argument("--pg-user", default="postgres")
    ap.add_argument("--source", default="tensor")
    ap.add_argument("--target", default="tensor_restore_check")
    ap.add_argument("--storage", required=True)
    a = ap.parse_args()
    base = f"host={a.pg_host} user={a.pg_user}"
    dump = os.path.join(tempfile.mkdtemp(), "tensor.dump")
    subprocess.run(["pg_dump", "-h", a.pg_host, "-U", a.pg_user, "-Fc", "-f", dump, a.source], check=True)  # noqa: S603,S607
    with psycopg.connect(f"{base} dbname=postgres", autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{a.target}"')
        c.execute(f'CREATE DATABASE "{a.target}"')
    subprocess.run(["pg_restore", "-h", a.pg_host, "-U", a.pg_user, "-d", a.target, "--no-owner", dump], check=True)  # noqa: S603,S607
    sql = ("select id::text, tenant_id::text, sha256, object_key, status from evidence_items "
           "where status not in ('deleted','awaiting_upload','duplicate','rejected_security') order by id")
    src = q(f"{base} dbname={a.source}", sql)
    dst = q(f"{base} dbname={a.target}", sql)
    problems = []
    if src != dst:
        problems.append(f"evidence metadata differs after restore ({len(src)} vs {len(dst)} rows)")
    checked = 0
    for _id, _tenant, sha, key, _status in dst:
        if not key or not sha:
            continue
        path = os.path.join(a.storage, key)
        if not os.path.exists(path):
            problems.append(f"missing object for evidence {_id}")
            continue
        with open(path, "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() != sha:
                problems.append(f"hash mismatch for evidence {_id}")
        checked += 1
    for t in ("audit_events", "signoffs", "review_decisions"):
        a_n = q(f"{base} dbname={a.source}", f"select count(*) from {t}")[0][0]  # noqa: S608
        b_n = q(f"{base} dbname={a.target}", f"select count(*) from {t}")[0][0]  # noqa: S608
        if a_n != b_n:
            problems.append(f"{t}: {a_n} rows before, {b_n} after")
    rls = q(f"{base} dbname={a.target}", "select count(*) from pg_tables where schemaname='public' and rowsecurity")[0][0]
    with psycopg.connect(f"{base} dbname=postgres", autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{a.target}"')
    print(f"evidence rows: {len(dst)}; objects hash-verified: {checked}; RLS tables after restore: {rls}")
    for p in problems:
        print("PROBLEM:", p)
    print("RESTORE VERIFIED" if not problems else "RESTORE FAILED")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
