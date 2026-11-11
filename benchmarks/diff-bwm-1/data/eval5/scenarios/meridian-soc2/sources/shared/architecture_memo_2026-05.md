# Meridian Platform Architecture & Data Flows

**Author:** Priya Raman, CTO, Meridian Health Loop
**Date:** 2026-05-08
**Audience:** Prospect and customer security review teams
**Classification:** Confidential — shareable under NDA

---

## 1. Purpose

This memo describes the architecture of the Meridian patient-workflow platform and the paths that protected health information (PHI) takes through it. It is written for security reviewers at prospective health-system customers and is the canonical companion to our security questionnaire responses. Questions can be directed to me or to our compliance lead, Alicia Fontaine.

## 2. Topology

The entire Meridian production environment runs in a single AWS account in **us-east-1**, inside a dedicated production VPC with public, private-application, and private-data subnet tiers spread across three availability zones.

**Data tier.**

- **Amazon RDS for PostgreSQL** is the system of record: workflow definitions, workflow execution state, patient contact records, message history, and audit events. We run a Multi-AZ **primary with one read replica**; the replica serves reporting queries and keeps analytical load off the primary.
- **Amazon ElastiCache (Redis)** provides short-lived caching for session state and workflow scheduling queues.
- **Amazon S3** holds application assets, log archives, report exports, and database snapshots.

**Application tier.** All services are containerized and run on **ECS Fargate** — there are no EC2 hosts to patch. The four core services:

- **workflow-engine** — the orchestrator. Consumes patient events, evaluates workflow branch logic, schedules outbound communications, and processes inbound patient responses.
- **sms-gateway** — the delivery service for text messaging. Manages carrier-facing throughput, delivery receipts, and retry logic against our SMS aggregator's API.
- **voice-ivr** — outbound voice workflows. Places automated calls, plays clinically-reviewed scripts, and captures IVR keypad/speech responses.
- **web-portal** — the customer-facing configuration and reporting application, and the patient-facing web check-in surface.

**Edge and workforce access.** Customer and patient web traffic terminates at an Application Load Balancer with AWS WAF. Workforce access to corporate SaaS is fronted by **Okta SSO**. Engineering access to the production VPC is via VPN.

**Observability.** Service logs and metrics ship to **Datadog**; CloudWatch alarms cover infrastructure-level signals. On-call rotation and paging are managed in-house.

## 3. Data flows

The PHI path through the platform is deliberately narrow and easy to audit:

1. **Ingestion.** Customer EHR interfaces deliver patient events (discharge records, appointment schedules, study rosters) through **CloudRx Interfaces**, our healthcare integration partner, over mutually-authenticated TLS. CloudRx normalizes HL7v2/FHIR payloads into Meridian's event schema.
2. **Orchestration.** The **workflow-engine** matches each event to the customer's configured workflow, evaluates eligibility and timing rules, and writes workflow state to RDS.
3. **Delivery.**
   - SMS messages are handed to the **Mercury Messaging** API — our SMS aggregator — by the sms-gateway service. Mercury manages carrier connectivity and returns delivery receipts.
   - Voice calls are placed through **Voxbridge Telephony**, which provides the outbound calling and IVR capture layer for voice-ivr.
   - Patient-facing web interactions (check-in forms, secure content links) are served directly by web-portal.
4. **Response handling.** Inbound SMS replies and IVR captures return through Mercury and Voxbridge respectively, are matched to the originating workflow execution, and drive branch logic — including escalation to customer nurse lines when clinical thresholds are met.
5. **Reporting.** Aggregated workflow outcomes flow to our analytics warehouse for customer-facing reporting dashboards in web-portal.

At no point does PHI leave AWS us-east-1 except to the delivery partners named above and to the customer's own systems. There is no offshore processing and no advertising or secondary use of patient data, ever.

## 4. Security posture

**Encryption.** Every data store in the Meridian platform is encrypted at rest under customer-managed KMS keys per our encryption standard — RDS, S3, ElastiCache, all of it. Encryption is table stakes and we treat it that way. All traffic is TLS 1.2+ in transit, both externally and between internal services.

**Access control.** Production access follows least privilege: engineers authenticate individually, production database access is restricted to the platform team, and access reviews are conducted regularly. Workforce SaaS access is centralized behind Okta with MFA. Secrets are managed in a dedicated vault, not in code or configuration files.

**Subprocessors.** We keep our subprocessor footprint deliberately small — an SMS aggregator, a telephony provider, a healthcare integration layer, and the AWS platform itself, plus a handful of supporting SaaS tools. All subprocessors with PHI access are disclosed to customers and operate under executed BAAs. Our vendor management process reviews each subprocessor's security posture at onboarding and renewal.

**Application security.** Code changes flow through pull-request review and CI checks before deployment. We commission third-party penetration testing of the platform and remediate findings according to severity.

**Monitoring and incident response.** Datadog alerting and CloudWatch alarms feed the engineering on-call rotation. Our incident response plan (refreshed January 2026) defines severity levels, roles, and communication expectations, including customer notification where contractually required.

## 5. Availability and resilience

The platform is designed to degrade gracefully rather than fail hard. RDS runs Multi-AZ with automated failover; ECS services run across three availability zones with health-checked auto-replacement; delivery to carriers is queue-backed, so a downstream partner outage delays rather than drops patient messages. Nightly database snapshots provide point-in-time recovery, and our disaster recovery plan provides for restoration of the full platform from backups in an alternate configuration. Historical platform availability has been strong, and we stand behind the uptime commitments in our customer agreements.

## 6. What's next

Through 2026 we are investing in three areas: completing the migration off our legacy VPN onto a zero-trust access model, expanding our voice capabilities with structured transcription (via MedTranscribe AI, which operates under BAA), and deepening the analytics layer so customers can benchmark workflow outcomes across service lines.

We're proud of this architecture. It is small, legible, and built around a single question: can we explain to a health system's CISO exactly where their patients' data is at every moment? The answer is yes — and this memo is that explanation.

---

*Priya Raman, CTO — praman@meridianhl.example*
