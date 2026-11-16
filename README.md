# Tensor Operations Console

The internal delivery platform behind TensorInfoSec's done-for-you compliance service: SOC 2, ISO 27001,
ISO 42001 and PCI DSS readiness. **AI drafts; a named Tensor expert signs; an independent auditor issues the report.**

It does not sell seats. Tensor engineers and experts use it to deliver the Audit-Ready Year faster: 100 hours per
client today, with 65 as the year-2 target. Clients, white-label partners and auditors get scoped, read-mostly views.

> Read [`docs/REVIEW.md`](docs/REVIEW.md) first. It explains what was changed from the AI-suggested engineering pack, and why.

## What's in it

| Area | What it does |
|---|---|
| **Readiness Snapshot** | Discovery-call intake → deterministic gap score, dated 8-week plan and quote from the plan's price book → the named signer signs → sent → converted into an engagement with one click. 3-business-day SLA tracking. |
| **Engagements** | Framework selection creates the in-scope controls and dated evidence requests. Server-enforced state machines cover engagements, controls, evidence, remediation, connectors and reports. |
| **Evidence pipeline** | Short-lived upload sessions → quarantine → content-type validation → malware scan (ClamAV) → SHA-256 → dedupe → sandboxed parser (rlimits, timeout, isolated interpreter) → chunks with page and offset provenance → versioning and retention. |
| **AI evidence review** | Structured outputs (Claude via the official SDK, or an offline mock). The model gets no tools. Code checks the schema, that every cited chunk was in the context, that quotes are verbatim, that controls are in scope, and blocks compliance claims. Bounded retry, per-job and per-tenant budgets, and a full run record (model, prompt and schema version, tokens, cost, latency). |
| **Expert review queue** | Approve, edit (re-validated) or reject (comment required). The original AI output is preserved, versions are checked optimistically, and review time is captured. |
| **Sign-off & trust ledger** | Immutable sign-offs over SHA-256 content hashes. Only the engagement's named signer signs, with MFA. Clients and auditors see who signed what. |
| **Questionnaires** | CSV/XLSX import. Answers are drafted only from *approved* evidence, with citations. Expert approval and sign-off come before a CSV export (with formula-injection guard). |
| **Policies** | Tensor-authored templates (9 policies) → client fills placeholders and approves → published as evidence through the same pipeline. |
| **Reports** | Evidence index, gap report and weekly status, built only from approved records. Approval must come from someone other than the author; release needs MFA and creates a sign-off. Stale evidence blocks release. Partner branding for white-label. |
| **Connectors** | GitHub (read-only): org 2FA requirement and branch protection snapshot as evidence. Needs client approval, uses an encrypted token, refuses write-scoped tokens, and handles revoke, rate limits and idempotent daily sync. |
| **Delivery economics** | Time per control (AI-assisted flag), engineer and expert cost, AI cost, contribution margin, and AI accept/edit/reject rates against the 100 h → 65 h target. |
| **Data protection** | Row-level security as a second tenant boundary. Append-only audit, sign-offs and review decisions are enforced in the database. Deletion needs two people and is verified. Retention sweep. Restore check script. |

## New in v1.1: assurance analytics, import centre, glass UI

Ideas taken from the ISO Sentinel prototype and the SOC 2 Type II audit dashboard workbook, rebuilt on Tensor's evidence-first model:

