# Information Security Policy

| | |
|---|---|
| **Policy ID** | POL-02 |
| **Version** | 1.2 |
| **Owner** | Priya Raman, Chief Technology Officer |
| **Approved** | 2024-03-11 |
| **Approved by** | Executive team (D. Okafor, P. Raman) |
| **Review cycle** | Annual |
| **Classification** | Internal |

---

## 1. Purpose

This policy establishes the requirements for protecting the confidentiality, integrity, and availability of information assets owned or processed by Meridian Health Loop, Inc. ("Meridian" or "the Company"). Meridian processes protected health information (PHI) on behalf of covered-entity customers as a business associate under HIPAA; the protections in this policy exist to safeguard that information, to satisfy Meridian's contractual and regulatory obligations, and to preserve the trust of the patients and health systems the Company serves.

## 2. Scope

This policy applies to all Meridian workforce members — employees, contractors, and temporary staff — and to all information systems, applications, networks, and data owned, operated, or processed by Meridian, including production systems hosted with cloud service providers and corporate SaaS applications. Third-party service providers that process Meridian data are governed by the Vendor Management Policy (POL-07) and applicable contractual agreements.

## 3. Roles and responsibilities

- **Executive team.** Accountable for the information security program, its funding, and acceptance of residual risk.
- **Chief Technology Officer.** Owns this policy and the overall security architecture; approves exceptions.
- **VP Engineering.** Responsible for implementing and operating technical controls in production infrastructure, including backup, recovery, and availability mechanisms.
- **Compliance & Privacy Lead.** Coordinates policy reviews, vendor security assessments, HIPAA business-associate obligations, and audit and assessment activities.
- **People Operations.** Administers workforce onboarding and offboarding procedures, background screening, and the security awareness training program.
- **All workforce members.** Required to comply with this policy and its subordinate policies and standards, to complete assigned security training, and to report suspected security incidents without delay.

## 4. Information classification

Meridian information is classified as **Restricted** (PHI and other regulated data), **Confidential** (customer business data, internal business records), or **Internal** (general company information). PHI is always Restricted. Handling requirements for each classification, including retention and disposal, are defined in the Data Retention & Disposal Policy (POL-08).

## 5. Access control

Access to Meridian information systems is granted on the principle of least privilege and the need to know.

- Access to production systems and Restricted data is limited to workforce members whose roles require it, as defined in the Access Control Policy (POL-04).
- Each workforce member is assigned unique credentials; credential sharing is prohibited except where a documented exception has been approved by the CTO.
- Privileged access (administrative access to production infrastructure, databases, or identity systems) requires management approval and is tracked.
- User access reviews are performed quarterly for all production systems, in accordance with POL-04, with results documented and exceptions remediated.
- Access for departing workforce members is removed promptly upon separation in accordance with the People Operations offboarding procedure.

## 6. Encryption

Meridian encrypts Restricted and Confidential data to protect it from unauthorized disclosure.

- All PHI is encrypted at rest under customer-managed keys (CMKs) in the cloud provider's key management service, with key policies restricted to the owning service roles. Account-default encryption does not satisfy this requirement for PHI-bearing data stores.
- All data in transit over public networks is encrypted using TLS 1.2 or higher.
- Encryption key management follows the Encryption Standard (POL-09); keys are managed through cloud-provider key management services and are not stored in application code.

## 7. System operations and monitoring

Production systems are monitored for availability, performance, and security-relevant events. Logs from production infrastructure are collected centrally and retained per POL-08. Security events and suspected incidents are handled in accordance with the Incident Response Plan (POL-03), which defines severity classification, escalation paths, containment and recovery steps, and internal and external communication requirements, including customer notification obligations arising under business associate agreements.

## 8. Change management

Changes to production systems follow the Change Management Policy (POL-05): changes are proposed, reviewed, tested, and approved before deployment, with emergency change provisions for urgent fixes. Application code changes require peer review prior to merge.

## 9. Business continuity and disaster recovery

Meridian maintains a Business Continuity & Disaster Recovery Plan (POL-10) covering the loss of production infrastructure, key personnel, or critical third-party services.

- Production data is backed up on a defined schedule, and backup integrity is verified.
- Recovery time and recovery point objectives are defined in POL-10 and reviewed with the executive team.
- The disaster recovery plan is tested at least annually, and test results are reviewed by management.
- Identified deficiencies from testing are tracked to remediation.

## 10. Security awareness training

All workforce members complete security awareness training at hire and annually thereafter. Training covers, at minimum: handling of PHI and HIPAA responsibilities, phishing and social engineering, acceptable use of Company systems, password and MFA practices, and incident reporting. People Operations tracks completion; managers are responsible for ensuring their teams complete assigned training on time.

## 11. Vendor and subprocessor security

Third parties that store, process, or transmit Meridian data are assessed and managed under the Vendor Management Policy (POL-07). Vendors with access to PHI must execute a business associate agreement before receiving any PHI, and are disclosed to customers as required by Meridian's contractual commitments.

## 12. Physical security

Meridian's production infrastructure is hosted in cloud-provider facilities; physical security of those facilities is inherited from the provider and reviewed through the provider's third-party attestations. Corporate offices require badge access, and workforce members are required to secure laptops and other portable devices in accordance with the Acceptable Use Policy (POL-01).

## 13. Exceptions

Exceptions to this policy must be requested in writing, approved by the CTO, time-bound, and recorded with compensating controls where applicable. Exceptions are reviewed at each policy review cycle.

## 14. Sanctions

Violations of this policy may result in disciplinary action up to and including termination of employment or contract, consistent with People Operations procedures and applicable law. Workforce members who report security concerns in good faith will not be subject to retaliation.

## 15. Review

This policy is reviewed at least annually by the policy owner and approved by the executive team. Material changes to Meridian's systems, obligations, or risk profile trigger an out-of-cycle review.

---

*Revision history:*

| Version | Date | Author | Notes |
|---------|------|--------|-------|
| 1.0 | 2022-06-02 | P. Raman | Initial policy |
| 1.1 | 2023-04-18 | P. Raman | Added vendor security section; aligned with BAA obligations |
| 1.2 | 2024-03-11 | P. Raman | Annual review; added encryption key management reference to POL-09 |
