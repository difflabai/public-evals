# Sagebrush DLR Study — Project Review

**Project:** Sagebrush Corridor Dynamic Line Rating Study (PRJ-2183)
**Client:** Juniper Plains Transmission
**Review date:** 2026-04-03
**Facilitator:** Elena Vasquez, Senior Project Manager
**Attendees:** Marcus Brill (technical lead), Sam Trettel (QA), two ratings engineers, Hal Brandt (PMO Director, observing)
**Distribution:** Project team; D. Whitfield; H. Brandt

---

## Outcome summary

The Sagebrush study — dynamic line rating analysis across 240 conductor segments on three 230kV circuits in the Sagebrush corridor — was contracted for delivery on 2026-02-13. Final deliverables were submitted and accepted on 2026-03-27, **6 weeks late**. Castellan issued a **$60,000 service credit** against the engagement fee, agreed with High Plains on 2026-03-31.

As with Red Mesa, the analysis that ultimately shipped was correct and well received. High Plains' planning group called the study "exactly what we asked for, a month and a half after we asked for it." The delay was not an engineering failure; it was an operational one, and it has a single dominant cause.

## What happened

The study's computational core runs through RATER, the ratings practice's internal Access database. RATER performs the IEEE 738 steady-state and transient calculations, holds the conductor and lookup tables, and — critically for this project — is the only tool in the building that can run batch DLR recalculations at scale. The Sagebrush scope required a full recalculation pass across all 240 conductor segments once the final weather-station dataset arrived from the meteorological vendor in late January.

Marcus Brill went out on medical leave on 2026-02-02. The leave was unplanned and ultimately lasted 3 weeks. We wish to state plainly, because it was said with some feeling in the review session: nobody in this room or outside it holds the leave against Marcus, and this review is about the fact that his absence stopped the project, not about the absence itself.

While Marcus was out, the queue of 240 conductor-segment recalculations sat idle. Two ratings engineers attempted to keep the project moving:

1. They attempted to run the batch module themselves. RATER's batch DLR routine depends on setup steps and intermediate checks that exist only as Marcus's working knowledge — there is no runbook. The attempts were abandoned after two days when the outputs failed sanity checks against prior runs.
2. They then rebuilt a subset of the calculation in Excel to at least advance the highest-priority segments. This workaround introduced **two errors** — a wind-incidence-angle unit inconsistency and a stale emissivity coefficient carried in from an older template. Both errors were caught late, in Sam Trettel's QA pass on 2026-03-16, and forced a full re-run of the affected segments through RATER after Marcus's return.

Marcus returned 2026-02-23 and cleared the full 240-segment queue in nine working days — which the panel notes is simultaneously a testament to him and the clearest statement of the problem.

## Secondary cause — manual weather data intake

The meteorological vendor delivers station data as CSV files. RATER's import path for this project required the data to be re-keyed by hand into its import sheet — approximately four analyst-days of transcription, performed in January, with one transposition error caught at import validation. This did not drive the six-week slip, but it lengthened the critical path before the leave began and it is the kind of manual step that turns a three-week absence into a six-week delay.

## Contributing causes, ranked

1. **RATER single-operator bottleneck (primary).** One person can run the batch DLR process; that person was unavailable for three weeks; the project stopped. The Excel workaround made things worse, not better, and consumed QA capacity on top of it.
2. **Manual weather-station data intake (secondary).** Hand re-keying of vendor CSVs extended the pre-leave critical path and added an error mode.
3. **No leave contingency in the project plan (contributing).** The plan assumed continuous availability of a single named individual for the computational phase. That assumption was invisible because it has always held before.

## What went well

- Sam Trettel's QA pass caught both workaround errors before anything reached the client. The second-reviewer discipline in the ratings practice worked.
- Marcus documented nothing less than usual — but on his return he prioritized the recalc queue over all other work without being asked.
- The client accepted the credit and renewed its 2026 task order, largely on the strength of candid weekly communication during the slip.

## Recommendations

1. The review's headline finding: succession risk on RATER must be addressed before Marcus retires (Dec 2027). This is not optional and it is not deferrable. The Sagebrush slip was a three-week preview of a permanent condition that begins the day Marcus leaves. Whatever form the answer takes — documented runbooks, a second trained operator, or replacement of RATER with something maintainable by more than one person — the firm has a hard deadline and it is not far away.
2. Establish an automated import path for meteorological vendor CSVs; eliminate hand re-keying from the ratings workflow.
3. Prohibit ad-hoc Excel reconstruction of RATER calculations for client deliverables; if the batch process is unavailable, the project schedule moves — it is cheaper than the QA burden of parallel math.
4. Project plans for ratings engagements must identify single-operator dependencies explicitly at kickoff.

## Facilitator's closing

Red Mesa cost us $85,000 and taught us about files. Sagebrush cost us $60,000 and taught us about people — specifically, that a 14-person practice runs its most computationally critical process through one individual and one Access database built in 2011. The review panel was unanimous that recommendation 1 is not an IT nicety but a continuity-of-business item with a fixed clock on it.

— E. Vasquez, 2026-04-03