| Feature | What it does |
|---|---|
| **Audit-readiness score** | Weighted 0–100 with a Ready / At risk / Critical rating. Type II model: implementation 30, testing coverage 25, operating effectiveness 25, evidence 10, exception health 10. Readiness (Type I) model: implementation 40, evidence 30, expert sign-off 20, remediation 10. Chosen automatically by whether tests exist. |
| **Engagement dashboard** | Readiness gauge, KPI tiles, effectiveness donut, criterion radar (grouped bars when there are fewer than 5 criteria), implementation trend, monthly activity, compliance matrix, domain bars, risk exposure, owners table, top exceptions and milestone timeline. All dependency-free SVG with hover tooltips. |
| **Type II testing** | Sample-based tests (samples, deviations, result computed or given). Deviations automatically raise exceptions (EXC-###) with severity from the control's risk rating and a linked remediation task. An exception closes only after the remediation task is verified, with a closure note. |
| **Milestones** | Default 7-step audit timeline per engagement (kick-off → report issued); done/late tracking. |
| **Import centre** | Gap-assessment or test-log spreadsheets (.xlsx/.csv). Auto-detects the right sheet and matches headers flexibly ("Standard Ref", "TSC Ref", "Control ID"…). Maps framework refs to Tensor controls. Dry-run validation log shows duplicates, unknown refs, invalid values and rows not assessed. **A self-assessment never marks a control implemented.** |
| **Controls explorer** | Search and filter by criterion, domain and effectiveness; inline owner and risk editing; workflow actions; CSV export. |
| **Executive report** | Printable report with rule-based findings (no AI), score components, compliance by criterion, and controls needing attention. |
| **Evidence uploader** | Drag-and-drop several files at once, with per-file upload progress and a live pipeline (received → quarantined → scanned → extracted → AI draft). |
| **Glass theme** | Dark by default, light mode toggle, validated colour-blind-safe chart palette, status colours always shown with labels. |
| **Demo evidence pack** | `demo/` holds 16 synthetic evidence files plus 3 import spreadsheets, with planted edge cases (stale, irrelevant, prompt injection, duplicate, invalid values). See `demo/README.md`. |
| **Claude artifact version** | `claude-artifact/tensor-evidence-studio.html` is a self-contained version that runs inside Claude: Claude reviews uploads with verbatim-citation checks, data is kept in the artifact's shared store, PDF/DOCX/XLSX are parsed in the browser, plus import, testing, an Ask-Claude panel and report downloads. |

## Run it locally

Requirements: Python 3.13, Node 22, PostgreSQL 16 (or Docker).

**With Docker (includes ClamAV):**
```bash
docker compose up --build        # http://localhost:8000  (synthetic demo data is loaded)
```

**Without Docker:**
```bash
# 1. Postgres: an owner role for migrations and a NON-owner app role (RLS applies to it)
createuser tensor_owner -P; createuser tensor_app -P; createdb -O tensor_owner tensor

# 2. Backend
cd backend
pip install -r requirements-dev.txt
export TENSOR_DATABASE_URL=postgresql+psycopg://tensor_app:devpass@localhost/tensor
export TENSOR_ADMIN_DATABASE_URL=postgresql+psycopg://tensor_owner:devpass@localhost/tensor
alembic upgrade head
python -m tensor_ops.seed --demo                 # reference data + synthetic demo clients
uvicorn tensor_ops.api.main:app --port 8000 &     # API (OpenAPI at /docs)
python -m tensor_ops.jobs.worker &                # background worker

# 3. Frontend
cd ../frontend && npm ci && npm run dev           # http://localhost:5173 (proxies /v1 to :8000)
```

Sign in with any synthetic user on the dev login screen. Try these:
- **Abrar J. (Delivery Lead)**: dashboard, engagements, snapshots, report release
- **Nadia R. (Compliance Specialist)**: the named signer: review queue, sign-offs
- **Acme CTO**: client portal (upload against requests, approve policies and connectors)
- **Northwind vCISO**: white-label partner view
- **Partner Auditor**: time-boxed read-only view (approved evidence only)

To use Claude instead of the offline mock: `TENSOR_AI_PROVIDER=anthropic TENSOR_ANTHROPIC_API_KEY=...`
(the default model is `claude-sonnet-5-5`, set with `TENSOR_AI_MODEL`). Run `python -m evals.run_evals` against it before enabling it for clients.

## Tests and checks

```bash
cd backend
pytest tests -q                       # 95 tests: isolation, ingestion, AI safety, review, workflow, reports, connectors, deletion
python -m evals.run_evals             # held-out AI evaluation (fails on safety-threshold breach)
ruff check . && bandit -r tensor_ops -c pyproject.toml && pip-audit -r requirements.lock
python ../deploy/restore_check.py --source tensor --storage var/objects
```

## Layout

```
backend/tensor_ops/
  api/            FastAPI app and routers (77 endpoints, /v1)
  security/       OIDC/dev auth, deny-by-default tenant-scoped authorization
  services/       evidence pipeline, review & sign-off, snapshot, pricing, policies, reports, metrics, retention
  ingestion/      storage adapter, malware scanner, magic-byte validation, sandboxed extraction
  ai/             provider adapters (Anthropic, mock), schemas, prompts, validation, task orchestration
  connectors/     connector contract, GitHub, encrypted secret store
  jobs/           Postgres job queue and worker
  content/        Tensor-authored control library (35 controls, 4 frameworks) and policy templates
backend/migrations/   Alembic: schema + RLS, grants, append-only triggers
backend/evals/        AI eval harness with dev and held-out synthetic cases
frontend/src/         React console, client portal and auditor view
docs/                 review, ADRs, threat model, traceability, production readiness, runbooks
```

## Status

This is a working, tested build of the full platform scope. **It is not certified production-ready.** See
[`docs/PRODUCTION_READINESS.md`](docs/PRODUCTION_READINESS.md) for the gates that still need a qualified human:
penetration test, expert validation of control mappings, legal review of the contract and DPA, an IdP, and cloud deployment.
