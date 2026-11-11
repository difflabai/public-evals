# Interview Transcript — Tom Okafor (Principal Engineer, PLS-CADD Design & Rebuild)

**Engagement:** Castellan Grid Consulting — Custom Software Needs Assessment
**Date:** 2026-06-16
**Location:** Castellan Denver HQ, conference room 3B
**Participants:** Alex Kessler (Foundry Systems Advisory, engagement lead); Tom Okafor (Principal Engineer; Practice Lead, PLS-CADD Design & Rebuild)
**Recording:** Consented. Transcribed from audio by Foundry; lightly edited for clarity. Timestamps approximate.

---

[00:00:05] KESSLER: Tom, thanks for the time. Tell me about the design practice.

[00:00:09] OKAFOR: Nineteen of us across the three offices. Everything runs through PLS-CADD — line design, rebuild modeling, clearance checks, structure loading. If Castellan bills it and it has a tower in it, it went through one of my people's PLS-CADD sessions first.

[00:00:41] KESSLER: Walk me through where the friction is.

[00:00:44] OKAFOR: Two places, and they're not subtle. Seats and files. Seats: we have nine concurrent licenses on the FlexLM server. On a normal Tuesday in the busy season, somebody is sitting in the checkout queue instead of working. The license manager logs all of it — denials, waits, the whole indictment. Kelly pulls me the report monthly. Files: every project lives on the CGC-FS01 share. No check-in, no check-out, no version control, no nothing. Just folders, and inside the folders, chaos with a naming convention stapled to it.

[00:02:30] KESSLER: Let's take files first. How does the chaos actually bite?

[00:02:36] OKAFOR: Red Mesa. That's the case study, and it cost us real money and a client's patience. Somebody revised the structure alignment in September, saved it under a new name, and the superseded one kept living in the working folder with FINAL_v2 in the filename. A modeler picked it up — reasonably! the name said final! — and we modeled on a dead alignment for three weeks before anyone caught it. FINAL_v2 means nothing; Red Mesa died on a stale alignment. You can write naming memos until the sun burns out. The share doesn't care. The share has no opinion about which file is current, and that's the entire problem.

[00:04:50] KESSLER: What would fix it?

[00:04:54] OKAFOR: An engineering data vault. Check-in, check-out, single current revision, full history, who-touched-what. The mechanical CAD world has had PDM systems for twenty-five years. We need that shape of thing for line-design data — PLS-CADD projects, alignments, survey files, the LiDAR-derived surfaces we reference. Lock a file when it's checked out, supersede explicitly, and make "which version is current" a question the system answers instead of a question you ask three people on Slack at 4:55 on a Friday.

[00:06:40] KESSLER: Buy that, or build it?

[00:06:43] OKAFOR: You want my unfiltered opinion? Because honestly we could write a project data manager ourselves in a summer. A database, a file store, a check-out flag, a web front end. I have two engineers who'd do it for the fun of it. I know that's not the answer you're going to write down, and I know Priya would set my badge on fire — but I want it on the record that this is not moonshot software. What matters is the workflow fits how line designers actually work. The generic document systems don't. Don't let anybody tell you SharePoint does this, by the way. SharePoint is where PowerPoints go. A PLS-CADD project is a constellation of interlinked files with survey data hanging off it — you put that in SharePoint and you'll have the same chaos with worse performance and a sync client that eats files for texture.

[00:09:10] KESSLER: Understood. Let's do seats. Nine licenses — what's the demand side look like?

[00:09:17] OKAFOR: Pull Kelly's license report, Jan through May, and pull the timesheets — you'll see the same picture from two directions. Peak demand runs above what we hold, denials pile up in the crunch months, and my seniors ration mornings. March and April were ugly. People started checking out a seat at 6:30 a.m. to hold it, which of course makes the contention worse — it's a little tragedy of the commons with engineering licenses.

[00:11:05] KESSLER: Does the delay show up in schedules?

[00:11:09] OKAFOR: It showed up in Red Mesa as roughly three weeks of calendar, per Elena's post-mortem, and that's the documented one. The undocumented tax is daily. An engineer in the queue for two hours doesn't bill those two hours to modeling. Multiply that across a season.

[00:13:20] KESSLER: The COO's memo describes the firm as effectively a fourteen-concurrent-seat shop. Does that square with what you see?

[00:13:29] OKAFOR: [laughs] Dana said fourteen? Look, I love her ambition. Fourteen is what you'd buy if licenses were free. That's not a measurement, that's a mood. I live in the checkout queue with these people every day, and I'll give you the operational answer instead.

[00:14:10] KESSLER: Which is?

[00:14:12] OKAFOR: Look at the license-manager peaks and the timesheet concurrency and they'll tell you where the ceiling actually is. It's above nine — obviously, or nobody would be queuing — and it's below Dana's number. I'd rather you trust Kelly's FlexLM logs than my gut or anyone else's.

[00:16:45] KESSLER: If the firm did add seats, what does one cost?

[00:16:50] OKAFOR: Rough numbers: a seat runs about twelve-five up front plus twenty-four hundred a year maintenance. So it's not nothing, but hold it against what a stalled crew costs on a fixed-fee rebuild and it's not a hard conversation. The hard part has never been the math. The hard part is that nobody owns the license capacity question — IT thinks it's a practice budget issue, the practice thinks it's an IT issue, and the renewal just rolls at nine every year on autopilot.

[00:19:12] KESSLER: So what's the right number? If I gave you the purchase order right now.

[00:19:17] OKAFOR: The honest answer is two, maybe three more seats and the waitlist goes away. Check it against the logs — I think the logs back me — but that's what the queue is telling me every morning. I don't need fourteen. I need my people to stop planning their day around a license server.

[00:21:30] KESSLER: Last area. The LiDAR side references your models —

[00:21:34] OKAFOR: Wrong practice lead, and I'm disciplined about my lane. Ray owns the LiDAR program and the numbers that go with it; Kelly owns where the data physically lives. What I'll say is that my designers consume the classified point clouds and the surfaces, and getting the current one out of the current pile is the same versioning disease in a bigger body. Same vault, same fix — that's my only claim there.

[00:22:58] KESSLER: If the assessment funds exactly one thing for your practice, what is it?

[00:23:02] OKAFOR: The vault. Seats are a purchase order; the vault is a capability. Red Mesa wasn't a license problem, it was a truth problem — nobody could say which file was true. Fix truth first. Then buy the seats, because the queue is real too. And if your report says the word SharePoint I will personally come to your office.

[00:23:40] KESSLER: Noted with appropriate fear. Thanks, Tom.

--- END OF TRANSCRIPT ---
