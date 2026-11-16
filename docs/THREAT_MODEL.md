# Threat model (STRIDE-style, as built)

## Trust boundaries
```
 Browser ──(A: untrusted input, bearer JWT)──▶ API ──(B: tenant scope from memberships + RLS)──▶ PostgreSQL
                                               │
                                               ├──(C: untrusted files)──▶ quarantine ─▶ scanner ─▶ sandboxed parser
                                               ├──(D: model output = proposal)──▶ Model provider (no tools)
                                               └──(E: read-only, client-approved token)──▶ GitHub
 Staff (F: per-tenant assignments, MFA for privileged actions)   Ops (G: owner DB role only for migrations/maintenance)
```

| Threat | Boundary | Control in this build | Residual / next step |
|---|---|---|---|
| Cross-tenant read via IDOR | A, B | 404 on out-of-scope IDs; tenant from memberships only; RLS on 24 tables with a non-owner app role | Pen test; add RLS regression to every new table (migration review checklist) |
| Cross-tenant leak via background job | B | Jobs run in an RLS session scoped to the job's tenant only | — |
| Cross-tenant leak via retrieval / AI context | B, D | Retrieval filtered by tenant + engagement + `approved` before ranking | — |
| Privilege escalation (client acts as staff) | A | Permission matrix per role; staff-only checks on review, verify, sign | Periodic access review of memberships |
| Stolen staff session signs or releases | F | MFA (`amr`) required for privileged actions; independent report approver; named-signer rule | Phishing-resistant MFA at the IdP; short token TTL |
| Prompt injection in evidence | C, D | Content delimited as untrusted; no tools; output validated; no state change without a human | Expand the injection corpus (OCR, hidden text in PDFs) |
| Hallucinated / fabricated citations | D | Verbatim-quote and chunk-ID validation; bounded retry; rejection recorded | — |
| Model claims "compliant" | D | Banned-claim detector → warning; conclusions only via human sign-off | — |
| Malicious upload (malware, zip bomb, parser exploit) | C | Magic-byte validation; zip-bomb guard; ClamAV; isolated child interpreter with CPU/mem/fd/no-write rlimits and timeout | Run parsers in a separate container with no network (seccomp) |
| Evidence tampering | B | SHA-256 at ingest; versions instead of overwrite; sign-offs hash content; restore check verifies hashes | Object-lock (WORM) on the evidence bucket |
| Audit log tampering | B, G | Append-only trigger blocks UPDATE/DELETE even for the owner role; app role lacks the grants | Ship logs to an external immutable store |
| Connector token theft | E | Fernet-encrypted secret table under RLS (cloud secret manager in prod); never logged or returned; client can revoke | Rotate keys; KMS envelope encryption |
| Over-scoped connector | E | Refuses tokens with admin/write scopes; read-only endpoints; client approval recorded | — |
| Cost abuse / DoS | A, D | Per-user rate limits (uploads, AI jobs, syncs); per-job and per-tenant daily AI budgets; upload size caps | Redis-backed limiter for multiple replicas; WAF |
| Sensitive data in logs | all | JSON logs with key and pattern redaction; document text never logged (tested) | Log access controls |
| CSV formula injection in exports | A | Leading `= + - @` escaped | — |
| XSS via report/snapshot HTML | A | HTML-escaped rendering; CSP `default-src 'none'`; sandboxed iframe in the UI | — |
| Dev login left enabled | A | `/v1/dev/*` returns 404 outside local; config refuses to start with a dev secret outside local | — |
