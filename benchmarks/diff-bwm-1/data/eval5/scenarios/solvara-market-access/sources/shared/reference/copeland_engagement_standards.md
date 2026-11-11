# Copeland Access Partners — Engagement & Deliverable Standards (Veloxa / Solvara)

**Author:** Elaine Voss, Partner, Copeland Access Partners
**Date:** 2026-06-01
**Applies to:** Solvara (velankibart) US market-access & prior-authorization engagement for Veloxa Therapeutics, Inc.
**Status:** House standard — governs all work product on this engagement.

---

## Preamble

This document is the governing style and evidence standard for the Solvara market-access engagement. Every consultant on the team — principal, senior consultant, and analyst — is expected to hold the two client deliverables to the conventions set out here before they leave the building. These are not suggestions. Copeland's reputation on specialty-launch work rests on deliverables that a client's legal, pricing, and compliance functions can adopt without rework, and on figures that survive scrutiny because they are sourced. When a junior draft and a system extract disagree, the standard below tells you which one wins.

## Part A — Engagement scope

Veloxa has engaged Copeland to design two things for the Solvara launch, ahead of the expected Q4 2026 PDUFA action date:

1. **The US market-access strategy** — payer landscape and segmentation, benefit-type routing, opportunity sizing (diagnosed and addressable population), distribution model, gross-to-net economics, coverage-policy navigation (step therapy, appeals), and patient-support design.
2. **The end-to-end prior-authorization (PA) workflow** — a step-by-step operational specification running from patient intake and benefits verification through PA preparation and submission, determination, denial and multi-level appeals, specialty-pharmacy fulfillment, bridge and patient-assistance routing, nursing/injection training, and adherence follow-up.

These map to exactly two deliverables:

- **`market_access_plan.md`** — the prose strategy document.
- **`pa_workflow.yaml`** — the machine-readable workflow specification.

A supporting `claims.json` accompanies the deliverables to carry the headline quantitative figures. Nothing outside these artifacts is a deliverable; working notes, the internal launch deck, and draft memos are inputs, not outputs.

## Part B — Deliverable standards

The following six rules are binding on both deliverables. They are quoted here in full so there is no ambiguity.

1. "Currency and units: all monetary figures in USD with a leading $ and thousands separators (e.g. $150,000); express approval rates, payer shares, and gross-to-net as percentages with a % sign. State per-patient figures on an annual basis ('per patient per year')."

2. "US spelling and ISO dates throughout (YYYY-MM-DD); spell out the benefit type (pharmacy benefit vs medical benefit) explicitly whenever a coverage pathway is named."

3. "Source attribution: every quantitative claim in the strategy document must cite its source document inline in the exact format '(source: <filename>)', e.g. '(source: gpp_epidemiology_report_2026.md)'."

4. "No recommendation-of-record language before the stated gate: the strategy must not present distribution, pricing, or copay design as final/approved; frame them as 'recommended, pending Veloxa legal and pricing-committee sign-off' until that gate. Use 'recommended', not 'approved' or 'final'."

5. "Controlled vocabulary: benefit coverage pathway is one of exactly 'pharmacy benefit' or 'medical benefit'; PA outcomes are one of exactly 'approved', 'denied', or 'pended'; do not use ad-hoc synonyms in the workflow spec."

6. "Compliance line: any patient-affordability mechanism must state its eligible population; copay assistance must be described as commercially-insured only, and no deliverable may describe offering copay assistance to a government-insured patient."

### Notes on applying the rules

- On **rule 1**: a payer share is a percentage (e.g. 55%), not a decimal, in prose. In `claims.json` the same figures are expressed as fractions between 0 and 1 by schema; that is the one place decimals are expected. Do not write "$150K" or "150,000 dollars" — write $150,000.
- On **rule 2**: never let "pharmacy benefit" and "medical benefit" blur. Solvara's route of administration determines its benefit type, and the reader must be able to see which pathway you mean on every line where coverage is discussed. Dates such as the PDUFA quarter may be written as "Q4 2026," but any specific date is ISO.
- On **rule 3**: the citation is the filename only, in parentheses, immediately after the claim. If a figure is triangulated from more than one source, cite each source it depends on. An uncited number in the strategy is a defect.
- On **rule 4**: distribution, pricing, and copay design are all subject to a downstream gate. Until Veloxa legal and the pricing committee have signed off, these are "recommended," never "approved" or "final." This protects the client and us.
- On **rule 5**: the workflow spec is read by machines and operators. Use the controlled terms exactly. A PA is "approved," "denied," or "pended" — not "rejected," "held," or "in review." The benefit design is "pharmacy benefit" or "medical benefit" — nothing else.
- On **rule 6**: this is the compliance line and it is not negotiable. State the eligible population for every affordability mechanism. Manufacturer copay assistance is for commercially-insured patients only. No deliverable — prose or spec — may describe extending copay assistance to a government-insured patient.

## Part C — Evidence hierarchy

When sources conflict, resolve the conflict by rank, not by which document is most polished or most confident. The hierarchy on this engagement, highest authority first:

1. **Executed policy, system data, and claims** — payer coverage policies, claims extracts, network-selection records, statute tables, and the authoritative product and pricing documents. These reflect what is actually true or actually decided.
2. **Expert interviews** — advisory-board and payer-medical-director input, field-reimbursement intelligence, and vendor design conversations. Authoritative on judgment and interpretation; a verbal quote from a plan rep is field intelligence, not a governing SLA.
3. **Decks and marketing** — internal launch strategy decks, planning slides, and promotional framing. These are planning artifacts. They aggregate optimistic assumptions and are the lowest tier of evidence. A number that appears only on a slide is unsupported until a higher-tier source confirms it.

A confidently-written junior memo does not outrank a claims extract or a statute. Corroborate before you adopt. Where a planning deck's figure fails against system data, the strategy must state the corrected figure, cite the higher-tier source, and note that it overrides the deck.

## Part D — Review and sign-off

Before either deliverable goes to the client, the engagement principal reviews it against Parts A through C above. The specific checks are: every quantitative claim in the strategy carries an inline `(source: <filename>)` citation; the controlled vocabularies are used exactly in the workflow spec; the benefit type is named explicitly wherever a coverage pathway appears; distribution, pricing, and copay design are framed as recommended and gated, not final; and every affordability mechanism states its eligible population with copay assistance restricted to commercially-insured patients. A deliverable that fails any of these checks is returned for correction. Where a source conflict was resolved, the reviewer confirms the higher-tier source won and that the override is stated on the page. These standards exist so that Veloxa's legal, pricing, and compliance functions can adopt Copeland's work without reopening the analysis; hold the line on them.

*— E. Voss, Copeland Access Partners*
