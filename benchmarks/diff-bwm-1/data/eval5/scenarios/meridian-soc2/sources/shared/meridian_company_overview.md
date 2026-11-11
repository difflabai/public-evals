# Meridian Health Loop — Company Overview

**Internal reference document — please keep current when sharing externally**
**Last updated:** 2026-04
**Maintained by:** Dana Okafor (CEO), with technical sections by Priya Raman (CTO)

---

## Mission

Meridian Health Loop closes the communication gap between health systems and the patients they serve. Care doesn't end when a patient walks out of the ER or hangs up with a scheduler — but for most health systems, communication does. We build the connective tissue: automated, clinically-reviewed patient workflows delivered over the channels patients actually use, so that no discharge instruction, study invitation, or appointment reminder falls into the void.

Our founding insight, from Dana's years running patient-access operations and Priya's work on clinical messaging infrastructure, is simple: patients don't download apps, but they do answer texts. Meet patients where they are, and follow-through rates change dramatically.

## Product

The Meridian platform delivers patient workflows over **SMS, web, and voice** on behalf of health systems. A workflow is a clinically-reviewed sequence of outbound messages, patient responses, branch logic, and escalation rules, configured per customer and per service line.

Our three flagship workflow templates:

- **ER discharge follow-up.** Patients discharged from the emergency department receive their discharge instructions, medication reminders, and symptom check-ins over SMS in the days following the visit, with escalation to a nurse line when responses indicate deterioration.
- **Clinical study enrollment.** Research teams use Meridian to screen, consent-schedule, and retain study participants, replacing phone-tag with structured two-way messaging.
- **Appointment check-in.** Pre-visit reminders, digital check-in links, and day-of wayfinding reduce no-show rates and front-desk congestion.

Customers configure workflows through the Meridian web portal; patients never install anything. Voice workflows (automated outbound calls with IVR response capture) serve patient populations that prefer or require a phone call over a text.

Because our workflows carry protected health information on behalf of covered entities, Meridian operates as a **business associate** under HIPAA for every customer engagement.

## Customers

Meridian serves **six health-system customers** today, spanning academic medical centers and regional community systems:

- **St. Aldric Health** — flagship pilot, ER discharge follow-up across two emergency departments (expansion under discussion)
- Calderwood Regional Medical Center — appointment check-in
- Tri-County Health Alliance — clinical study enrollment (two active studies)
- Northgate Community Hospital System — ER discharge follow-up
- Blue Elm Health — appointment check-in and discharge follow-up
- Sagebrush Health Partners — clinical study enrollment

**Bayview Regional Health Network** is in late-stage sales for a system-wide discharge follow-up deployment; a pilot agreement is in place and security review is under way. Both St. Aldric and Bayview have identified an independent SOC 2 Type II examination as a condition of expanded contracting in 2027, which is a top company priority for the second half of 2026.

## Team

Meridian is **a team of 45 across engineering, clinical operations, CS, and sales**, headquartered in Durham, North Carolina, with remote staff concentrated in Ohio and Texas. Engineering is our largest function, followed by clinical operations — the nurses and clinical informaticists who review every workflow template before it touches a patient. Customer success owns onboarding and workflow configuration; the sales team is focused exclusively on health systems.

Leadership:

- **Dana Okafor** — CEO & co-founder
- **Priya Raman** — CTO & co-founder
- **Tom Brzezinski** — VP Engineering
- **Marcus Webb** — VP Sales
- **Alicia Fontaine** — Compliance & Privacy Lead / CS Operations
- **Rosa Delgado** — People Ops Manager

## Architecture at a glance

*(Section maintained by Priya Raman; see the platform architecture memo for the full picture.)*

The Meridian platform runs entirely in **AWS us-east-1**. Core components:

- **PostgreSQL on RDS** as the system of record for workflow state, patient contact records, and message history
- **ECS-based services** for workflow orchestration, message delivery, voice IVR, and the customer web portal
- Managed AWS services (S3, ElastiCache, CloudWatch) supporting storage, caching, and observability
- **Okta SSO** fronting our corporate SaaS applications for workforce access

Patient messages flow from customer EHR interfaces into the workflow engine, which schedules and delivers outbound communications through our SMS delivery partner and our telephony provider. Inbound patient responses return through the same channels and drive workflow branch logic.

## Compliance & Trust

Trust is the product. Health systems hand us their patients' contact information and clinical instructions; we treat that responsibility as the foundation of the business.

- **HIPAA.** Meridian operates as a business associate and executes BAAs with its customers. All data is encrypted at rest and in transit, access to production systems is restricted to authorized personnel, and every subprocessor operates under an executed BAA.
- **SOC 2.** Meridian completed a SOC 2 Type I examination in August 2025 (Castlewick Advisory) and is preparing for a Type II examination with a readiness assessment beginning June 2026.
- **Security program.** Our security policies cover access control, incident response, encryption, vendor management, and business continuity, with a fractional vCISO (engaged April 2026) supporting the program's maturation.

For customer security reviews, contact Alicia Fontaine (compliance@meridianhl.example) or your account executive; the standard security packet includes our architecture memo, policy summaries, and the 2025 Type I report.

---

*Questions or corrections: Dana Okafor. Please do not circulate stale copies — the canonical version lives in Notion under Company / Overview.*
