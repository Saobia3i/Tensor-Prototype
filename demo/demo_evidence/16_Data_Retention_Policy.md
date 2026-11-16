# Data Classification and Retention Policy

Effective 2026-02-01 · Owner: CTO

| Class | Examples | Handling |
|---|---|---|
| Restricted | credentials, keys | secrets manager only |
| Confidential | customer data | encrypted at rest and in transit (TLS 1.2+, AWS KMS) |

Customer data is retained for the contract term plus 90 days, then deleted; deletion is logged. Backups expire after 35 days.
