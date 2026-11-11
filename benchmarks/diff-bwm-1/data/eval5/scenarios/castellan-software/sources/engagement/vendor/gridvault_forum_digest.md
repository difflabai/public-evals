# GridVault User Community — Forum Thread Digest

*Compiled during vendor diligence for the Castellan engagement, captured 2026-06-15.*
*Threads copied verbatim from community.gridvault.example (public member forum; registration-gated). Usernames as posted. Selection criteria: threads touching FAC-008, LiDAR module licensing, GridVault Connect, and the consulting-firm user base.*

---

## Thread: "FAC-008 module — anyone actually using it?" (started 2026-04-02)

**linesim_dan** (2026-04-02): We were told at sales stage the facility ratings workflow was ready. We've been live on GridVault since last summer and I can't find it anywhere in our tenant. Am I missing a config switch?

**pwr_ratings_amy** (2026-04-03): You're not missing anything. I opened a ticket about this in March. Support confirmed the FAC-008 module is still a roadmap item — GA now targeted for Q2 2027. Ticket #48812 if anyone wants to reference it.

**txline_hector** (2026-04-03): that's a slip from the Q4 2026 date they gave at the user conference. Second year in a row it's moved right.

**westbrook_ops** (2026-04-05): confirming from our side too — we bought partly for FAC-008 in 2025 and are still doing it in spreadsheets. The ratings audit trail on the base module is fine, but the actual facility ratings workflow (equipment-level rating rollup, submission package assembly) does not exist in production. Do your diligence on this one, folks.

**linesim_dan** (2026-04-05): Thanks all. Frustrating — the base platform is genuinely good, but sales needs to stop selling the roadmap.

---

## Thread: "Veg Analytics — 6 month review" (started 2026-02-18)

**veg_mgmt_kate** (2026-02-18): Six months in on Veg Analytics for a ~2,000 line-mile annual program. Honest review: the LiDAR clearance reports are legitimately good. FAC-003 clearance analysis that used to take my analysts a week per corridor comes out in a day, formatted, with the encroachment tables ready for client QA. Ingest is the weak spot — big LAS uploads need to be chunked or they time out.

**talus_gis** (2026-02-19): +1 on the clearance reports. One warning for anyone budgeting: Veg Analytics is a separate SKU. It is not in the base per-user price, whatever the slide deck implied. Ask for the order form, not the deck.

**veg_mgmt_kate** (2026-02-19): Correct, it's a separate SKU — $360/user on our renewal. Worth it for us, but yes, get it in writing.

---

## Thread: "GridVault Connect API rate limits" (started 2026-05-07)

**apiguy_marcelo** (2026-05-07): Anyone else hitting the 100 requests/minute cap on the Connect API? Our utility client polls ratings endpoints hourly across ~900 facilities and we're getting 429s during their batch windows.

**linesim_dan** (2026-05-08): Yes. Workaround is the bulk export endpoint, but then it's not really "machine-readable delivery," it's a nightly file drop. Support says configurable limits are coming.

**westbrook_ops** (2026-05-09): We got the limit raised to 300/min by asking our AE, for what it's worth. Squeaky wheel.

---

## Thread: "How many consultancies are actually on here?" (started 2026-03-11)

**txline_hector** (2026-03-11): Sales collateral says "40+ transmission engineering firms." I count about a dozen consulting firms on the members map — most of the map pins are utility-side ratings desks and co-op engineering departments. Anyone have a real number?

**talus_gis** (2026-03-12): I went through the map pin by pin last quarter for our own reference-check homework: I get ~12 active consultancies, incl. us, Westbrook, and a couple of one-office shops. The "40+" presumably counts every firm that ever did a pilot, plus the utility internal teams.

**veg_mgmt_kate** (2026-03-12): Matches what I see at the user conference. The consultancy table is one table.

---

## Compiler's note (Foundry, 2026-06-15)

Threads above were selected for relevance to Castellan's requirements; ordering is by topic, not date. Usernames `talus_gis` and `westbrook_ops` appear to correspond to Talus Line Engineering and Westbrook Grid Partners, the two reference customers offered by the GridVault account executive in his 2026-06-11 follow-up email. No thread content has been edited.
