# Ledgerline Assurance LLC — SOC 2 Type II Readiness Assessment
## Interview Transcript: Tom Brzezinski, VP Engineering, Meridian Health Loop

**Date:** 2026-06-11
**Interviewer:** Katherine Liu, Engagement Director, Ledgerline Assurance
**Interviewee:** Tom Brzezinski, VP Engineering
**Duration:** 1 hour 4 minutes
**Transcription note:** Transcribed from recording 2026-06-13; lightly edited for filler words. Continuation paragraphs by the same speaker carry a timestamp only.

---

[00:00:16] Katherine Liu: Tom, thanks for the time. Priya gave me the platform tour yesterday; today I want the operational reality — infrastructure, access, backups, resilience. Same caveat I gave her: I'd rather hear "I don't know" or "it's ugly" than a polished answer we both have to walk back when the exports land.

[00:00:39] Tom Brzezinski: You'll get plenty of both. Where do you want to start?

[00:00:43] Katherine Liu: Your patch. What do you actually own?

[00:00:47] Tom Brzezinski: Everything under the application layer — AWS, networking, the database, CI/CD, on-call, and by default anything nobody else owns, which at a 45-person company is a long list. I've got the platform team, the SREs — well, SRE singular now — and I share the app engineers with Priya.

[00:01:32] Katherine Liu: "SRE singular now" — that's the February departure?

[00:01:36] Tom Brzezinski: Evan Marsh, yeah. Senior platform engineer, was here from the early days. We'll get to him, I assume.

[00:01:44] Katherine Liu: We will. Let's do endpoints first, since it came up in the pen-test readout. What's the managed-device picture?

[00:09:12] Tom Brzezinski: engineering, clinical ops, most of G&A are on corp machines. sales and a chunk of CS are on personal machines — I honestly don't track it, the Kandji export has the real number. The corp machines are all in Kandji, FileVault enforced, screen lock, the standard profile. The personal machines are just... personal machines. No MDM, no visibility, nothing. Sales has always pushed back on carrying two laptops and nobody's made them.

[00:10:03] Katherine Liu: And those unmanaged machines — do they touch PHI?

[00:10:08] Tom Brzezinski: They can. Sales does demos off real customer dashboards more than I'd like, and the CS folks on personal machines work support cases in Zendesk all day, which is patient data. So yes. I'm not going to pretend otherwise.

[00:10:31] Katherine Liu: Appreciated. I'll size the population from the Kandji export against the roster rather than anyone's estimate — there are a few different numbers floating around internally.

[00:10:42] Tom Brzezinski: I've seen the Slack thread you're referring to. Trust the export.

[00:21:50] Katherine Liu: Remote access. Yesterday I heard "Okta with MFA for the SaaS estate, and some legacy on the VPN side." Fill in the legacy.

[00:22:08] Tom Brzezinski: Okta's genuinely fine — SSO, MFA enforced, that's the front door for everything SaaS. The problem is the old Fortinet VPN, vpn2. It predates Okta, it's got its own local accounts, password only, no MFA. It's supposed to be replaced — there's been a "migration" on the roadmap for over a year, we even ran a Twingate trial — but vpn2 is still what on-call uses to reach the production VPC at 3 a.m.

[00:22:45] And it's password-only in 2026, which is how the pentest guys walked right in with a password spray. That finding is completely fair. A dozen-plus people still have local accounts on that box and I'd bet money some of those passwords are recycled.

[00:23:19] Katherine Liu: So the accurate statement is: MFA on remote access is enforced for the Okta-fronted estate, and not enforced on the legacy VPN path into production.

[00:23:29] Tom Brzezinski: That's exactly the accurate statement. If someone's written down "100% MFA on remote access" anywhere, that's not a statement I'd sign.

[00:23:41] Katherine Liu: Someone has, but let's stay on infrastructure. Backups.

[00:23:47] Tom Brzezinski: Nightly pg_dump of the production database, copied to an S3 bucket, meridian-db-snapshots. Fourteen-day retention on the nightlies plus month-end archives. Restores get tested informally whenever we refresh staging from a snapshot, so I do know the dumps are restorable — that part works.

