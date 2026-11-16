---
title: Change Management and Secure Development Policy
controls: TC-CM-01, TC-SD-01, TC-CM-02
---
# Change Management and Secure Development Policy — {{company_name}}

**Effective:** {{effective_date}} · **Owner:** {{security_owner}} · **Review:** every 12 months

1. All production code changes are made through pull requests in {{code_host}}.
2. The default branch is protected: direct pushes are blocked and at least one reviewer other than the author must approve.
3. Automated tests, dependency scanning and secret scanning run in CI before merge.
4. Infrastructure changes are made as code where possible and follow the same review process.
5. Emergency changes may be deployed with after-the-fact review within two business days, recorded in {{ticketing_tool}}.
6. Developers follow the secure coding guideline, including input validation, output encoding and secrets management.
7. Production data is not copied into development or test environments.
