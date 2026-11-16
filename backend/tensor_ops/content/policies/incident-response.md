---
title: Incident Response Plan
controls: TC-IR-01, TC-LM-01
---
# Incident Response Plan — {{company_name}}

**Effective:** {{effective_date}} · **Incident lead:** {{security_owner}} · **Review and tabletop test:** every 12 months

## 1. Report
Anyone who suspects a security incident reports it immediately to {{security_owner}} or via {{incident_channel}}.

## 2. Triage and severity
| Severity | Examples | Initial response |
|---|---|---|
| High | Confirmed customer data exposure, production compromise | Within 1 hour |
| Medium | Suspicious access, malware on a laptop | Within 1 business day |
| Low | Policy violation, failed phishing attempt | Within 3 business days |

## 3. Response steps
1. Contain: limit the damage (revoke credentials, isolate systems) with the incident lead's approval.
2. Preserve evidence: keep logs and affected artefacts before changing systems.
3. Eradicate and recover: remove the cause, restore from known-good state, monitor.
4. Notify: legal counsel decides on customer and regulatory notifications within contractual and legal deadlines.
5. Learn: hold a post-incident review within 10 business days and track actions to completion.

## 4. Records
Every incident is logged with timeline, decisions, impact and follow-up actions.
