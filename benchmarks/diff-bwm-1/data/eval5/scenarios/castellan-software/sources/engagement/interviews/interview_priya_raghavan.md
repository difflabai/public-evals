# Interview Transcript — Priya Raghavan (IT Manager)

**Engagement:** Castellan Grid Consulting — Custom Software Needs Assessment
**Date:** 2026-06-16
**Location:** Castellan Denver HQ, conference room 3B
**Participants:** Alex Kessler (Foundry Systems Advisory, engagement lead); Priya Raghavan (IT Manager)
**Recording:** Consented. Transcribed from audio by Foundry; lightly edited for clarity. Timestamps approximate.

---

[00:00:06] KESSLER: Priya, thanks for fitting me in. I know your calendar is a war zone.

[00:00:11] RAGHAVAN: My calendar is fine. My ticket queue is the war zone. You have forty-five minutes and then I have a conference room AV emergency, which I can tell you about now because there is always one.

[00:00:24] KESSLER: Let's start with the shape of the IT function itself.

[00:00:28] RAGHAVAN: The shape is this: there are two of us; we keep laptops alive, we do not run a software company. That's the single most important sentence I will say today, so I'd like it in your report in bold. Me and Kelly Nguyen. Kelly does servers, storage, backups, the license server, and about half the tickets. I do vendors, budget, security posture, onboarding, and the other half. Eighty-five staff, three offices, two of us. Whatever your assessment recommends, it has to be operable by that team plus vendors, or it is fiction.

[00:01:45] KESSLER: That's heard. Give me the inventory picture — what does the firm run on?

[00:01:51] RAGHAVAN: I maintain a tool inventory, I'll send you the current export — it's dated May. The load-bearing items: PLS-CADD with nine licenses on FlexLM. Traversa PSA for time, billing, and project accounting. Office 365 for everything ambient. The RATER database, which is Marcus's, and which I have opinions about that I will keep professional. Three field inspection apps — SnapInspect, Fulcrum, CollectorPro — because three different clients standardized on three different things and two more clients still mandate paper. A CloudCrate business account where the LiDAR deliveries land. And the CGC-FS01 file share, which is where engineering data goes to multiply.

[00:03:50] KESSLER: Let's do Traversa. What's the contract position?

[00:03:54] RAGHAVAN: Renewal is October 2026, so your timing is convenient. And before you find it yourself in the budget file: we budget for eighty-five seats but only seventy are active. The FY2026 line was built years ago on an all-staff assumption, finance keeps rolling it forward, and the actual invoice is for seventy. I flag it every cycle; it never gets fixed because a too-big budget line hurts nobody's quarter. The invoice is the real number — pull it from AP, not from my budget sheet.

[00:05:30] KESSLER: Is Traversa the right system?

[00:05:33] RAGHAVAN: It's an adequate system that everybody enjoys hating. The complaints I get are really configuration complaints — the utilization dashboard being the loudest example. Elena will perform the full opera for you, I assume she already has it scheduled. My position: you don't rip out a PSA because a report is misconfigured. You fix the mapping, which requires someone to own Traversa administration as a job and not as a hobby, which — see sentence one, there are two of us.

[00:07:20] KESSLER: The compliance near-miss in March. From where you sit, what happened?

[00:07:26] RAGHAVAN: A stale workbook nearly went into a filing and QA caught it. I know Sam's memo calls it systemic, and I've read it, and Sam and I have had this argument at a civilized volume. I still think process, not tools, caught us there — a human second reviewer caught it, which means the human layer worked. I'm not against better tooling, obviously, I'm the one who'd deploy it. I just get allergic when every incident becomes a procurement case. Sometimes the fix is that people follow the checklist.

[00:09:05] KESSLER: Sam's memo says nobody deviated from procedure, though. The index itself pointed QA at the stale copy.

[00:09:12] RAGHAVAN: And that's the strongest counter to my position, yes. Thank you for reading it at me. [laughs] Look, put it this way: if you give me versioned, access-controlled ratings data, I will happily be wrong. My worry is sequencing — we have finite change capacity, and I want it spent where the risk is.

[00:10:40] KESSLER: Which brings us to build versus buy. Where do you land, in general?

[00:10:45] RAGHAVAN: Buy. Almost always buy, for a firm our size. Every custom system we build is a system Kelly and I patch, back up, secure, and get paged about at 2 a.m. forever. RATER is my Exhibit A — brilliant tool, one maintainer, retiring, and the succession plan is an org chart with a hole in it. Tom will tell you his guys can write a data manager in a summer. Sure. And in three summers it's my unsupported legacy system with one maintainer who got promoted. We do not have the metabolism to run bespoke software. We buy, we configure, we hold vendors accountable. That's a two-person-IT strategy.

[00:12:50] KESSLER: You've seen the GridVault proposal. Your read?

[00:12:54] RAGHAVAN: GridVault checks every box on paper. Ratings module, veg-management analytics, inspection forms, a client portal, a document vault — it maps almost line for line onto our pain list, and one vendor relationship instead of six is exactly the consolidation I want. I'll be straight with you though: on the compliance workflow claims, the written confirmation I asked Grant for came back mushier than I'd like — 'early adopter program' is not the same thing as a product — but I still think the platform consolidation story carries the day. Validate the FAC-008 module properly; given March, that validation cannot be a formality. Put reference calls with firms actually running it in production into your plan — firms our size, not their lighthouse account.

[00:15:10] KESSLER: If GridVault's compliance module turned out to be less mature than the deck says?

[00:15:15] RAGHAVAN: Then we'd phase it — take the modules that are real now, hold the compliance piece to a proof. I'm pro-buy, not pro-gullible. But I'd want evidence before we discount them, the same way I want evidence before we anoint them.

[00:16:30] KESSLER: LiDAR storage. What's the current arrangement?

[00:16:34] RAGHAVAN: The acquisition vendors ship us encrypted hard drives — physical drives, couriered, logged on a literal whiteboard in the Denver office. In 2026. Mailing hard drives around the Mountain West is a felony against common sense, and I've said so in nicer words in three budget cycles. The drives get uploaded to the CloudCrate account when someone has time, which is the phrase doing all the work in that sentence.

[00:17:55] KESSLER: How much LiDAR data are we actually talking about — what's in the account, what comes in per year?

[00:18:01] RAGHAVAN: I'm not going to guess numbers at you, because guessed numbers end up in consultants' reports with my name on them. Kelly administers that account and watches the quota; she has the console figures and the intake history. Ask Kelly, and treat whatever she tells you as the number. What I'll say qualitatively: it's large, it grows every flight season, and a consumer-grade cloud account with a quota is not an archive strategy for it, it's a bucket under a leak.

[00:19:40] KESSLER: Last one. If the assessment gets one thing right, what should it be?

[00:19:45] RAGHAVAN: Respect the denominator. Two people. Every recommendation costs operational calories — migrations, permissions, backups, training, vendor management. If the plan assumes an IT function we don't have, either fund that function or shrink the plan. I've watched consultants draw beautiful target architectures for firms triple our size. Draw ours.

[00:20:30] KESSLER: That's fair, and quotable. Thanks, Priya.

[00:20:33] RAGHAVAN: Bold, remember. Sentence one, in bold.

--- END OF TRANSCRIPT ---
