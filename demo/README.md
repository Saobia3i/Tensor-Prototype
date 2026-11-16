# Demo evidence pack — Acme Analytics (synthetic)

Everything here is invented for demos and tests. No real company or person.

## demo_evidence/ — upload these as evidence (drag all of them onto the Evidence tab)

| File | What the AI should do |
|---|---|
| 01_Information_Security_Policy_v3.pdf | Map to governance controls (policy, security owner, management review) |
| 02_Okta_MFA_Enforcement_Export.json | Map to MFA / SSO controls |
| 03_Q3_2026_User_Access_Review.xlsx | Map to quarterly access review and least privilege |
| 04_GitHub_Branch_Protection_Settings.json | Map to change management (PR review) and MFA (org 2FA) |
| 05_Change_Management_Procedure.docx | Map to change management and secure SDLC |
| 06_Security_Awareness_Training_Completion.csv | Map to security training |
| 07_Incident_Response_Plan_and_Tabletop_2026.pdf | Map to incident response |
| 08_AWS_Backup_and_Restore_Test.txt | Map to backups / restore testing |
| 09_Vendor_Risk_Register.xlsx | Map to vendor risk management |
| 10_Vulnerability_Scan_Summary_Sep2026.pdf | Map to vulnerability management |
| 11_Laptop_MDM_Compliance_Report.csv | Map to endpoint protection and asset inventory |
| 12_Risk_Assessment_2026.docx | Map to risk assessment |
| 13_Penetration_Test_Report_2023.pdf | Map to pentest **but flag as stale** (older than 12 months) |
| 14_Team_Offsite_Agenda.txt | **Insufficient evidence**, no control supported |
| 15_Vendor_Contract_with_hidden_instructions.docx | **Prompt-injection test**: flagged, nothing approved automatically |
| 16_Data_Retention_Policy.md | Map to data classification / retention and encryption |

Upload the same file twice to see duplicate detection.

## imports/ — use in the Import centre

| File | Kind | What you'll see in the validation log |
|---|---|---|
| ACME_ISO27001_Gap_Assessment.xlsx | Gap assessment (ISO 27001 engagement) | Sheet auto-detected ("Requirements"), 1 duplicate ref, 1 unknown ref (A.8.99), 1 invalid value ("Maybe"), 1 not assessed |
| ACME_SOC2_Gap_Assessment.csv | Gap assessment (SOC 2 engagement) | Flexible headers ("TSC Ref", "Status", "Control Owner", "Risk Rating") |
| ACME_SOC2_Type2_Test_Log.xlsx | Test log | 9 tests; 2 fails and 1 pass-with-exception create exceptions EXC-001..003 and remediation tasks |

Regenerate: `python demo/generate_demo_pack.py`
