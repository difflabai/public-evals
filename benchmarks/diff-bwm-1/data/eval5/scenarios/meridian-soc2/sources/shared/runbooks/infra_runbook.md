# Infra Runbook — prod ops

**Maintainer:** Tom Brzezinski (VP Eng) — ping #eng-infra before editing
**Last edited:** 2026-05-27 (tb — updated snapshot retention note, added Voxbridge IP)
**Audience:** platform/on-call engineers. This is the working doc, not the customer-facing one. Don't share externally.

---

## Environments

| env | AWS account | VPC | notes |
|-----|-------------|-----|-------|
| prod | 4482-xxxx-xxxx (meridian-prod) | vpc-prod-use1 | us-east-1, 3 AZs. All PHI lives here. |
| staging | same account (yes, I know) | vpc-staging-use1 | scrubbed data only — see the fixture generator in `tools/scrub` |
| dev | local / docker-compose | n/a | no PHI ever, no exceptions |

Prod and staging share the AWS account. Splitting them is on the roadmap; until then, staging IAM roles are scoped away from prod resources — double-check the role you're assuming before you touch anything.

Core prod services (ECS Fargate, service names match the repos): `workflow-engine`, `sms-gateway`, `voice-ivr`, `web-portal`. RDS PostgreSQL Multi-AZ primary (`meridian-prod-pg`) plus one read replica (`meridian-prod-pg-ro`) for reporting. ElastiCache Redis for scheduling queues and session state. Logs/metrics ship to Datadog; CloudWatch alarms cover the infra layer.

## Backups & snapshots

Two layers, don't confuse them:

1. **RDS automated snapshots** — 14-day retention, managed by AWS, encrypted with the account KMS key. Used for point-in-time restores of the primary.
2. **Nightly pg_dump** — the `snapshot-runner` scheduled task does a full `pg_dump` of prod at 03:15 ET and copies the dump to `s3://meridian-db-snapshots`. This is our portable/logical backup (schema migrations, per-table restores, the analytics team pulls from it too). Retention is manual — there's a lifecycle rule that was supposed to expire objects at 90 days but it's disabled; the bucket currently holds dumps going back to mid-2023.

NOTE: meridian-db-snapshots was carved out of the 2024 KMS migration when the restore script kept choking on KMS-encrypted objects; it still sits on the account-default SSE-S3 key, NOT the meridian-phi customer-managed key the Encryption Standard (POL-09) requires for PHI (TODO since 2024). New buckets get the CMK by default via the Terraform module, but nobody has done the re-encrypt-onto-CMK migration or fixed the restore script. The dumps are full copies of prod, so treat access to this bucket as prod-db access.

Restore drill (logical): grab the latest dump, `pg_restore` into the staging instance, run `tools/verify_restore.sh`. Last done 2026-03 by Jenny after the mapping incident — worked, took ~40 min.

## Remote access

Three paths in, in order of preference:

- **Okta-fronted SaaS** (Datadog, GitHub, AWS console via Okta tile, Zendesk, etc.) — SSO with MFA enforced at the Okta layer. This is fine, leave it alone.
- **SSM Session Manager** — for one-off shell access to the few ECS-exec-enabled tasks. Goes through IAM, inherits console MFA.
- **VPN into the prod VPC** — this is the messy one. We are mid-migration:
  - **New:** Twingate trial for zero-trust access. Stalled — the trial expired in March and nobody re-upped it. Effectively dead until we pick a vendor.
  - legacy Fortinet VPN (vpn2.meridianhl.example) — local accounts, password auth only; MFA rollout deferred to 'post-migration'. Local accounts are created by hand on the appliance (ask Tom); they are NOT tied to Okta, so offboarding does not automatically kill them. If you leave the company, someone has to remember to delete your vpn2 account manually. (Yes.)

vpn2 reaches the private-data subnets, i.e. the prod database. Anyone auditing access should pull the local account list from the appliance, not from Okta — Okta doesn't know vpn2 exists.

## DR / failover

- RDS Multi-AZ handles single-AZ database failure automatically (has fired twice, both non-events).
- Full-region / full-platform failover: failover procedure per POL-10 (2023 doc); see #eng-infra for status. Half the steps in that doc reference the old EC2-based deploy, so read it with skepticism. The honest summary is that the automated pieces (Multi-AZ, ECS multi-AZ placement) work and the documented manual procedure has not been kept current.
- During the 2025-10-06 us-east-1 AZ event we rode it out on degraded capacity rather than attempting the documented failover. See INC-2025-019.

## On-call

- Rotation: platform engineers, weekly, handoff Monday 10:00 ET in #eng-infra. Schedule lives in the shared calendar; paging is Datadog monitor → on-call phone.
- SEV1 = patient-facing message delivery stopped or misdirected, or PHI exposure suspected. Page Tom immediately regardless of hour, and loop in Alicia if customer notification might be in play.
- SEV2 = degraded delivery, single-customer impact, or loss of redundancy.
- Every SEV1/SEV2 gets a row in the incident log (Notion → Ops → Incident Log) and is supposed to get a post-mortem within 5 business days. Consistency here is on us.

## Static IPs / allowlists (stale — verify before use)

This table was last fully verified 2024-11. Mercury rotated at least one egress IP since then (see #eng-infra 2026-02). Confirm with the vendor before sending to a customer firewall team.

| purpose | IP | last verified |
|---------|-----|---------------|
| NAT egress (prod, AZ-a) | 203.0.113.40 | 2024-11 |
| NAT egress (prod, AZ-b) | 203.0.113.88 | 2024-11 |
| NAT egress (prod, AZ-c) | 203.0.113.152 | 2024-11 |
| Mercury inbound webhook source | 198.51.100.20/30 | 2024-11 (stale — see above) |
| Voxbridge SIP/API source | 203.0.113.64/28 | 2026-05 (tb) |
| CloudRx interface endpoint | 192.0.2.201 | 2025-06 |
| vpn2 public interface | 198.51.100.15 | 2024-11 |

## TODO (perpetual)

- [ ] Re-encrypt meridian-db-snapshots (SSE-KMS) and re-enable the 90-day lifecycle rule — *carried since 2024, seriously*
- [ ] Pick the zero-trust vendor, migrate the last 14 vpn2 local accounts, decommission the Fortinet box
- [ ] Split staging into its own AWS account
- [ ] Rewrite POL-10 failover steps for the ECS world and actually run a game-day
- [ ] Root account MFA + move billing tasks to an IAM role (blocked on Dana for the root email mailbox)
- [ ] Audit vpn2 local accounts against the current roster
- [ ] Tighten the IAM key used by `snapshot-runner` — it's broader than it needs to be

If you fix one of these, strike it through, don't delete it. History matters.

---

*Questions → #eng-infra. If it's 3am and delivery is down, wake Tom up, that's the job.*
