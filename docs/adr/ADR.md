# Architecture decision records

## ADR-001 — Modular monolith: FastAPI + PostgreSQL, separate worker process
**Decision.** One Python package (`tensor_ops`) with explicit modules (`api`, `services`, `ingestion`, `ai`,
`connectors`, `jobs`). The same image runs the API (`uvicorn`) or a worker (`python -m tensor_ops.jobs.worker`).
**Why.** Team size; one language for backend and AI; Pydantic gives typed schemas for both the API and the model output.
**Revisit when** the ingestion or AI workers need separate scaling. They already run as separate processes.

## ADR-002 — Postgres job queue instead of Temporal (for now)
**Decision.** A `jobs` table claimed with `SELECT … FOR UPDATE SKIP LOCKED`. It has idempotency keys, bounded
retries with exponential backoff, a dead state, and per-kind handlers. Jobs carry IDs only, never document content.
**Why.** No extra infrastructure to run. Transactional with business writes, so no outbox is needed.
**Revisit when** workflows need long timers or human-wait states beyond a few days. `jobs/queue.py` is the
seam where Temporal can replace it.

## ADR-003 — Tenant isolation in three layers
1. **Application.** Every tenant-owned query goes through `security/authz.py`. Tenant scope comes from the
   authenticated user's memberships, never from the request body. Inaccessible objects return 404, not 403.
2. **Database.** Row-level security on every tenant-owned table. The app connects as a non-owner role
   (`tensor_app`). Each transaction sets `app.tenant_ids` to the caller's tenants. A bug that forgets the
   application filter still returns no foreign rows.
3. **Tests.** Negative tests for IDOR on every resource type, list endpoints, exports, search, jobs and connectors.

## ADR-004 — Authentication
OIDC JWT verification (JWKS, issuer, audience, expiry) is required whenever `TENSOR_ENV != local`. The app refuses
to start without it (fail closed). In `local` only, `/v1/dev/login` issues HS256 tokens for seeded synthetic users.
MFA and phishing-resistant factors are enforced at the IdP. Privileged actions (sign-off, report release) require
an `amr` claim containing `mfa` outside local.

## ADR-005 — AI integration
- `ai/provider.py` defines `generate_structured(task, system, user, schema, budget)`. Adapters: `anthropic`
  (official SDK, `output_config.format` JSON schema structured outputs) and `mock` (deterministic, used in tests,
  evals and offline demos).
- The model gets **no tools** for evidence review or questionnaire drafting. It returns JSON only. This removes
  the tool-abuse surface entirely. Any future tool must be a narrow, server-authorised function.
- Output validation is code, not prompt: JSON schema, cited chunk IDs must have been in the context, quotes must
  appear verbatim in the cited chunk, control IDs must belong to the engagement, and words like
  "compliant" or "certified" are blocked.
- Every run records provider, model, prompt version, schema version, input refs, tokens, cost, latency and request ID.
- Per-job token and dollar budgets. A per-tenant daily AI spend cap is enforced before the call.

## ADR-006 — Storage, scanning and secrets behind adapters
`ingestion/storage.py` (local filesystem; S3 adapter seam), `ingestion/scanner.py` (ClamAV `clamd` INSTREAM
or a dev EICAR-only scanner, which is refused outside local) and `connectors/secrets.py` (Fernet-encrypted table
for dev; cloud secret-manager seam). Connector secrets never live in the `connectors` row; only a reference does.

## ADR-007 — Untrusted document parsing in a sandboxed child process
Extraction runs in `python -I -m tensor_ops.ingestion.extract_child` with CPU, memory and file-size rlimits and a
wall-clock timeout. A crashing or hostile parser cannot take down the worker, and no document text is logged.

## ADR-008 — Content licensing
The control library is Tensor-authored. Framework mappings store only requirement identifiers (for example `CC6.1`
or `A.5.15`), a short Tensor paraphrase, and licence metadata. No AICPA or ISO normative text is bundled.

## ADR-009 — Frontend
React + TypeScript + Vite, one single-page app with three audiences routed by role: Tensor operators, client users,
and auditors (read-only). In production it is served as static files by the API container, so there is one origin
and no CORS.
