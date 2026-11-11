# Ledgerline Assurance LLC — SOC 2 Type II Readiness Assessment
## Interview Transcript: Priya Raman, CTO & Co-founder, Meridian Health Loop

**Date:** 2026-06-10
**Interviewer:** Katherine Liu, Engagement Director, Ledgerline Assurance
**Interviewee:** Priya Raman, CTO & Co-founder
**Duration:** 1 hour 6 minutes
**Transcription note:** Transcribed from recording 2026-06-12; lightly edited for filler words. Continuation paragraphs by the same speaker carry a timestamp only.

---

[00:00:19] Katherine Liu: Thanks for making the time, Priya. As we covered at kickoff, this session is for the system description and your view of the control environment. I'll caveat up front: nothing you say today is a finding on its own — we'll be corroborating against the exports Gideon is pulling this week, so if you're unsure about something, it's genuinely more useful to say so than to guess.

[00:00:41] Priya Raman: Understood. Fire away.

[00:00:44] Katherine Liu: Let's start with the platform. Walk me through it as if I were a health system's security architect.

[00:00:52] Priya Raman: Sure. So Meridian is a patient workflow platform — we deliver clinical communications over SMS, web, and voice on behalf of health systems. Three flagship workflow templates: ER discharge follow-up, clinical study enrollment, and appointment check-in. A health system configures a workflow, feeds us a patient cohort, and we run the outreach and capture the responses.

[00:01:38] Everything runs in AWS, us-east-1, single production VPC. The data layer is RDS PostgreSQL — a primary and a read replica. Compute is ECS Fargate. We have four core services — workflow-engine, sms-gateway, voice-ivr, and web-portal — plus, sorry, five if you count the internal admin console, meridian-admin, which is staff-facing only.

[00:02:31] Katherine Liu: And the PHI path? Where does patient data enter and leave the boundary?

[00:02:37] Priya Raman: It enters mostly through CloudRx Interfaces — that's our EHR integration partner — into workflow-engine, which orchestrates everything. Outbound SMS goes through Mercury Messaging, they're the aggregator that actually hands messages to the carriers. Voice goes out through Voxbridge Telephony. Inbound replies come back the same paths. Support cases live in Zendesk, analytics lands in Snowflake, transactional email is Sendwell. So yes, PHI does transit a handful of subprocessors — that's disclosed, it's in our architecture memo.

[00:03:52] Katherine Liu: We'll come back to subprocessors. Identity and access first — how do people authenticate?

[00:04:01] Priya Raman: Okta is the front door for the SaaS estate — SSO with MFA enforced. Engineering access to AWS is IAM with roles per function. Remote access into the VPC is over VPN. There's some legacy there that Tom can speak to better than I can — he owns the infra day to day.

[00:04:36] Katherine Liu: Noted — I'm seeing Tom tomorrow, so I'll take the VPN detail with him. Change management?

[00:04:44] Priya Raman: GitHub, protected main branch, PR review required, CI runs the test suite, deploys go through staging and a canary before full rollout. That part of the shop is genuinely tight — it's the part we built as engineers, for engineers, so it got the love early.

[00:05:29] Katherine Liu: That matches what I've seen so far. Now, your May architecture memo — the one prepared for prospect security reviews. It's well written, and I want to test a few of its assertions, because they'll become control statements in the readiness report if they hold. First: encryption at rest. The memo says every data store is encrypted at rest — RDS, S3, ElastiCache, all of it.

[00:06:02] Priya Raman: That's right. RDS is encrypted with KMS, the app buckets are SSE, ElastiCache — actually, hold on, let me not overstate the ElastiCache piece. We turned encryption on when we rebuilt the cluster last year. Yes. So that's right.

[00:17:48] Katherine Liu: Let me put something specific to you, then, because I'd rather do it here than in the report review. Gideon's S3 inventory pull is scheduled for this week, but the pen testers already looked at the bucket estate during fieldwork, and there's an engineering runbook note about a snapshot bucket. What's your understanding of meridian-db-snapshots?

[00:18:26] Priya Raman: The nightly pg_dump copies. Those land in S3 and... hm.

[00:18:40] Right, so — the snapshot bucket — okay, that one predates the default-encryption change, it's on the backlog. It was carved out years ago because of a restore-script issue and it never got cleaned up. I'll be honest, when I wrote the memo I was thinking of the application data stores. The snapshot bucket is a real exception and it's the wrong bucket to have as your exception, I know — those dumps are full production data.

[00:19:34] Katherine Liu: Full production data meaning PHI.

[00:19:37] Priya Raman: Yes. Patient identifiers, message content, the lot. It's on the remediation list — genuinely, it's a config change plus re-encrypting the existing objects, it's days of work, not months. It just hasn't been anyone's top priority.

[00:20:02] Katherine Liu: Understood. For the report we'll characterize it from the inventory export rather than from either the memo or this conversation — I'd encourage you to treat that export as the source of truth internally too. Are there other buckets in the same state, to your knowledge?

