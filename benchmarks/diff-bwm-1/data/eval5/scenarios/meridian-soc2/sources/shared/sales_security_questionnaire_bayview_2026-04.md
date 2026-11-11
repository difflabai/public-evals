# Bayview Regional Health Network — Vendor Security Questionnaire

**Vendor:** Meridian Health Loop, Inc.
**Product under review:** Patient Workflow Platform (SMS / web / voice)
**Submitted:** 2026-04-17
**Prepared by:** Marcus Webb (VP Sales, Meridian) with input from J. Park (DevOps)
**Bayview reviewer:** Vendor Risk Office

*Instructions from Bayview: answer each item Yes/No/Partial with a brief narrative. Attach evidence where indicated. Answers become contractual representations upon countersignature of the Pilot Services Agreement.*

---

### A. Governance and policy

**Q1.** Does the vendor maintain a documented information security program? **Yes.** Meridian maintains a documented information security policy and supporting policies covering access control, change management, incident response, and vendor management.

**Q2.** Is there a named individual accountable for security? **Yes.** Security is overseen by the CTO with support from a fractional vCISO engaged in 2026.

**Q3.** Are security policies reviewed and approved on a defined cycle? **Yes.** Policies are reviewed on an annual cycle.

**Q4.** Does the vendor carry cyber-liability insurance? **Yes.** Coverage is in force; certificate available on request.

**Q5.** Has the vendor completed a SOC 2 examination? **Partial.** Meridian completed a SOC 2 Type I examination as of 2025-06-30 (Castlewick Advisory) and has engaged an assessor to prepare for a Type II examination with a report targeted for Q1 2027.

### B. Access control and authentication

**Q6.** Is access to production systems provisioned by role with approval? **Yes.** Access follows a request-and-approval workflow assigned by job role.

**Q7.** Are user access reviews performed periodically? **Yes.** Access reviews are performed on a quarterly cadence.

**Q8.** Are all subprocessors disclosed to customers? **Partial.** All subprocessors are disclosed at Bayview's request, and PHI-handling subprocessors operate under business associate agreements.

**Q9.** Is least privilege enforced? **Yes.**

**Q10.** Are privileged accounts restricted and monitored? **Yes.** Administrative access is limited to the engineering team.

**Q11.** Is single sign-on used for internal applications? **Yes.** Okta SSO fronts internal SaaS applications.

**Q12.** Is multi-factor authentication enforced for remote access? **Yes.** Multi-factor authentication is enforced for 100% of remote access to production systems.

**Q13.** Are passwords subject to a complexity and rotation policy? **Yes.**

### C. Data protection

**Q14.** Is data encrypted in transit? **Yes.** TLS 1.2+ on all external connections.

**Q15.** Is data encrypted at rest? **Yes.** Production data stores are encrypted at rest.

**Q16.** Is PHI segregated from non-PHI environments? **Partial.** Production PHI is logically segregated; non-production environments use de-identified or synthetic data.

**Q17.** Is there a data classification scheme? **Yes.**

**Q18.** Are backups encrypted and tested? **Partial.** Backups are taken nightly; restore testing is performed periodically.

### D. Vulnerability and threat management

**Q19.** When was the most recent third-party penetration test, and what were the results? **Yes.** Our most recent third-party penetration test identified no critical findings; all issues were remediated within SLA.

**Q20.** Is vulnerability scanning performed? **Yes.** Automated scanning runs on a recurring basis.

**Q21.** Is there a patch-management process? **Yes.**

**Q22.** Is centralized logging and monitoring in place? **Yes.** Logs are aggregated to a monitoring platform with alerting.

**Q23.** Is there endpoint protection on workforce devices? **Partial.** Corporate devices are managed with endpoint management and disk encryption.

### E. Incident response and breach notification

**Q24.** Is there a documented incident response plan? **Yes.** Reviewed 2026-01.

**Q25.** Are incidents logged and triaged? **Yes.**

**Q26.** What is the customer breach-notification commitment? **Yes.** Meridian notifies affected customers of security incidents involving their data in accordance with the applicable business associate agreement.

### F. Availability and continuity

**Q27.** What uptime commitment does the vendor offer? **Yes.** We commit to a 99.99% uptime SLA for all customers.

**Q28.** Is there redundancy across availability zones? **Yes.** The platform runs across multiple AWS availability zones.

**Q29.** Is there a documented disaster recovery plan? **Yes.** A DR plan is maintained.

**Q30.** Is there a status page for incident communication? **Yes.**

### G. Personnel and training

**Q31.** Is security awareness training provided? **Yes.** Security awareness training is completed annually by all staff.

**Q32.** Are background checks performed at hire? **Yes.**

**Q33.** Do workforce members sign confidentiality agreements? **Yes.**

**Q34.** Is there an onboarding and offboarding process for access? **Yes.**

### H. Program maturity and roadmap

**Q35.** Are there any known gaps or planned improvements relevant to this engagement? **Partial.** Formal DR test: scheduled for 2026. MFA coverage extension to legacy remote-access paths and completion of the current-cycle training rollout are in progress as part of the Type II readiness program.

**Q36.** Is a bug-bounty or responsible-disclosure program in place? **No.** Not at this time; under consideration.

**Q37.** Are data-retention periods defined per data type? **Yes.**

**Q38.** Does the vendor support customer data export and deletion? **Yes.**

**Q39.** Is code deployed through a controlled CI/CD pipeline with review? **Yes.** Changes go through pull-request review and automated pipelines.

**Q40.** Does the vendor use production data in test environments? **No.**

---

> **Follow-up note — R. Okonjo (Bayview Vendor Risk), 2026-05-28:**
> Marcus — thanks for the submission. One item before we can close review: please reconcile **Q27 against MSA §7.2** of the St. Aldric agreement you referenced, which states 99.9% monthly uptime. We need the questionnaire and the executed contract to agree on the number we'd be relying on. Also please attach the penetration-test report referenced in Q19 so we can confirm the finding counts. — R.O.