[00:24:26] The bucket itself is the embarrassing part. It got carved out of the KMS migration in 2024 — the restore script kept choking on KMS objects — so it's still on the account-default SSE-S3 key instead of the meridian-phi customer-managed key our own encryption standard requires for PHI. It's been a TODO in the runbook ever since. And before you ask — yes, that's the same bucket the pen testers reached with the leaked IAM key. Account-default encryption doesn't gate anything: any principal with GetObject gets plaintext. It's the worst finding in the report and I don't dispute an inch of it.

[00:25:10] Katherine Liu: What's the fix and the timeline?

[00:25:16] Tom Brzezinski: Rotate and scope the IAM keys — that's basically done as of last week. Then move the bucket onto the meridian-phi KMS key, re-encrypt the existing objects, fix the restore script so it stops choking on KMS, and block public access at the account level. It's a week of careful work. It should have been done two years ago; there's no technical obstacle, it just kept losing the priority fight.

[00:40:38] Katherine Liu: Disaster recovery. The information security policy commits to annual DR testing, and the 2025 readiness materials describe the same. Walk me through the last test — when it ran, what it covered, where the results are.

[00:41:02] Tom Brzezinski: You want the policy answer or the honest answer? Because they're not the same answer.

[00:41:05] Katherine Liu: The honest one, always.

[00:41:07] We have never actually run a failover test. Not once. The DR doc is from 2023 and half the runbook steps reference infrastructure we've already replaced. There's a warm standby configuration on paper and a read replica we could promote, but nobody has ever pulled the lever end-to-end. Last October we had an availability-zone event that would have been the natural moment, and we rode it out instead of failing over — partly because degraded service felt safer than an untested failover, which tells you everything about our confidence in the procedure.

[00:42:11] Katherine Liu: So the honest control status is: a DR plan exists, it's three years stale, and the test control has never operated. Given your customers hold you to an uptime SLA with service credits, that lands as a priority item — the review period opens in October, and an auditor will ask for test evidence from within the period.

[00:42:34] Tom Brzezinski: Then we run our first real failover test this summer. Honestly, I'll be glad to have the audit as the forcing function — I've wanted to do this for two years.

[00:51:47] Katherine Liu: Let's do the topic you predicted. Offboarding, and specifically Evan Marsh.

[00:52:02] Tom Brzezinski: Evan left in February — February 13th, a Friday. Not on good terms, for what it's worth, it was a termination. Rosa filed the deprovisioning request with IT the following Monday, ticket and everything. And then... nothing happened. There's no owner for that queue since — well, ironically, since Evan, because he's the one who used to do the actual deprovisioning.

[00:52:19] his stuff should have been killed in February. if it's still live in that export, that's... yeah. AWS, GitHub, the VPN, direct database credentials — he had all of it, he was the most privileged engineer we had. Four months. I have no reason to think he's touched anything, and also no way to prove he hasn't, which I understand is kind of the point of the control.

[00:53:14] Katherine Liu: It is. And he's not unique — the same export method will catch anyone else whose termination didn't propagate.

[00:53:23] Tom Brzezinski: There'll be at least a couple more. A CS departure from last fall, I think, and one from clinical ops this spring. Offboarding here is an email to IT and hope. The checklist Rosa maintains is honest about it — the offboarding half literally says draft on it.

[00:58:14] Katherine Liu: Last one, and I'll ask it directly because it surfaced in the pen test's CloudTrail review. The AWS root account — who can use it, and how is it protected?

[00:58:30] Tom Brzezinski: Yeah. The root login sits in a shared 1Password vault. Three of us have used it — me, Jenny, and Evan before he left, which means as of today the credential a terminated employee knew is still valid. And no MFA on root. I know exactly how bad that sounds because it is exactly that bad. It's a startup habit from 2021 that nobody ever unwound — billing lives on root, so people keep reaching for it.

[00:59:21] Katherine Liu: The remediation is mechanical, at least: rotate the root credential today, hardware MFA on it, break-glass procedure, and IAM billing access so nobody needs root for an invoice again.

[00:59:34] Tom Brzezinski: The rotation happens this week — that one I can promise you. The rest goes on the list with a name next to it. Preferably not mine, but it'll be mine.

[00:59:46] Katherine Liu: A name and a date is all the report needs. Tom, this was genuinely useful — candor makes readiness work about ten times faster. We'll validate everything against the exports, but you've saved us a lot of digging.

[01:00:02] Tom Brzezinski: That's the job. Send me the list — and put the failover test on it in bold.

*[End of transcript — 01:04:33]*
