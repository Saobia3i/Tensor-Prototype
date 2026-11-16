# Production readiness: traceability, verification and open gates

Verification status as of this build. **"Verified" means a test or check was actually run in this build.**
Nothing listed as open has been claimed as done.

## 1. Acceptance criteria traceability (engineering pack 08 + business-plan additions)

| # | Requirement | Where implemented | Verified by |
|---|---|---|---|
| P1.1 | Tenant A cannot read/list/download tenant B records by changing IDs or params | `security/authz.py`, every router; RLS in `migrations/0002` | `test_tenant_isolation.py` (11 tests, incl. RLS-only, jobs, downloads, expiry) ✅ |
| P1.2 | All evidence access and exports create audit events | `audit.record` in evidence/report/questionnaire routers | `test_evidence_access_and_review_are_audited` ✅ |
| P1.3 | Files quarantined and scanned before extraction | `services/evidence.process_ingest` | EICAR, scanner-outage (fails closed) tests ✅ |
| P1.4 | Upload size/type limits, short-lived URLs | `create_upload_session`, `validate.py` | `test_upload_session_rules`, type-validation tests ✅ |
| P1.5 | Secrets not in source or logs | `logging.redact_*`, `connectors/secrets.py` | connector token test (DB + logs), gitleaks in CI ✅ |
| P1.6 | Restore test succeeds | `deploy/restore_check.py` | Run on dev DB: 10/10 objects hash-verified, RLS intact ✅ (repeat on staging) |
| P2.1 | Evidence has hash, source, collection time, version, retention | `EvidenceItem` | `test_pdf_happy_path_with_provenance`, versioning test ✅ |
| P2.2 | Server-enforced evidence state machine | `services/state_machines.py` | illegal-transition tests ✅ |
| P2.3 | Reviewer approves/rejects mapping with comments | `services/review.apply_review` | review tests ✅ |
| P2.4 | Export includes evidence index and provenance | `services/reports.py` | report tests ✅ |
| P2.5 | Framework version + licensing metadata | `content/library.py`, `frameworks` table | Control library UI/API ✅ |
| P3.1 | Every candidate mapping cites evidence IDs and locations | `ai/validation.py` | `test_mock_review_proposes_with_verified_citations` ✅ |
| P3.2 | Invalid schema and fabricated citation IDs rejected | `ai/validation.py` | fabricated / non-verbatim / malformed / out-of-scope tests; eval adversarial set 8/8 ✅ |
| P3.3 | Missing evidence → explicit insufficient-evidence | schema + validator | `test_insufficient_evidence_path` ✅ |
| P3.4 | Prompt-injection corpus triggers no unauthorized action | no tools + validator + state machine | injection tests; eval injection cases ✅ |
| P3.5 | Model output never bypasses review | proposals only; review endpoints human-only | `test_compliance_claim_is_flagged_and_never_applied` ✅ |
| P3.6 | Eval thresholds agreed with a qualified expert on held-out examples | `evals/` (synthetic) | ⚠️ **OPEN**: thresholds are placeholders; expert-labelled corpus needed |
| P3.7 | Cost per completed workflow observable | `ai_runs`, `services/metrics.py` | metrics test ✅ |
| P4.1–4.5 | Revoked token fails safely; retry idempotent; scope mismatch visible; no token reuse; rate-limit backoff | `connectors/` | `test_connectors_deletion_config.py` ✅ (mocked GitHub; verify scopes against live GitHub docs before pilot) |
| P5.1 | Labour minutes, rework, acceptance, turnaround, margin by engagement | `services/metrics.py` | metrics test ✅ |
| P5.2 | Client-facing reports require approval | report state machine, independent approver, MFA release | report tests ✅ |
| T7 | Customer deletion request removes data per policy | `services/retention.py` (two-person, verified) | deletion tests ✅ |
| T8 | Reviewer edit keeps original AI output and revision | `ai_artifacts.output` vs `final_output` | `test_edit_preserves_original_ai_output` ✅ |
| T10 | DB/object restore keeps IDs, hashes, metadata consistent | `deploy/restore_check.py` | run ✅ |
| B1 | Readiness Snapshot with signer, dated plan, all-in quote, 3-day SLA | `services/snapshot.py`, `pricing.py` | snapshot tests ✅ |
| B2 | Named signer & trust ledger | `signoffs`, `/trust-ledger` | sign-off tests ✅ |
| B3 | White-label partners | `partners`, `partner_admin`, branded reports | partner tests ✅ |
| B4 | Questionnaires grounded in approved evidence only | `ai/tasks.answer_questionnaire_item` | questionnaire tests incl. cross-tenant ✅ |
| B5 | Year-1 time mapping toward 100 → 65 h | `time_entries`, metrics | metrics test ✅ |

