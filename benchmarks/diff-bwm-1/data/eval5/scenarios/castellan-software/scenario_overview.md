# The Scenario

I'm an **Engagement Lead at Foundry Systems Advisory**, a consultancy that helps professional-services firms decide what software to build custom, buy, configure, or leave alone. Castellan Grid Consulting's COO, **Dana Whitfield**, has hired Foundry to assess her firm's custom software needs after a bruising six months: two blown project deadlines and a compliance near-miss that came uncomfortably close to a client filing.

The present date is **June 2026**.

---

## The Client — Castellan Grid Consulting

Castellan is an 85-person power transmission line engineering consultancy headquartered in Denver, CO, with satellite offices in Salt Lake City, UT and Kansas City, MO. Its service lines:

- **Transmission line rating studies** — static, ambient-adjusted (AAR), and dynamic line ratings (DLR)
- **LiDAR-based vegetation management analysis** — NERC FAC-003 clearance work
- **Facility ratings compliance programs** — NERC FAC-008
- **PLS-CADD line design and rebuild modeling**
- **Storm-hardening / resilience assessments**
- **Inspection program support**

Castellan is very good at the engineering. The tooling underneath it is another story: a homegrown Access ratings database with one maintainer, a multi-terabyte project file share with no version control, LiDAR point clouds arriving on mailed hard drives into a consumer cloud account, three different field-inspection apps plus paper forms, and a professional-services-automation (PSA) system whose utilization dashboard nobody trusts.

---

## Why Now

Three events forced the question:

- **Red Mesa 115kV rebuild** — delivered weeks late after a superseded design file survived on the share as "FINAL_v2" and modeling proceeded on stale data, compounded by PLS-CADD license contention. The client took a fee concession.
- **Sagebrush DLR study** — delivered weeks late when the firm's homegrown ratings database bottlenecked on its single operator, who was out on leave with nobody else able to run the batch recalculations. The client took a service credit.
- **A FAC-008 near-miss** — a stale conductor rating workbook nearly went into a compliance submission for the firm's largest client; it was caught in QA days before the filing date and traced to spreadsheet versioning on the project share.

---

## The Industry Context

Transmission engineering consultancies live on utility compliance and reliability spend: NERC FAC-003 vegetation clearance obligations, FAC-008 facility ratings programs, and the industry-wide shift toward dynamic line ratings. The work is data-heavy — LiDAR point clouds by the terabyte, conductor rating calculations per IEEE 738, PLS-CADD models — and clients are raising the bar on delivery. Utilities increasingly demand **digital delivery**: secure portals and documented APIs, not PDF reports. Castellan's largest client, Palisade Power & Light, has written digital-delivery requirements directly into its services-renewal RFP.

---

## The Central Tensions

- **Key-person risk on a clock.** The firm's conductor-ratings engine, RATER, is a 2011 Access database written and maintained by one principal engineer — who retires in December 2027.
- **Build appetite vs a 2-person IT team.** Senior engineers are confident they could build tooling in-house; the entire IT department is two people who keep laptops alive.
- **A vendor at the door.** GridVault Software is pitching an all-in-one suite for transmission engineering firms. The pitch deck is polished and addresses real pains; the pitch deck is also marketing. Written follow-ups and the user community tell a fuller story.
- **Data at a scale the tooling ignores.** TB-scale annual LiDAR intake into a quota-capped consumer cloud account, an unversioned multi-terabyte PLS-CADD file share, and rating workbooks whose versioning already produced a compliance near-miss.
- **Revenue conditioned on shipping software.** Palisade's renewal requires a client portal and API delivery of ratings results within twelve months of contract award — and Palisade is the firm's largest client.
- **Numbers that don't agree.** Seat counts, subscription costs, utilization rates, data volumes, and system histories are stated differently by different people and documents. Part of my job is to reconcile them before building anything on top of them.

---

## My Mission

Produce a current-state assessment and a **build / buy / configure / retain capability plan**: the capabilities Castellan needs, how each should be sourced, the integration architecture between them, a phased roadmap with cost bands, and the data-migration and adoption risks. The COO has been explicit that fleet and drone acquisition decisions are out of scope — this engagement is about software capability.

All Foundry deliverables must comply with the firm's **Engagement Deliverable Standards** (in the engagement kickoff materials), which govern currency formatting, source attribution, vendor gate language, and required closing sections.

---

## The People

| Name | Role | Perspective |
|------|------|-------------|
| Dana Whitfield | COO, Castellan Grid Consulting — engagement sponsor | Reliable on strategy, revenue, and client stakes. Her technical sizing figures are gut estimates, not measurements — verify against data. |
| Marcus Brill | Principal Engineer, Line Ratings — author and sole maintainer of the RATER ratings database; retiring December 2027 | The authoritative expert voice on rating methodology, IEEE 738 calculations, and what RATER does. Strongly build-biased; check his recollections about the system's data against IT records. |
| Priya Raghavan | IT Manager (leads the 2-person IT team) | Accurate on inventory, licenses, and infrastructure facts. Strongly buy-biased — champions GridVault — and skeptical that tooling, rather than carelessness, caused the near-miss. |
| Kelly Nguyen | Systems Administrator (the second half of the IT team) | Quietly authoritative on storage, backups, and the 2019 file-server migration. Her Slack posts reward close reading. |
| Tom Okafor | Principal Engineer, PLS-CADD Line Design practice lead | Authoritative on PLS-CADD workflow, the file-share chaos, and license contention. Build-biased — trust his facts more than his "we could write it ourselves" proposals. |
| Jess Calloway | Junior LiDAR Analyst (2 years experience) | Energetic and likable; sometimes confidently wrong. Fact-check her figures and proposals against authoritative sources. |
| Elena Vasquez | Senior Project Manager — ran the Red Mesa rebuild | Precise, receipts-keeping narrator of the two blown deadlines, and the person who knows exactly why nobody trusts the PSA dashboard. |
| Sam Trettel | Compliance Lead (NERC FAC-003 / FAC-008 programs) | Authoritative on the compliance near-miss root cause, audit-evidence burden, and what FAC-008 workflow support actually requires. |
| Ray Donnelly | VP Engineering — Vegetation Management practice lead | Reliable on flight volumes and the mailed-drive workflow. Keeps pitching an in-house drone acquisition program that is outside this engagement's scope. |
| Grant Mosier | Account Executive, GridVault Software Inc. (vendor) | Salesperson. Weigh written quotes over slide copy, and independent user reports over both. |

---

## What You Have Access To

You will be provided with:

- **Shared client materials** (in `sources/shared/`) — corporate documents, data extracts and inventories, Slack channel exports, and reference notes
- **Engagement materials** (in `sources/engagement/`) — kickoff memos and standards, interview transcripts Foundry conducted, project post-mortems, operational data extracts, vendor materials, and client documents

Sources conflict with each other in places. Reconciling them is part of the work.

---

*All organizations, people, products, figures, documents and events in this scenario are fictional. Real vendors, standards bodies and public programs appear only in neutral roles, and nothing here is legal, regulatory, medical or financial guidance.*
