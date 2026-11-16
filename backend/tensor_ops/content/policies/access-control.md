---
title: Access Control Policy
controls: TC-AC-01, TC-AC-02, TC-AC-03, TC-AC-04, TC-HR-04
---
# Access Control Policy — {{company_name}}

**Effective:** {{effective_date}} · **Owner:** {{security_owner}} · **Review:** every 12 months

## 1. Principles
- Access follows **least privilege** and **need to know**.
- Every person has an individual account; shared accounts are not allowed except documented break-glass accounts.
- Workforce identities are managed centrally in **{{identity_provider}}**; key applications use single sign-on.

## 2. Authentication
- Multi-factor authentication is **enforced** for {{identity_provider}}, {{cloud_provider}} consoles, {{code_host}} and any remote access.
- Passwords follow the identity provider's policy; credentials are never shared or stored in code.

## 3. Provisioning and removal
- Access is requested through {{ticketing_tool}} and approved by the system owner.
- Access is removed **within one business day** of a person leaving, and on role change.

## 4. Privileged access
- Administrative access is limited to named individuals listed in the admin register and reviewed quarterly.

## 5. Access reviews
- System owners review user access to production, {{cloud_provider}}, {{code_host}} and {{identity_provider}} **every quarter**.
- Exceptions are fixed within 10 business days and the review is recorded.
