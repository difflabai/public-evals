# Red Mesa 115kV Rebuild — Project Review

**Project:** Red Mesa–Cottonwood 115kV Rebuild (PRJ-2168)
**Client:** Ute Basin Electric Cooperative
**Review date:** 2026-02-12
**Facilitator:** Elena Vasquez, Senior Project Manager
**Attendees:** Tom Okafor (modeling lead), Dev Sharma (PM support), four staff engineers from the design practice, Hal Brandt (PMO Director, observing)
**Distribution:** Project team; D. Whitfield; H. Brandt

---

## Purpose and ground rules

This review follows the standard PMO blameless format: we reconstruct what happened from project records, rank contributing causes by schedule impact, and record action items with owners. Quotes below are paraphrased from the session unless marked. This document is the record of the review; the working notes are archived under the project folder.

## Outcome summary

The Red Mesa rebuild — PLS-CADD modeling, structure spotting, and rebuild design for 41 miles of the Red Mesa–Cottonwood 115kV line — was contracted for final deliverable submission on 2025-11-14. Final deliverables were accepted by Ute Basin on 2026-01-16, **9 weeks late**. To preserve the relationship and the co-op's 2026 program work, Castellan agreed to an **$85,000 fee concession**, executed by change order on 2026-01-23. The client has signaled it will continue to work with us; the account team characterizes the relationship as "bruised, recoverable."

The engineering itself was sound. The design that shipped in January passed client review with minor comments only. The nine weeks were lost to how we manage files and licenses, not to how we do engineering.

## Timeline

| Date | Event |
|---|---|
| 2025-08-04 | Project kickoff; survey and classified LiDAR received from client's acquisition vendor |
| 2025-08-22 | Preliminary structure alignment issued to project share |
| 2025-09-12 | Client directs reroute around a cultural-resource parcel (structures 214–241); corrected alignment produced and saved to a working subfolder |
| 2025-09-15 | Modeling resumes. The team pulls `RM_Alignment_FINAL_v2.xlsx` from the project folder root, believing it current |
| 2025-10-06 | Design review detects that three weeks of PLS-CADD modeling was performed against the superseded alignment; work on structures 214–241 and adjacent spans is invalidated |
| 2025-10-07 to 2025-11-21 | Rework of affected spans; concurrent license contention across the practice (see Cause 2) |
| 2025-11-14 | Original contractual delivery date passes; client notified of slip on 2025-11-10 |
| 2026-01-16 | Final deliverables submitted and accepted |
| 2026-01-23 | $85,000 fee concession executed by change order |

## Contributing causes, ranked by schedule impact

### Cause 1 — File-share version confusion (primary)

When the client's reroute came in on 2025-09-12, the corrected alignment was saved into a working subfolder with a revised filename. The superseded alignment remained at the project folder root as `RM_Alignment_FINAL_v2.xlsx` — a name that, to anyone opening the folder, announced itself as the current file. It was modeled against for three weeks before the 2025-10-06 design review caught the discrepancy.

Nobody misfiled anything by the standards we actually have, because we do not have standards for this. The project share has no check-in/check-out, no way to mark a file superseded, and no single index of record. "FINAL_v2" is a convention, and conventions do not survive a reroute issued on a Friday afternoon. The review panel was unanimous that any team member in the same folder on the same Monday would have made the same choice.

Direct schedule impact: three weeks of invalidated modeling, plus the rework tail counted under Cause 3.

### Cause 2 — PLS-CADD license contention (secondary)

The rework window (October–November) collided with peak demand for PLS-CADD across the practice, as two other projects were also in heavy modeling phases. Engineers reported waiting for a license to free up before they could start or resume work, and evening/weekend modeling sessions were scheduled specifically around license availability. Reconstructing from the daily standup notes and the team's own logs, **license checkout waits cost us approximately 3 weeks of calendar schedule** on the recovery effort.

The panel noted this is not a Red Mesa problem; it is a firm capacity problem that Red Mesa happened to hit at the worst time. Sizing the right number of seats is outside the scope of this review and should be done from license-manager data rather than from this room's frustration.

### Cause 3 — Rework cascade from Cause 1

The invalidated spans could not simply be re-run: structure spotting decisions downstream of the stale alignment had propagated into material lists and the draft plan-and-profile sheets. Unwinding those dependencies consumed more effort than the original modeling. The panel estimates roughly 60% of the total slip traces to Cause 1 and its cascade, with Cause 2 accounting for the remainder.

## What went well

- The 2025-10-06 design review worked as designed — it caught the problem, late but decisively.
- Client communication after 2025-11-10 was proactive and candid; Ute Basin's project engineer specifically credited the weekly recovery reports.
- No quality escapes in the final deliverable.

## Action items

| # | Action | Owner | Due | Status at issuance |
|---|---|---|---|---|
| 1 | Issue project-share naming convention memo for design practice | T. Okafor | 2026-02-28 | Open |
| 2 | Investigate PDM (product/project data management) options with check-in/check-out for PLS-CADD projects | TBD | TBD | Open — no owner identified |
| 3 | Provide license checkout wait data to PMO monthly | K. Nguyen (IT) | 2026-03-15 | Open |
| 4 | Single "current alignment" index sheet required per project folder | E. Vasquez | 2026-02-20 | Open |

Facilitator's note on item 2: this is the third post-project review since 2024 to raise data management tooling, and the third time the action item has left the room without an owner. I am recording that fact here deliberately.

## Facilitator's closing

We lost nine weeks and $85,000 on a project our engineers executed well. The causes were structural: an uncontrolled file share and a license pool sized by history rather than by demand. Until one or both change, this review's most likely legacy is being quoted in the next one.

— E. Vasquez, 2026-02-12
