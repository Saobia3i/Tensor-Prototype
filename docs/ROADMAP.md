# Rollout plan, aligned to the business plan

| Plan phase (deck) | Months | Use of this platform | Build / harden |
|---|---|---|---|
| **Proof**: 5 founding clients | 1–3 | Snapshots for every lead; engagements, evidence pipeline, review queue, sign-off, policies, weekly status. **AI provider: mock or Claude on internal docs only** until gate 5 passes. Log time on every control. | Close gates 3, 4, 6 and 7 (IdP, cloud deploy, legal, auditor fees). Run Tensor itself as tenant zero. |
| **Repeatable**: 3 clients/month | 4–9 | Turn on Claude for evidence review after the expert eval passes. Questionnaires for clients' enterprise deals. First white-label partner. | Pen test (gate 1). Expert-validated mappings (gate 2). Second connector chosen from pilots (Google Workspace or AWS). OpenTelemetry. |
| **Scale**: 6 direct + 2 white-label/month | 10–18 | Stay Compliant retainer workflows (quarterly access reviews, questionnaires in 3 days). Read the metrics dashboard: are hours per client trending toward 65? | Tensor's own SOC 2 audit (month 12) using this platform's own evidence. Recurring-task scheduler for retainer controls. Partner portal polish. |
| **Push to $1M ARR** | 19–24 | Year-2 automation: use the reviewer edit/accept data and per-control hours to decide which controls to automate next. | Model routing (cheaper model for classification once eval parity is shown), more connectors, ISO 27001 SoA builder. |

**Rule:** automate a control only when the metrics show (a) it costs real hours and (b) the AI's accept rate on it is high
in held-out evaluation. Experts keep signing every output.
