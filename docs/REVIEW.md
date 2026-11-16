# Review: the AI-suggested engineering pack vs. the TensorInfoSec business plan

**Verdict: the pack is sound on safety but aimed at the wrong business shape.** Its guardrails are
right for Tensor: experts own conclusions, every AI output cites evidence, tenants are isolated,
standards text is licensed, and nothing claims certification. Those guardrails are also Tensor's sales story
after the Delve episode, so they stay. But the pack describes a generic SOC 2 operations tool for a
well-staffed engineering team. It leaves out the parts of the plan that make money (the free
Snapshot funnel, questionnaires inside the retainer, white-label partners, the named signer, hours per client)
and it adds infrastructure a five-person Dhaka team would struggle to run.

This repository implements the corrected scope below.

## What the pack gets right (kept)

| Pack principle | Why it matters for Tensor's plan |
|---|---|
| AI proposes, humans approve; AI never marks a control "compliant" | Matches the plan's "a named expert signs every output" promise |
| Citations to exact evidence spans, fabricated citations rejected | Matches "evidence comes only from the client's real systems with an audit trail" |
| Tenant isolation as a security boundary, tested with negative tests | Required for multi-client delivery and for Tensor's own SOC 2 in month 12 |
| No copyrighted SOC 2 / ISO text in the product | AICPA and ISO texts are licensed; the control library here uses Tensor's own wording and only cites requirement IDs |
| Modular monolith, not microservices | Right size for the team |
| Append-only audit trail, versioned evidence, SHA-256 hashes | Gives an auditor-ready provenance story |

## What is wrong or missing (changed in this build)

1. **No Readiness Snapshot module.** The plan's whole funnel books a free Snapshot (about 34 a month in year 2,
   delivered within 3 business days) with gaps, a dated plan, an all-in quote and the signer's name. The pack
   never mentions it. **Added:** intake questionnaire, then deterministic gap scoring, a dated 8-week plan, a quote from the
   plan's price book, and expert sign-off before it is sent. This is probably the biggest time saver in year 1.

2. **Questionnaires are treated as a later nice-to-have.** The Stay Compliant retainer ($750–$2,500/month)
   promises up to six questionnaires answered within 3 days. That is recurring revenue and highly automatable.
   **Changed:** questionnaire drafting grounded only in *approved* evidence and policies ships in the first AI phase, with
   per-answer citations and expert approval.

3. **The "named signer" differentiator is not modelled.** The pack has reviewers, but no signed attestation
   a client or auditor can see. **Added:** immutable `signoffs` (signer, title, statement, SHA-256 of the
   exact content signed), a signer assigned on every engagement, and a client-visible trust ledger.

4. **No white-label / partner model.** Partners and white-label account for 78 of the 161 clients in the plan's two-year
   model. **Added:** partner organisations that own client tenants, partner-branded reports, and a
   `partner_admin` role scoped to that partner's clients only.

5. **"Map everything" in year 1 is not captured.** Year 2 automation depends on year-1 data: hours per control,
   templates, and reviewer edits. The pack only tracks metrics in phase 5. **Changed:** time entries per
   engagement control are recorded from day one, along with AI-assisted flags, reviewer accept and edit rates, AI cost per
   engagement, and contribution margin against the plan's targets (100 h in year 1, 65 h in year 2, $9/h engineers, $75/h experts).

6. **Policy templates are missing.** The plan says "templates for policies, never for conclusions." **Added:** a
   Tensor-authored policy template library rendered with client parameters. Drafts stay drafts until
   client and expert approval.

7. **Infrastructure is oversized for the team.** Temporal, a managed OIDC IdP from day one, Terraform with three cloud
   accounts, and a vector DB together mean too much operational load before revenue. **Changed:** a Postgres job queue
   (`FOR UPDATE SKIP LOCKED`, idempotency keys, bounded retries) behind an interface that Temporal can replace later.
   Postgres full-text retrieval only. OIDC verification is built in and required outside local. Storage, scanner, secrets and AI
   sit behind adapters, so cloud services drop in without rewrites.

8. **The endpoint/EDR phase does not fit the plan.** The plan resells 24/7 monitoring through a partner and does not
   build it. **Dropped** from the roadmap. Expansion revenue in the plan comes from pentests, ISO 42001, PCI DSS and the
   retainer, so the control library covers SOC 2, ISO 27001:2022, ISO 42001 and PCI DSS v4 references.

9. **The auditor is the plan's main referral channel but is barely served.** **Added:** a time-boxed read-only
   auditor role that sees only approved evidence, sign-offs and released reports, with every download logged.

10. **Cross-border staff access is not addressed.** US buyers will ask who in Dhaka can see their data. **Added:**
    least-privilege per-tenant assignments (no global staff access), audit of every evidence view and download, and
    short-lived download links. Contract terms (DPA, subprocessors, data residency) still need counsel.

## Business-plan risks the software cannot fix (need your decision)

- **Simulated buyer panels are not demand.** The 25% yes rate and the 11 of 20 retainer uptake come from synthetic
  panels. The first five founding clients are the real test. Build only phases 1–2 before they sign.
- **The 80% automation target vs. the 35% (100 → 65 h) in the model.** Keep the model at 35% until the metrics dashboard
  shows real numbers. The software now measures this directly.
- **Your own SOC 2 in month 12.** The platform holds client evidence, so enterprise buyers will ask about Tensor's own
  controls well before month 12. Run Tensor as tenant zero in this system from day one.
- **Auditor fee in the "all-in quote."** The quote engine needs your partner auditor's actual price list. The defaults in
  `pricing.py` are placeholders and are labelled as such.
- **Licensing.** Confirm whether you will license the AICPA TSC and ISO texts for internal reference. The product
  ships without them.
