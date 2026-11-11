# Interview Transcript — Elena Vasquez (Senior Project Manager, PMO)

**Engagement:** Castellan Grid Consulting — Custom Software Needs Assessment
**Date:** 2026-06-17
**Location:** Castellan Denver HQ, conference room 3B
**Participants:** Alex Kessler (Foundry Systems Advisory, engagement lead); Elena Vasquez (Senior Project Manager, Project Management Office)
**Recording:** Consented. Transcribed from audio by Foundry; lightly edited for clarity. Timestamps approximate.

---

[00:00:05] KESSLER: Elena, thanks for doing this. You ran Red Mesa, so I want to spend real time there.

[00:00:11] VASQUEZ: I ran Red Mesa, I facilitated its post-mortem, and I keep the receipts on both. Ask me anything; if I don't have the document, I'll tell you it's an impression and not a fact. I try to keep those piles separate.

[00:00:28] KESSLER: Then let's start with Red Mesa. What happened, in your own sequencing?

[00:00:33] VASQUEZ: The short version, and it matches the written post-mortem: we delivered nine weeks late and gave the client an $85,000 fee concession to keep the relationship. Three causes, ranked. First, version confusion on the file share — a superseded structure alignment survived in the working folder with FINAL_v2 in the name, and the modeling team built on it for three weeks before the discrepancy surfaced. Second, PLS-CADD license contention — the checkout waits cost us about three weeks of calendar, and that's the post-mortem's number, not my memory. Third, the rework cascade from the first cause. None of those three is a people problem. I want to be precise about that, because the lazy read is "the modeler grabbed the wrong file." The file system offered no way to know it was the wrong file. That's a system design choice we made by never making a choice.

[00:03:10] KESSLER: And Sagebrush?

[00:03:12] VASQUEZ: Different failure, same lesson. Six weeks late, $60,000 service credit. Marcus was out on medical leave for three weeks in February, and it turned out the entire batch recalculation capability of the line ratings practice was inside one person's head. The queue of segment recalcs just sat there. People improvised in Excel, the improvisation introduced errors, QA caught them late, and late QA catches are schedule bombs. I adore Marcus, everyone does, but a firm this size cannot have a single human as its ratings infrastructure. He'd be the first to agree, for what it's worth. He has agreed, in meetings, with witnesses.

[00:05:20] KESSLER: Two blown deadlines in five months. Is that a run of bad luck or a trend?

[00:05:26] VASQUEZ: It's a trend that finally got expensive enough to see. The same fragilities were present on projects that happened to go fine — Red Mesa and Sagebrush are just the ones where the dice came up wrong. That's the framing I gave Dana, and I think it's why you're here.

[00:06:40] KESSLER: Let's talk about the PSA. When I ask about utilization, people wince and mention Traversa.

[00:06:47] VASQUEZ: They wince because the dashboard is wrong and everyone senior knows it's wrong. I dug into this myself last quarter because I was tired of arguing about it in ops reviews.

[00:07:15] KESSLER: Wrong how?

[00:07:17] VASQUEZ: Project-code mapping. When we opened three projects in 2022 — 2201, 2214, 2230, all live, all billing clients today — the codes never got mapped to a billable category in Traversa's configuration. The dashboard counts every hour on them as non-billable. Nobody owns Traversa administration, so the defect has sat there for four years.

[00:23:12] KESSLER: [returning after a scheduling interruption] You were going to give me the corrected utilization number.

[00:23:18] VASQUEZ: The dashboard reads 61, but if you add back the three project codes Traversa never got mapped — 2201, 2214, 2230 — we're at 68. Nobody trusts that report and nobody should. If you want to verify it, don't take my math — pull the raw timesheet extract and recompute it with those three codes counted billable. It's an afternoon with a spreadsheet. I'll send you the extract myself.

[00:24:30] KESSLER: That's a meaningful gap. Does it change decisions?

[00:24:34] VASQUEZ: It nearly changed a big one. There was a stretch last fall when leadership was reading 61 as a demand problem and talking about slowing hiring. The real number said our people are appropriately loaded and the constraint is tooling, not demand. A wrong dashboard almost drove a wrong staffing call. That's what a broken system of record costs — not the number itself, the decisions leaning on it.

[00:25:50] KESSLER: What does Traversa cost the firm, do you know?

[00:25:53] VASQUEZ: Off the top of my head, we pay about five grand a month for Traversa — Priya or the invoice will give you the exact contract figure. Which, for the record, I think is fine value if the thing were configured properly. My complaint has never been the price. It's that we bought a system of record and then didn't record.

[00:27:00] KESSLER: Let's do field inspections. The three-apps situation.

[00:27:04] VASQUEZ: Three apps plus paper, because clients mandate their own platforms — SnapInspect for one, Fulcrum for another, CollectorPro for a third, and two utilities that still require paper forms. Our four field analysts capture the same kinds of data four different ways, and then re-key it into our deliverable formats and into Traversa. I did the walkthrough with them in April for exactly this engagement: each of the four field analysts loses maybe 30 hours a month just re-keying. There's even a timesheet code for it now — ADM-REKEY — which I insisted on, because pain you don't measure is pain you keep. Check the April extract; the code shows up just about where my estimate says it should.

[00:29:20] KESSLER: What's the fix look like from the PM chair?

[00:29:24] VASQUEZ: One intake path. I don't care whether we build a normalization layer, buy a platform that ingests all the client formats, or configure something in the middle — genuinely indifferent, and suspicious of anyone who isn't. What I care about is that a tree or an insulator gets typed once, by one person, and flows to the deliverable and the timesheet from there. Same principle as the file share: single source of truth, versions controlled, provenance visible. Red Mesa, the Drake near-miss, and the rekey tax are the same disease in three bodies.

[00:31:10] KESSLER: You're the first person who hasn't pushed me hard toward build or buy.

[00:31:15] VASQUEZ: Because that's a means question and I get paid for ends. Tom wants to build everything; he also wants to personally rewrite the license server, probably. Priya wants to buy everything; she's also right that two people can't run a software shop. The honest answer is a mix, decided per capability on evidence, with somebody actually owning each system afterward. Ownership is the part everyone skips. Every mess I've walked you through today — the share, the dashboard, the budget line that doesn't match the invoice — is an unowned system quietly rotting.

[00:32:40] KESSLER: Last question. What should I not miss?

[00:32:43] VASQUEZ: The Palisade clock. The RFP language conditions the renewal on portal and API delivery on a twelve-month fuse from award, and that's our largest client. Whatever sequencing you propose, that date is not a stretch goal, it's a survival requirement. Sequence backward from it.

[00:33:05] KESSLER: Heard. Thank you, Elena — this was the most citable hour I've had all week.

[00:33:09] VASQUEZ: I'll email you the extract and the two post-mortems before you're back at your desk.

--- END OF TRANSCRIPT ---
