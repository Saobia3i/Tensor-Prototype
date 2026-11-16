# Runbooks

## Deploy
1. CI is green on `main`: lint, SAST, dependency audit, migrations round-trip, tests, eval safety gate, image scan.
2. Back up: confirm a PITR window and take a snapshot.
3. Run migrations as the **owner** role: `alembic upgrade head` (one-off task). Never run migrations from the API container.
4. Roll out API and workers progressively (one replica first). Watch error rate, queue age and 5xx.
5. Smoke test: `/readyz`, sign in, open an engagement, upload a synthetic file to tenant zero, and confirm it reaches `proposed`.

## Roll back
- Application: redeploy the previous image tag. Migrations are additive by convention. If a migration must be reverted, run
  `alembic downgrade -1` only after confirming the downgrade path in staging.
- Data: restore with PITR to a new instance, run `deploy/restore_check.py` against it, then switch over.

## Restore drill (quarterly)
`python deploy/restore_check.py --pg-host <host> --source tensor --storage <mounted evidence store>`. Record the output in
tenant zero as evidence for Tensor's own TC-BC-01.

## Incident: suspected cross-tenant exposure
1. Contain: revoke the affected sessions and memberships, and disable the endpoint behind a feature flag or route block.
2. Preserve: export `audit_events` for the window (by correlation ID) to immutable storage. Do not modify rows.
3. Assess: identify the affected tenants and objects from audit events (`evidence.viewed`, `*.downloaded`).
4. Notify: counsel decides on client notification under the DPA timelines.
5. Fix and add a regression test to `test_tenant_isolation.py`, then hold a post-incident review.

## Incident: connector token compromise
Revoke in the console (this destroys the secret), ask the client to revoke the token at GitHub, review `connector.*` audit events,
then re-issue a new read-only token with client approval.

## Incident: malicious upload detected
The item stays `rejected_security` in quarantine (it is not parsed). Notify the client contact and keep the object for analysis,
then delete it through the deletion workflow.

## Incident: model provider outage or misbehaviour
Set `TENSOR_AI_PROVIDER=mock` (or pause the AI worker pool). Evidence still flows to `expert_review` for manual mapping.
If outputs degrade, run `python -m evals.run_evals` against the provider and compare with the last report.

## Data deletion request
Client admin or delivery lead creates the request, a different authorised person approves it, and the worker executes and verifies.
The result is stored on the request (`verified: true`). Backups expire under the backup-retention policy. Document this in the DPA.
