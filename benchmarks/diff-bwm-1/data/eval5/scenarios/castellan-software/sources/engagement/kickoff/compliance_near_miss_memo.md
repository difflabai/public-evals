# MEMORANDUM — Compliance Incident Review

**Castellan Grid Consulting — Compliance Programs**

**To:** Dana Whitfield, COO
**Cc:** Marcus Brill; Priya Raghavan; Hal Brandt; Palisade account team
**From:** Sam Trettel, Compliance Lead (FAC-003 / FAC-008 Programs)
**Date:** 2026-04-10
**Re:** Incident review — stale conductor rating workbook in Palisade FAC-008 submission package (incident of 2026-03-27)
**Classification:** Near-miss. No erroneous data was filed. This memo is the formal root-cause record.

---

## 1. Summary

During final assembly of the Palisade Power & Light FAC-008 facility ratings submission (filing date 2026-03-30), the draft package incorporated an ampacity value for ACSR 795 kcmil 26/7 "Drake" conductor drawn from a workbook that did not reflect the correction issued on 2026-03-09. The discrepancy was identified during second-reviewer QA on 2026-03-27 — 3 days before the filing date — and the package was corrected before submission. No incorrect rating left the building.

Had the stale value been filed, Castellan would have delivered a facility rating to a client's NERC compliance program that was inconsistent with the client's Facility Ratings methodology. Under FAC-008-3, the Transmission Owner is obligated (R6) to maintain Facility Ratings consistent with its documented methodology; our role as the client's engineering support makes the accuracy of what we hand them a direct input to their compliance posture. I will not speculate in this memo about enforcement consequences; the professional consequences — to the Palisade relationship and to our standing as a compliance services provider — are sufficient to justify the findings below.

## 2. Timeline

| Date | Event |
|---|---|
| 2026-03-09 | Correction to the Drake rating workbook issued following identification of an input-assumption error in the emergency rating case. Corrected copy saved to the project share by the originating engineer |
| 2026-03-09 to 2026-03-24 | Submission package assembly proceeds; the package index sheet references a Drake workbook copy that predates the correction |
| 2026-03-27 | Second-reviewer QA compares packaged values against the ratings practice's current outputs and flags the Drake discrepancy |
| 2026-03-28 | Package corrected; all other conductor values re-verified against current sources |
| 2026-03-30 | Corrected submission filed on schedule |

## 3. Findings

The investigation reviewed the project share's file history, the package index sheet, and the working practices of the three staff involved, and interviewed each of them.

Three versions of the Drake rating workbook existed on the project share at the time of QA; the reviewer was directed by the index sheet to a copy that had not received the March correction. No individual deviated from written procedure.

That paragraph is the finding, and I want its weight understood. The engineer who issued the March correction saved it exactly where our procedure says to save it. The analyst who assembled the package pulled the workbook the index sheet pointed to, exactly as our procedure says to do. The index sheet itself was stale — it had been created at package kickoff and no procedure required it to be updated when a correction was issued. Every person did their job as written, and the process still carried a superseded rating to within three days of a compliance filing.

The failure is systemic: we manage safety- and compliance-relevant rating data as loose spreadsheet copies on an uncontrolled file share, with currency established by filename convention and a manually maintained index. There is no single authoritative copy, no mechanism that marks a superseded version as superseded, and no structural reason this cannot recur on any submission we assemble.

I note for completeness that a characterization of this incident as individual carelessness has circulated informally. The investigation does not support that characterization, and this memo supersedes it.

## 4. What worked

Second-reviewer QA caught the discrepancy. That control functioned exactly as designed, and the reviewer deserves specific credit for checking packaged values against current practice outputs rather than against the index. It must also be said plainly: a control that catches a systemic failure three days before a filing deadline is a last line of defense, not a management system.

## 5. Corrective actions

The following interim measures take effect immediately for all FAC-008 submission work:

1. **Single-index rule.** One index sheet per submission package, updated same-day whenever any referenced workbook changes; the updater initials and dates the index entry.
2. **Naming-convention memo.** Superseded rating workbooks are renamed with a `SUPERSEDED_` prefix at the time of correction; the correcting engineer owns the rename.
3. **Pre-filing currency check.** QA sign-off now includes an explicit line item verifying every packaged rating against the ratings practice's current output, not against the package index.

To be explicit about their limits: these controls are compensating, not corrective; the underlying versioning exposure remains. They reduce the probability of recurrence by adding human diligence to a process that failed despite human diligence. A durable fix requires versioned, access-controlled ratings data with one authoritative source per conductor and facility — a systems question, not a procedures question, and one I understand falls within the scope of the Foundry engagement now underway. I ask that this memo be provided to the Foundry team in full.

## 6. Status

Interim controls 1–3 in effect as of 2026-04-06. Incident closed as a near-miss; root cause classified systemic (uncontrolled versioning of ratings data). No individual corrective action is warranted or taken.

Sam Trettel
Compliance Lead, Castellan Grid Consulting
strettel@castellangrid.example
