# The Scenario

I'm a **Senior Consultant at Ledgerline Assurance LLC**, engaged to run a **SOC 2 Type II readiness assessment** of **Meridian Health Loop, Inc.** ahead of a planned Q1 2027 audit. My engagement director is Katherine Liu, a former Big 4 SOC 2 practice leader. The readiness deliverables go to Meridian's CEO and CTO and will frame the remediation program for the proposed Type II review period of **2026-10-01 through 2027-03-31**.

It is June 2026.

---

## Engagement Context

Two flagship health-system prospects — **St. Aldric Health** and **Bayview Regional Health Network** — have made a Q1 2027 SOC 2 Type II report a condition of contract expansion. Meridian obtained a SOC 2 Type I from Castlewick Advisory in August 2025, but a Type I is a point-in-time opinion on control design only; the Type II must show controls *operating* across a review period.

The arithmetic is unforgiving: for controls to operate through the proposed six-month review period and land a report in Q1 2027, remediation of the most serious gaps must be complete **before 2026-10-01**. My readiness assessment defines the audit scope, maps the current control posture against the Trust Services Criteria, and sequences the remediation program to hit that date.

---

## The Client — Meridian Health Loop, Inc.

Meridian is a 45-person healthcare communications technology startup. Its platform delivers patient workflows over SMS, web, and voice on behalf of health systems: **ER discharge follow-up**, **clinical study enrollment**, and **appointment check-in** are the flagship workflow templates. The stack runs on AWS; delivery rides on third-party SMS and telephony providers.

Meridian **processes PHI as a business associate** under HIPAA, which means executed Business Associate Agreements up and down its vendor chain, contractual incident-notification obligations to its health-system customers, and confidentiality commitments that shape what the SOC 2 must cover. St. Aldric is a production customer under an executed MSA; Bayview signed a BAA for a pilot and is in late-stage sales.

---

## SOC 2 in This Market

Health systems increasingly gate vendor expansion on a SOC 2 Type II report. A few realities of the terrain matter here:

- **SOC 2 is an attestation, not a certification.** There is no "SOC 2 certified"; there are examination reports against the AICPA Trust Services Criteria. Ledgerline's house standards are strict about this language.
- **Scope is argued, not assumed.** The Security (common) criteria are always in scope; whether Availability, Confidentiality, or Privacy join them must be driven by the entity's actual customer commitments — contract clauses, SLAs, BAAs — not by marketing appetite.
- **Evidence has a hierarchy.** Dated system exports beat contemporaneous tickets and messages, which beat interviews, which beat policies and marketing copy. A readiness assessment lives or dies on grounding control status in the right layer.

---

## The Central Tensions

- **Paper versus reality.** Meridian's written record — an architecture memo, a company one-pager, a prior Type I, a prospect security questionnaire — describes a mature posture. The June data pulls, tickets, Slack exports, and interviews describe the company that actually exists. Where they disagree, I have to decide which source to trust and say why.
- **Compliance versus sales.** The sales organization has been answering prospect security questionnaires under deal pressure. What customers have been told and what the evidence supports are not the same thing, and the gap is itself a finding.
- **A stale prior report.** The 2025 Type I described control design as of mid-2025. Importing its assertions wholesale into 2026 is the easiest mistake available in this engagement.
- **A part-time compliance function.** The compliance lead wears the hat alongside running Customer Success ops. Facts reach her; follow-through doesn't always happen.
- **A hard deadline.** Every P1 gap must be remediated before the review period opens on 2026-10-01, or the Q1 2027 report — and the contract expansions gated on it — slips.

---

## Cast

| Name | Role | Perspective |
|------|------|-------------|
| Dana Okafor | CEO & co-founder, Meridian Health Loop | Commercially candid engagement sponsor; the readiness push exists because St. Aldric and Bayview demanded it. Defers on technical detail and sometimes repeats the marketing line on compliance posture. |
| Priya Raman | CTO & co-founder, Meridian Health Loop | Deep on architecture, and her topology can be trusted — but her written memos read more confidently than the evidence supports. Concedes specifics under direct questioning; verify her control assertions against the exports. |
| Tom Brzezinski | VP Engineering, Meridian Health Loop | The most candid internal voice on infrastructure reality. His interview and the engineering runbook reflect the current state, warts included. |
| Jenny Park | DevOps Engineer II, Meridian Health Loop | Junior and confident; her Slack takes don't always survive contact with the exports. Corroborate before adopting anything she asserts. |
| Marcus Webb | VP Sales, Meridian Health Loop | Owns the Bayview security questionnaire answers and is motivated to present the strongest possible posture. His documents are evidence of what customers were told, not of what controls exist. |
| Alicia Fontaine | Compliance & Privacy Lead (part-time hat; also runs Customer Success ops), Meridian Health Loop | Under-resourced but accurate. Reliable on facts, weak on follow-through. |
| Rosa Delgado | People Ops Manager, Meridian Health Loop | Owns the roster, the training tracker, and the on/offboarding checklist. Her CSV exports are dependable; the checklist's aspirations are another matter. |
| Evan Marsh | Former Senior Platform Engineer, Meridian Health Loop (terminated 2026-02-13) | Departed four months before the engagement; authored policies still in force. He does not speak in any source — what remains of him in the systems is the evidence. |
| Katherine Liu | Engagement Director, Ledgerline Assurance | Former Big 4 SOC 2 practice leader. Author of the Ledgerline engagement standards and the scoping guidance; her standards govern deliverable style, vocabulary, and citations. |
| Gideon Hart | Fractional vCISO (contractor to Meridian since 2026-04) | Commissioned the penetration test and the June data pulls. Accurate and blunt; his cover notes flag which exports are raw system outputs versus self-attested material. |
| Simone Aldana | Engagement Manager, Castlewick Advisory (prior consultant) | Signed the August 2025 SOC 2 Type I. Historically honest — but the report is a point-in-time design opinion, and the world has moved since. |
| Ruth Okonjo | VP Vendor Risk, Bayview Regional Health Network | Customer-side counterpart. Her contract terms — incident notification windows, subprocessor disclosure and BAA flow-down — are binding and authoritative for scoping. |

---

## What You Have Access To

You will be provided with:

- **Shared company materials** (in `sources/shared/`) — Meridian's internal documents as they exist: the company overview, architecture memo, policies and policy register, runbooks, HR checklists, the incident log, tickets, Slack exports, the prior Type I excerpt, the Bayview questionnaire, press coverage, and neutral reference briefs.
- **Engagement-gathered materials** (in `sources/engagement/`) — what Ledgerline collected for this assessment: the kickoff engagement standards and scope memo, the June data pulls (roster, access review, endpoint inventory, training tracker, vendor register, S3 inventory, pen-test summary), interview transcripts, email threads, and customer contract excerpts.

This scenario overview and the task brief are provided at the start of the engagement.

---

*All organizations, people, products, figures, documents and events in this scenario are fictional. Real vendors, standards bodies and public programs appear only in neutral roles, and nothing here is legal, regulatory, medical or financial guidance.*
