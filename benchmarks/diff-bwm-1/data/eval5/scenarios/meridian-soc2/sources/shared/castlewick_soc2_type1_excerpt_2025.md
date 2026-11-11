# SOC 2 Type I Report — Section III excerpt (System Description & Selected Controls), as of 2025-06-30

**Castlewick Advisory LLP**
**Report on Meridian Health Loop, Inc.'s Description of Its Patient Workflow Platform and the Suitability of the Design of Its Controls**
**As of 2025-06-30 — issued 2025-08-22**
**Engagement Manager: Simone Aldana**

> **Important — nature of this report.** This is a Type I examination. Castlewick's opinion addresses (1) whether management's description of the system fairly presents the system as designed and implemented **as of 2025-06-30**, and (2) whether the controls stated in the description were **suitably designed** as of that date to provide reasonable assurance that the applicable trust services criteria would be met **if the controls operated effectively**. A Type I examination does not include tests of the operating effectiveness of controls and provides no assurance regarding any period before or after 2025-06-30. Readers evaluating the current state of controls should not rely on this report for any date after 2025-06-30.

---

## III.1 Overview of the system

Meridian Health Loop, Inc. ("Meridian") provides a patient communications workflow platform delivered over SMS, web, and voice channels on behalf of health-system customers. Core workflow templates as of the report date include emergency department discharge follow-up, clinical study enrollment, and appointment check-in. The platform is hosted in Amazon Web Services (us-east-1) and operated by Meridian's engineering organization. Meridian acts as a business associate to its health-system customers and processes protected health information in the course of delivering the services.

## III.2 Selected control descriptions

The controls below are excerpted from management's description. Castlewick's design-suitability opinion applies to these controls as described, as of 2025-06-30.

### Logical access

**CA-04 (User access provisioning).** Access to production systems is provisioned through a documented request-and-approval workflow. Access rights are assigned according to job role, following the principle of least privilege.

**CA-05 (Access reviews).** User access reviews are performed quarterly for all production systems, with results documented and exceptions remediated within 30 days. Review results are retained as evidence and reviewed by the VP Engineering.

**CA-06 (Deprovisioning).** Terminated workforce members are deprovisioned within one business day. People Operations notifies IT of departures, and IT confirms removal of access across production systems, identity provider, and VPN.

### Security operations

**CA-11 (Incident response).** Meridian maintains a documented incident response plan. Incidents are logged, triaged by severity, and resolved by the engineering on-call rotation; post-incident reviews are performed for significant incidents and tracked to closure.

**CA-12 (Vulnerability management).** External penetration testing is performed annually by an independent third party, and findings are remediated according to severity-based timelines.

### Availability and continuity

**CA-15 (Business continuity and disaster recovery).** The disaster recovery plan is tested at least annually, and test results are reviewed by management. Backup snapshots of production databases are taken nightly and stored in a separate storage location.

### Human resources

**CA-18 (Security awareness training).** Security awareness training is completed by 100% of workforce members annually. New hires complete training as part of onboarding, and completion is tracked by People Operations.

**CA-19 (Confidentiality obligations).** All workforce members execute confidentiality agreements at hire.

### Vendor management

**CA-21 (Vendor risk).** Vendors with access to sensitive data are inventoried, risk-rated, and bound by contractual security and confidentiality obligations, including business associate agreements where PHI is involved.

## III.3 Complementary user entity controls

Management's description assumes that user entities (health-system customers) maintain controls over the accuracy of patient contact data furnished to the platform, the authorization of workflow templates placed into production on their behalf, and the timely notification to Meridian of personnel changes affecting their administrative users. Exceptions arising from the absence of such user-entity controls are outside the scope of this report.

## III.4 Basis of examination

Castlewick's procedures consisted of inquiry of management, inspection of the documented policies and procedures supporting each control statement, and observation of selected system configurations, in each case as of 2025-06-30. Castlewick did not perform tests of controls over any period of time, and expresses no opinion on operating effectiveness. Management is responsible for the completeness, accuracy, and method of presentation of the description.

This excerpt is provided for informational purposes to parties familiar with SOC 2 reporting. It omits Section I (Independent Service Auditor's Report), Section II (Management's Assertion), the balance of Section III, and all appendices. Reproduction or citation of this excerpt without the full report context is at the reader's risk.

---

**Castlewick Advisory LLP**
Simone Aldana, Engagement Manager
2025-08-22
