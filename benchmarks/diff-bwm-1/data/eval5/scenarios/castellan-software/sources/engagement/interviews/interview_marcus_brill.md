# Interview Transcript — Marcus Brill (Principal Engineer, Line Ratings)

**Engagement:** Castellan Grid Consulting — Custom Software Needs Assessment
**Date:** 2026-06-15
**Location:** Castellan Denver HQ, conference room 3B
**Participants:** Alex Kessler (Foundry Systems Advisory, engagement lead); Marcus Brill (Principal Engineer; Practice Lead, Line Ratings)
**Recording:** Consented. Transcribed from audio by Foundry; lightly edited for clarity. Timestamps approximate.

---

[00:00:07] KESSLER: Thanks for making time, Marcus. I know June is a bad month to ask for an hour.

[00:00:13] BRILL: Every month is a bad month to ask for an hour. But Dana asked, so here I am. Ask your questions.

[00:00:21] KESSLER: Let's start with the ratings practice. What does the work actually look like day to day?

[00:00:29] BRILL: Utilities need to know what their conductors can carry without violating clearances or annealing the wire. We do that math. Static ratings, ambient-adjusted ratings, and increasingly dynamic line ratings — DLR — where the rating moves with weather. Fourteen people in the practice. The math itself is IEEE Standard 738. Steady-state and transient thermal ratings of bare overhead conductors. It is not mysterious. It is just easy to get wrong if you're sloppy.

[00:01:18] KESSLER: And RATER is the tool that does that math here.

[00:01:22] BRILL: RATER is the tool. I built it in 2011, in Access 2010, because what we had before was a shoebox of spreadsheets and I got tired of finding sign errors in other people's radiation terms. It does the full 738 calculation — steady-state and transient — it holds the conductor library, the AAR lookup tables per client and per region, and it runs batch recalculations. A DLR study like Sagebrush is something like 240 conductor segments, each with its own weather inputs. You queue the batch, RATER chews through it overnight, and in the morning you have a defensible number for every segment with the assumptions logged next to it.

[00:03:05] KESSLER: How many people can run it?

[00:03:08] BRILL: Run a single rating? Four or five of my engineers, if the wind is right. Set up and run a batch job, restructure a lookup table, chase down why a segment result looks off? That's me. I'm not proud of that — well, I'm a little proud of it — but I'm aware it's a problem. Sagebrush made it everyone's problem. I was out for three weeks in February, medical thing, all resolved, and the batch queue just sat there. People tried to reproduce pieces of it in Excel. Two of those numbers were wrong and Sam's QA caught them late. I read the post-mortem. It's fair.

[00:05:40] KESSLER: Is there documentation? If someone new needed to learn the batch side —

[00:05:46] BRILL: The documentation is me. There's a README from 2014 and some notes in the code, but honestly, the documentation is me. I know how that sounds.

[00:06:12] KESSLER: How would you characterize the state of the code itself?

[00:06:16] BRILL: It's Access and VBA, thirty-some forms, a couple hundred modules. It's tidy by my standards, which I concede are private standards. Look — we've tried to hire for this. Twice we got close. Two sharp candidates, both had the ratings background, both got a look at the codebase during the technical interview and — I'll be honest with you — both withdrew. One of them sent a very polite email saying he'd decided to, quote, pursue opportunities in modern environments. The other one just stopped answering the recruiter. Which, fine. I understand. I once spent a weekend in 2016 tracking a bug to a form control that only misfired when the segment count was a multiple of 64. You don't forget a weekend like that. My point is, the tool works. The wrapper it comes in scares people. Those are different problems and I'd thank you not to confuse them in your report.

[00:09:30] KESSLER: Fair. Let me ask about the data inside RATER, not the code. How far back does the ratings history go?

[00:09:38] BRILL: All the way. That's one of the things nobody gives it credit for.

[00:11:04] KESSLER: When you say all the way —

[00:11:07] BRILL: RATER's got fifteen years of ratings history in it — everything back to 2011, plus the legacy runs I imported going back to 2005. Every rating we've issued, with its assumptions. When Palisade asks why a 2012 rating differs from a 2024 rating on the same span, I can answer that in an afternoon. Try doing that from a vendor's cloud thing where the history starts the day you sign the contract.

[00:12:35] KESSLER: Do you pull from the older archive often?

[00:12:38] BRILL: When it's needed. It isn't needed often — most trend questions live in the last five or six years. But it's there, and "it's there" is worth a lot in this business.

[00:13:20] KESSLER: You mentioned vendors. GridVault has pitched the firm — they claim a ratings module. Have you looked at it?

[00:13:29] BRILL: I sat through the demo in May. It's toy math, they interpolate where 738 says integrate. I asked their sales engineer how they handle the transient case for a fault-clearing scenario and he told me, with a straight face, that most customers don't ask about that. Most of our clients are the ones who ask about that. I'll grant their charts are pretty. Charts are cheap.

[00:15:02] KESSLER: If the firm decided to replace RATER with something supportable — what would that take, in your view?

[00:15:10] BRILL: Less than people assume — a proper ratings engine is six months of a good engineer's time, not some vendor's module. The 738 math is published. The conductor library is a table. The batch runner is a queue. What takes care is the validation — you regression-test the new engine against RATER's outputs across the whole conductor library, every ambient case, until the residuals are noise. I'd insist on that regardless of what you build or buy. If your report says "buy the module and trust it," you and I are going to have words.

[00:17:44] KESSLER: Would you be part of that validation?

[00:17:47] BRILL: I'd better be. I'm here through December next year — I've told Dana, December and then I'm done, there's a drift boat and a stretch of the Gunnison with my name on it. Eighteen months, give or take. That's enough time to do this right and barely enough time to do it twice, so I'd rather we not do it wrong the first time.

[00:19:55] KESSLER: If we lined up a successor or a build team tomorrow, what's the handover plan?

[00:20:01] BRILL: Sit with me. Run the batches with me for a season. I can write down the parts that write down — the AAR table conventions, the client-specific assumptions, why the Bonanza tables have that oddball emissivity. But some of it is judgment, and judgment transfers by apprenticeship, not by wiki. I've said this to three COOs now. Dana is the first one who seems to be doing something about it, which is why you're sitting here.

[00:22:30] KESSLER: Anything you'd want the assessment to say that I haven't asked about?

[00:22:36] BRILL: Two things. One: whatever you recommend, the ratings math is the crown jewels. It's why Palisade pays us. Don't let anyone treat it as a checkbox in some suite. Two: don't let them cheap out on validation time because a slide said "white-glove onboarding." Slides don't stamp calcs. Engineers stamp calcs.

[00:23:40] KESSLER: Understood. Thanks, Marcus.

[00:23:43] BRILL: Tell Dana I was polite.

--- END OF TRANSCRIPT ---
