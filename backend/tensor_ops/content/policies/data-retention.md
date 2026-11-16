---
title: Data Classification and Retention Policy
controls: TC-DP-01, TC-CR-01
---
# Data Classification and Retention Policy — {{company_name}}

**Effective:** {{effective_date}} · **Owner:** {{security_owner}} · **Review:** every 12 months

| Class | Examples | Handling |
|---|---|---|
| Restricted | Credentials, encryption keys | Secrets manager only; never in code, chat or email |
| Confidential | Customer data, contracts, source code | Encrypted at rest and in transit; access on need to know |
| Internal | Internal docs, plans | Company systems only |
| Public | Marketing site | No restrictions |

- Customer data is retained for the contract term plus {{customer_data_retention}}, then deleted.
- Deletion is performed in production systems and expires from backups within {{backup_retention}}.
- Data is encrypted in transit (TLS 1.2+) and at rest using {{cloud_provider}} managed keys.
