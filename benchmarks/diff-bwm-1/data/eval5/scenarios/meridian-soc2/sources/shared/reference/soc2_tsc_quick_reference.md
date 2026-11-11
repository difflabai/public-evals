# Trust Services Criteria (2017 framework, 2022 points-of-focus revision) - practitioner quick reference

A working crib sheet for readiness and examination teams. Topic labels only, in our own words - always draft against the published criteria and points of focus, never against this sheet. Identifiers follow the 2017 Trust Services Criteria; the 2022 revision changed points of focus, not identifiers.

## Structure

The criteria are organised as **common criteria (CC-series)**, which apply to every SOC 2 examination and on their own make up the Security category, plus four optional categories - Availability, Processing Integrity, Confidentiality and Privacy - that are added when the service organisation's commitments and system requirements call for them. There are **33 common criteria** in total: CC1.1-CC1.5, CC2.1-CC2.3, CC3.1-CC3.4, CC4.1-CC4.2, CC5.1-CC5.3, CC6.1-CC6.8, CC7.1-CC7.5, CC8.1, and CC9.1-CC9.2.

## Common criteria

### CC1 - Control environment (5 criteria)

- **CC1.1** - Ethics and integrity: code of conduct, tone at the top, how violations are handled.
- **CC1.2** - Board oversight: an oversight body independent of management that actually reviews controls.
- **CC1.3** - Organisation design: reporting lines, roles and who holds which authority.
- **CC1.4** - People: hiring, training and keeping competent staff.
- **CC1.5** - Accountability: owners are named and answerable for the controls they run.

### CC2 - Communication and information (3 criteria)

- **CC2.1** - Information quality: the data that controls depend on is relevant and reliable.
- **CC2.2** - Internal communication: staff know their control responsibilities (policies, training, channels).
- **CC2.3** - External communication: customers and other outside parties are told what affects them.

### CC3 - Risk assessment (4 criteria)

- **CC3.1** - Objectives: stated clearly enough that risks to them can be identified.
- **CC3.2** - Risk identification and analysis across the organisation.
- **CC3.3** - Fraud risk is considered explicitly.
- **CC3.4** - Change-driven risk: significant changes (systems, vendors, people) trigger reassessment.

### CC4 - Monitoring activities (2 criteria)

- **CC4.1** - Ongoing and periodic evaluations show controls exist and work.
- **CC4.2** - Deficiencies are reported promptly to whoever must fix them, including leadership where needed.

### CC5 - Control activities (3 criteria)

- **CC5.1** - Controls are chosen to bring identified risks down to an acceptable level.
- **CC5.2** - General IT controls support the business controls.
- **CC5.3** - Policies say what is expected; procedures put the policies into practice.

### CC6 - Logical and physical access controls (8 criteria)

- **CC6.1** - Access architecture: identification, authentication, encryption at rest, access-control design.
- **CC6.2** - User provisioning and removal: accounts are approved before issue and removed when no longer authorised.
- **CC6.3** - Role-based access: least privilege and segregation of duties when access is granted, changed or revoked.
- **CC6.4** - Physical access to facilities and hardware.
- **CC6.5** - Media disposal: protections stay on until data is unrecoverable.
- **CC6.6** - Perimeter and remote access: threats from outside the system boundary, authentication strength.
- **CC6.7** - Data in motion: encryption in transit, removable media, endpoints, data-loss channels.
- **CC6.8** - Malware: prevention and detection of unauthorised or malicious software.

### CC7 - System operations (5 criteria)

- **CC7.1** - Configuration and vulnerability monitoring.
- **CC7.2** - Anomaly monitoring of system components and evaluation of possible security events.
- **CC7.3** - Event triage: deciding whether an event is an incident, and acting on it.
- **CC7.4** - Incident response: a defined program to contain, remediate and communicate incidents.
- **CC7.5** - Recovery from incidents.

### CC8 - Change management (1 criterion)

- **CC8.1** - Changes to infrastructure, data, software and procedures are authorised, tested, approved and documented.

### CC9 - Risk mitigation (2 criteria)

- **CC9.1** - Business-disruption risk: mitigation activities for potential disruptions.
- **CC9.2** - Vendor and partner risk: due diligence, contractual security and confidentiality terms, monitoring.

## Availability (3 criteria)

- **A1.1** - Capacity: current capacity and usage are tracked so demand can be met.
- **A1.2** - Resilience: environmental protections, backups and recovery infrastructure.
- **A1.3** - Recovery testing: recovery-plan procedures are tested.

## Confidentiality (2 criteria)

- **C1.1** - Confidential information is identified and protected.
- **C1.2** - Confidential information is disposed of as committed (including return-or-destroy terms).

## A note on the Privacy category

The Privacy criteria (the P-series: P1.1 through P8.1) are for organisations that make their own commitments to data subjects about collecting, using, keeping, disclosing and disposing of personal information - usually through a consumer-facing privacy notice. When an organisation handles personal information only on behalf of its customers (for example PHI under HIPAA business associate agreements), those obligations are normally covered through the Confidentiality category and the relevant regulatory program, not by scoping in Privacy. Scoping Privacy in without entity-level privacy-notice commitments creates criteria the organisation has no basis to meet.

## Practical reminders

- Category selection follows the organisation's service commitments and system requirements - contracts and SLAs, not sales preference.
- Every in-scope criterion needs at least one mapped control in the system description and the matrix; "not applicable" is itself an assertion that needs a rationale.
- Criterion identifiers (CC-, A-, C- and P-series) are stable across the 2017 framework and the 2022 revision.
