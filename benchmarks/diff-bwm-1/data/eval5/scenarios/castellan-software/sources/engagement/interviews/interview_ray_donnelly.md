# Interview Transcript — Ray Donnelly (VP Engineering; Practice Lead, Vegetation Management & LiDAR Analytics)

**Engagement:** Castellan Grid Consulting — Custom Software Needs Assessment
**Date:** 2026-06-17
**Location:** Castellan Denver HQ, conference room 3B
**Participants:** Alex Kessler (Foundry Systems Advisory, engagement lead); Ray Donnelly (VP Engineering; Practice Lead, Vegetation Management & LiDAR Analytics)
**Recording:** Consented. Transcribed from audio by Foundry; lightly edited for clarity. Timestamps approximate.

---

[00:00:06] KESSLER: Ray, appreciate the hour. Give me the shape of the vegetation management practice.

[00:00:11] DONNELLY: Biggest practice in the firm by headcount — twenty of us — and the fastest growing by revenue. The product is FAC-003 vegetation compliance, delivered off airborne LiDAR. Utilities have to keep vegetation out of their transmission corridors, NERC audits them on it, and the defensible way to do it at scale is fly the corridor, get a classified point cloud, and run clearance analysis against the conductor positions. We tell them which trees to cut, with coordinates and evidence. It's a great business. It's also drowning in its own data, which I assume is why you're in my calendar.

[00:01:35] KESSLER: It is. Start with the flight program — what's the scale this year?

[00:01:40] DONNELLY: Growing every season. Three acquisition vendors under contract, fixed-wing and helicopter, flying corridors across five states.

[00:08:14] KESSLER: [after corridor-map walkthrough] So bringing that back to volume — how many miles this season?

[00:08:19] DONNELLY: All programs together, we'll fly about 1,900 line-miles this season. That's the 2026 program as sold — could tick up if High Plains exercises their fall option, but 1,900 is what I'd plan on. Five years ago that number was six hundred. Nobody resized anything downstream as it grew, which is the story of this practice in one sentence.

[00:09:30] KESSLER: Walk me through what happens to the data after the aircraft lands.

[00:09:35] DONNELLY: [laughs] You're going to think I'm describing 2006. The vendor processes and classifies the point cloud, then ships it to us on encrypted USB hard drives, by courier. The drives arrive at the Denver office. There's a whiteboard by Anna's desk — an actual whiteboard — where we log what came in, which project it belongs to, and whether it's been uploaded to the cloud account yet. That whiteboard is, functionally, our data-management system of record for incoming deliveries. Then someone uploads the drive contents to CloudCrate when they can. The spec says ten business days; ask the team how that's going. Last I checked with Kelly the upload backlog was running three, four weeks behind. So at any given moment there's a stack of drives in a locked cabinet holding deliverables clients have already paid for, findable only by whiteboard archaeology.

[00:12:10] KESSLER: What breaks first in that chain?

[00:12:13] DONNELLY: Findability, then time. An analyst starting clearance analysis has to figure out whether the tile they need is on CloudCrate, on a drive in the cabinet, or still at the vendor. I've watched a senior analyst spend most of a day locating data we already owned. And the storage account itself is a whole separate conversation — quota pressure, what we keep, what got cleaned out. Kelly manages that account and she has the actual figures; I make a point of not quoting storage numbers from memory because I'll be wrong and then it ends up in a report. What I'll tell you is the trend: the flight program grows every year and the plumbing hasn't grown since we were a third this size.

[00:14:20] KESSLER: And the analysis side — the deliverable flow?

[00:14:24] DONNELLY: Point cloud in, clearance analysis against conductor geometry, then the FAC-003 deliverable package: encroachment findings, span-by-span clearances, work orders, and the evidence trail the client's compliance team hands to auditors. That last part matters — this isn't pretty pictures, it's audit evidence, so provenance and versioning on those deliverables is a compliance property, not a nicety. Sam will preach that sermon better than me.

[00:15:40] KESSLER: Where does the field inspection data fit in?

[00:15:44] DONNELLY: That's the other leak. The LiDAR tells you where the encroachments are; the field teams then verify and document on the ground. Different clients mandate different apps — plus the paper holdouts — and the data comes back in whatever shape the app produces. So my folks type the same tree twice, sometimes three times. Once in the client's mandated app, once into our deliverable package, and half the time once more into the tracking sheet. Elena's got the hours quantified — she did a walkthrough with the field analysts in April. Use her numbers; mine would be anecdote.

[00:17:30] KESSLER: If I fix one thing in your practice with software, what is it?

[00:17:34] DONNELLY: One system that knows where every point cloud and every deliverable is, from vendor handoff to archive, with the whiteboard retired. Everything else — the analytics, even the rekey problem — gets easier once the data has an address. But look, while I have you, the real fix is upstream of all of it, and it's in a memo I sent Dana in April.

[00:18:05] KESSLER: The UAS proposal.

[00:18:07] DONNELLY: The drone program, yes. Simple pitch: four fifty all-in and we stop renting helicopters — it's in my memo. Two aircraft, sensors, two Part 107 pilots, training. We stop paying charter rates, we control the flight schedule, we stop being hostage to three vendors' backlogs, and the data lands in our hands the same day we fly it. You want to fix the mailed-drives problem? Don't mail drives. It pays for itself against charter costs — the payback table's in there. Fold it into your roadmap and I'll owe you a very good steak.

[00:19:20] KESSLER: I've read it, and it's a serious piece of work — but I'll be straight with you: aircraft acquisition is a capital and operations decision, and Dana's scoped this engagement to software capability. Your own memo actually says it cleanly — that it addresses acquisition capacity, not the data-management gap. Even with your own aircraft, the point clouds still need somewhere to live and a system that tracks them.

[00:19:52] DONNELLY: [laughs] Fine. Yes. That sentence is in there because Anna made me put it in, and she was right. Worth the attempt, though — you can't blame a practice lead for trying to get his aircraft into somebody else's budget. But don't let the scope line make the storage problem sound optional. Drone program or no drone program, the data keeps coming, and it gets bigger every season.

[00:20:45] KESSLER: That lands clearly. Last thing — who else should I talk to on the data side?

[00:20:49] DONNELLY: Kelly, full stop. She runs the account, she saw the quota problem coming a year out, and she's the only person in this building who knows what's actually stored where. And listen to her over the enthusiastic answers you might get elsewhere — some of my juniors are great analysts and casual statisticians, if you take my meaning.

[00:21:15] KESSLER: I take it. Thanks, Ray.

--- END OF TRANSCRIPT ---