[00:20:21] Priya Raman: There might be one or two old ones — legacy exports, deprecated static assets, that vintage. Nothing else with PHI in it, as far as I know. The export will tell you.

[00:25:58] Katherine Liu: Let's do subprocessors. The memo states all subprocessors with PHI access are disclosed to customers and operate under executed BAAs. Can you walk me through the BAA coverage vendor by vendor?

[00:26:15] Priya Raman: AWS, executed, ages ago. CloudRx, executed. Voxbridge, executed. Zendesk, Snowflake, Sendwell, MedTranscribe — executed, Alicia keeps the register. Mercury... I'm pretty sure the Mercury BAA went out for signature last fall. DocuSign, October or November. I'd have to check where it landed — Alicia would know the current status.

[00:27:04] Katherine Liu: "Went out for signature" and "executed" are different states, and for your SMS aggregator the difference is material — Mercury touches essentially every patient message you send. We'll verify against the vendor register and the correspondence rather than resolve it here. And disclosure — is Mercury on the subprocessor list you provide to customers?

[00:27:29] Priya Raman: I believe the list says "SMS delivery partner." Whether it names Mercury specifically, I'd have to look. That's — yeah, I'd have to look.

[00:27:41] Katherine Liu: Okay. Flagging that both halves of the memo's sentence are things we need to evidence, not assume. Access reviews next. The memo says access reviews are conducted regularly. The prior readiness work described a quarterly cadence. What's actually happening?

[00:33:02] Priya Raman: we did them quarterly through last year... this year has been rough. We lost our senior platform engineer in February, the Bayview pilot ate the spring, and the review cycle slipped. I couldn't tell you off the top of my head when the last completed one was. Last fall, probably? September, October?

[00:33:40] Katherine Liu: The access export will carry per-row review dates, so we'll let it speak. I'd only note that for a Type II with a review period opening in October, a quarterly control needs to be operating by then — not designed, operating. The auditors will sample actual review artifacts.

[00:34:02] Priya Raman: Understood. That one's on me to restart.

[00:46:55] Katherine Liu: I want to spend a few minutes on incident management. Tell me about March — INC-2026-007, the St. Aldric discharge batch.

[00:47:12] Priya Raman: The misroute. A header row got dropped on a cohort import and the recipient mapping shifted by one, so a batch of discharge follow-ups went to the wrong phone numbers. Not our best day.

[00:47:30] We caught it late morning, paused the tenant, and we fixed the mapping same day. I know we owe ourselves a retro on it. The import now has a checksum so it can't shift silently again — the engineering fix was real. The paperwork side, the post-mortem, the who-do-we-notify analysis... that didn't happen. It was a brutal quarter and I made the call to keep the team moving.

[00:48:21] Katherine Liu: When you say the who-do-we-notify analysis didn't happen — no assessment was made against your customer contract notification clauses, or against the breach notification framework? By anyone?

[00:48:36] Priya Raman: Not a formal one, no. We told St. Aldric's ops contact there'd been a template glitch and it was fixed. Nothing beyond that. Alicia has since reconciled the actual impact numbers with support — she has the count, I don't want to misquote it. I know where you're going with this, and yes, in hindsight it needed a proper assessment at the time. It will be in your report and it should be.

[00:49:12] Katherine Liu: It will, with the reconciled figures from the support data rather than anyone's recollection — including the question of notification obligations, which ultimately needs your counsel's judgment, not ours. Okay. Last topic: scope. At kickoff we proposed all the common criteria plus Availability and Confidentiality for the review period starting 2026-10-01.

[00:55:41] Priya Raman: Right, and I wanted to push on one thing there. Could we add the Privacy category? For marketing value, honestly — we sell into health systems, "we cover Privacy" sounds like it closes deals. Dana would love it on the one-pager.

[00:56:04] Katherine Liu: I'd push back, and here's the reasoning rather than just the answer. The Privacy criteria attach to commitments an organization makes about personal information practices — privacy notices, choice and consent, data-subject rights. Meridian doesn't make consumer-facing privacy-notice commitments; your patients are your customers' patients, and your obligations run through your business associate agreements. That's a HIPAA overlay, and it's better managed as its own program with its own assessment — which is happening separately. Pulling Privacy into the SOC 2 would add criteria you have no commitments against, which lengthens the audit and creates exception risk with no contractual driver. Availability and Confidentiality, by contrast, are forced in by your executed customer commitments. My advice: hold scope where we proposed it and revisit Privacy at renewal if a contract ever actually demands it.

[00:57:22] Priya Raman: That's a better answer than the one I walked in with. Fine — agreed.

[00:57:29] Katherine Liu: Good. Last thing: between now and October, the remediation window is the whole ballgame. The gaps we write up in June are only a problem if they're still open when the review period starts.

[00:57:48] Priya Raman: Understood. Send us the list with priorities and owners and we'll run it like a release plan. That part we know how to do.

[00:58:01] Katherine Liu: That's exactly the right frame. Thanks, Priya — I'll see Tom tomorrow for the infrastructure deep-dive.

*[End of transcript — 01:06:12]*
