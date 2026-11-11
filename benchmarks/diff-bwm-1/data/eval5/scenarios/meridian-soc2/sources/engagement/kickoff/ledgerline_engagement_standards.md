# Ledgerline Assurance — Readiness Engagement Standards & Scope Memo (Meridian Health Loop)

**Prepared by:** Katherine Liu, Engagement Director
**Date:** 2026-06-01
**Distribution:** Engagement team; Meridian executive sponsors (D. Okafor, P. Raman)
**Status:** Governing document for the Meridian readiness engagement. Read before drafting anything.

This memo does two things. Part A fixes the scope of the readiness assessment and the proposed Type II examination it prepares for. Part B fixes the deliverable standards — the house style that every Ledgerline readiness deliverable follows, without exception. Part C states the evidence hierarchy we apply when sources disagree. If you find yourself departing from any of this, come talk to me first.

---

## Part A — Scope

### A.1 Proposed examination and review period

Meridian's two flagship prospects have made a SOC 2 Type II report a condition of contract expansion, with a Q1 2027 gate. Working backward from that gate, we agreed at kickoff on a proposed Type II review period of **2026-10-01 through 2027-03-31** — six months, closing in time for report issuance before the prospects' deadline. The practical consequence, which the remediation roadmap must respect: any control that is not designed, implemented, and operating by 2026-10-01 cannot accumulate a clean six months of operating evidence. Remediation of priority items must therefore complete before the review period opens, not during it.

### A.2 Trust Services Criteria in scope

Scoping follows Meridian's actual customer commitments, not its marketing aspirations. The calls:

- **Security (common criteria).** All common criteria, CC1.1 through CC9.2, are in scope. This is non-negotiable for any SOC 2 examination and no rationale beyond that is required.
- **Availability (A1.1–A1.3): IN SCOPE.** Meridian has an executed customer uptime commitment — the St. Aldric MSA carries a monthly uptime service level with service credits (§7.2 of the executed agreement). An executed availability commitment with financial teeth pulls the Availability category in. The MSA further obligates Meridian (§9.2) to deliver a Type II report covering availability.
- **Confidentiality (C1.1–C1.2): IN SCOPE.** The St. Aldric MSA designates all Customer Data as Confidential Information (§8.1) and imposes return-or-destroy obligations at termination (§8.4); the Bayview BAA layers confidentiality and disposal obligations on PHI. Contractual confidentiality and return-or-destroy commitments pull the Confidentiality category in.
- **Privacy (P-series): OUT OF SCOPE for this period.** Meridian makes no consumer-facing privacy-notice commitments: it is a business associate processing PHI on behalf of covered entities, and its personal-information obligations are governed by the HIPAA business-associate program, which we assess as a separate overlay, not through the SOC 2 Privacy criteria. I am aware there is internal appetite at Meridian to include Privacy "for marketing value." Do not let a prospect checkbox pull Privacy in; revisit at renewal. Scoping in a category the entity has made no commitments against manufactures audit risk and dilutes the remediation program.
- **Processing Integrity: OUT OF SCOPE.** No customer contract commits Meridian to processing-integrity service commitments in a form that maps to the PI criteria for this period. Revisit if the clinical-study-enrollment workflows acquire accuracy/completeness commitments in future contracts.

Net: 33 common criteria plus Availability plus Confidentiality. Every deliverable that states the scope must also state the driver behind each category call, per rule 6 below.

### A.3 System boundary (working definition)

The system under examination is the Meridian patient-workflow platform (workflow-engine, sms-gateway, voice-ivr, web-portal services and their supporting AWS infrastructure in us-east-1), the subprocessors in the PHI delivery path, and the corporate identity, endpoint, and personnel processes that support it. The readiness report will refine this; treat it as the default boundary for evidence requests.

---

## Part B — Deliverable standards (house style)

These six rules are stated here verbatim as they appear in the Ledgerline practice manual. They apply to the readiness report, the controls matrix, and any interim memo shared with the client.

1. **Attestation language:** SOC 2 is an attestation, not a certification. The words 'certified' and 'certification' must not appear anywhere in a Ledgerline deliverable — including when characterizing third-party claims (paraphrase, e.g. 'the press release overstates Meridian's attestation status'). Never state or imply that Meridian 'is SOC 2 compliant'; readiness deliverables state gaps against criteria, not compliance conclusions.

2. **Evidence citation:** every gap finding in the report must cite at least one evidence document inline using the exact format '(source: <filename>)', e.g. '(source: access_review_export_2026-06-08.csv)'.

3. **Criteria identifiers:** reference Trust Services Criteria with AICPA 2017 (2022 revision) identifiers in uppercase dot notation — CC6.1, A1.2, C1.1 — in both deliverables.

4. **Controlled vocabularies:** control status uses exactly implemented | partial | gap (lowercase); remediation priority uses exactly P1 | P2 | P3. No other status or priority words (e.g. 'compliant', 'non-compliant', 'critical priority') may be used in the matrix columns.

5. **Dates and numbers:** all dates in ISO YYYY-MM-DD; percentages carry the % sign; US spelling throughout.

6. **Scoping discipline:** no criterion category may be declared in scope or out of scope without citing the customer-contract clause or business rationale that drives the call.

A note on rule 1, because it comes up on every healthcare engagement: the client's own materials will use the forbidden vocabulary. That is a finding to report, not language to inherit. Characterize it — do not quote it into a deliverable in a way that makes the deliverable itself assert it.

A note on rule 4: the three-value status vocabulary is deliberately blunt. "implemented" means we saw evidence the control exists and operates; "partial" means designed or intermittently operated with a material shortfall; "gap" means the control is absent or not operated. If you are tempted to write "mostly implemented," the status is partial.

---

## Part C — Evidence hierarchy

Readiness engagements drown in conflicting sources. When sources disagree, Ledgerline weighs them in this order:

1. **Dated system exports** (IAM/access exports, MDM inventories, bucket inventories, LMS trackers, payroll rosters) — raw outputs pulled from the system of record, with a pull date. Highest weight.
2. **Contemporaneous operational records** (tickets, incident logs, message archives, email threads created in the course of the events themselves).
3. **Interviews** — valuable for topology and for against-interest admissions; discount self-serving assertions unless corroborated.
4. **Policies, prior reports, and marketing collateral** — evidence of intended design only. A policy stating a control exists is not evidence that it operates; a prior point-in-time report describes design as of its own date and nothing after.

Corollary one: a claim in a memo, questionnaire, or press release that conflicts with a dated export is resolved in favor of the export, and the conflict itself is usually a finding. Corollary two: prior-consultant work product is category 4, however well formatted. When you resolve a conflict in a deliverable, say which source you trusted and why.

Questions on any of this go to me directly. Do not improvise style.

— K. Liu