## 2. Security checks run in this build

| Check | Result |
|---|---|
| `pytest tests` | 95 passed (incl. demo-pack pipeline, import, Type II tests, exceptions, analytics) |
| AI held-out eval (mock provider) | safety thresholds met; output validity 1.0, adversarial rejection 1.0, injection detection 1.0 |
| `ruff` (incl. flake8-bandit rules) | clean |
| `bandit` | 0 high, 0 medium (3 low: subprocess use in the sandbox, reviewed) |
| `pip-audit` | 0 known vulnerabilities (pypdf upgraded to 6.21.2, certifi to 2026.10.23 during this build) |
| `npm audit` | 0 vulnerabilities (Vite upgraded to 6.4.5 during this build) |
| Migrations up → down → up | passes |
| Restore check | passes on dev data |
| Browser walkthrough (staff, client, auditor; approve + upload) | no page errors |
| Container image scan (Trivy), gitleaks | configured in CI, **not run here** (no Docker registry pull in this environment) |

## 3. Open gates (must be closed by qualified people before real client evidence)

1. **Independent penetration test** of the deployed staging environment (OWASP ASVS L2 scope, plus LLM/GenAI Top 10).
2. **Expert validation of every control mapping.** All 142 mappings are `draft`. A qualified SOC 2 / ISO practitioner must
   validate them and set `expert_validated`. Confirm the ISO 42001 and PCI DSS v4.0.1 identifiers against licensed copies.
3. **Identity provider.** Configure OIDC (Okta, Entra or Google) with phishing-resistant MFA for staff and an `amr` claim.
   Provision users before first login. Self-registration does not exist.
4. **Cloud deployment.** Managed Postgres with PITR, S3 (SSE-KMS, versioning, private) via an adapter in `ingestion/storage.py`,
   a secret manager adapter in `connectors/secrets.py`, ClamAV sidecar, separate prod account, IaC, TLS, WAF.
   Add a shared rate-limit store (Redis) when running more than one API replica.
5. **Expert-labelled evaluation corpus.** Aim for 100+ examples per common document type, with thresholds agreed with the
   quality reviewer. Run `evals` against the real model before enabling `TENSOR_AI_PROVIDER=anthropic` for clients.
6. **Legal.** Counsel to review the client contract (scope, "readiness not certification", liability, guarantee wording), the DPA,
   subprocessors (the model provider is one), cross-border access from Bangladesh/Nepal, retention periods and breach notification.
7. **Auditor fees.** Fill `PRICE_BOOK["auditor_fee_estimate"]` from the partner audit firm, or quotes keep showing "to be confirmed".
8. **GitHub connector.** Confirm the required fine-grained permissions against current GitHub docs and get written client approval of scope.
9. **Observability.** Add an OpenTelemetry exporter and the alerts in pack 07 §4. Structured JSON logs with correlation IDs are already emitted.
10. **Restore drill** on staging with production-like volume, plus quarterly repeats.

## 4. Decisions that need a human (from Prompt 0, item 7)

- Which frameworks Tensor commits to in contracts for the first 5 founding clients (the plan says SOC 2 first).
- Who is the named signer for each engagement, and what credentials are shown on sign-offs.
- Retention periods per contract (the default here is 730 days) and whether clients may opt out of AI processing.
- Which model provider and region are acceptable to clients. Turn off provider-side data retention where offered.
- Whether "not applicable" decisions need quality-reviewer co-approval.
- Which connector to build second, chosen from what signed pilots actually use.
