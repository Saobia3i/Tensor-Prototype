---
title: Business Continuity and Disaster Recovery Plan
controls: TC-BC-01, TC-BC-02
---
# Business Continuity and Disaster Recovery Plan — {{company_name}}

**Effective:** {{effective_date}} · **Owner:** {{security_owner}} · **Review and test:** every 12 months

- **Critical services:** the customer-facing application and its data stores on {{cloud_provider}}.
- **Recovery objectives:** recovery time objective (RTO) {{rto}}; recovery point objective (RPO) {{rpo}}.
- **Backups:** production databases are backed up automatically; backups are encrypted and retained for {{backup_retention}}.
- **Restore test:** a restore is tested at least once a year and the result recorded.
- **Roles:** {{security_owner}} coordinates recovery; {{approver}} communicates with customers.
- **Communication:** customers are informed through {{status_page}} during major disruptions.
