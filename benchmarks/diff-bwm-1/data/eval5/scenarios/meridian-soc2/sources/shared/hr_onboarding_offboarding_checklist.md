# Onboarding & Offboarding Checklist

**Owner:** Rosa Delgado, People Ops
**Last updated:** 2025-07-19
**Applies to:** all Meridian Health Loop employees and contractors

This checklist is the People Ops procedure for bringing workforce members on board and (eventually — see below) off board. Hiring managers: the onboarding section is live and every item is required. Please don't improvise; if a step doesn't fit your new hire's situation, ask People Ops rather than skipping it.

---

## Onboarding

**Owner: People Ops (Rosa Delgado), with IT and hiring manager tasks as noted.**

### Before day one

- [ ] Signed offer letter and confidentiality/IP agreement returned (People Ops)
- [ ] Background check initiated through Checkr and cleared before start date; clinical-operations hires also complete OIG/SAM exclusion screening (People Ops)
- [ ] Equipment ordered: corporate laptop for roles handling PHI or production systems — Engineering, Clinical Ops, and G&A by default. Kandji enrollment happens at first boot; do not hand over a laptop that hasn't gone through IT imaging. (IT)
- [ ] Okta account created; group assignments per the role template — new hires get the baseline app set only, anything beyond baseline needs manager approval in the ticket (IT)
- [ ] Gusto profile, payroll, and benefits enrollment scheduled (People Ops)
- [ ] Desk/remote-setup stipend processed for remote staff (People Ops)

### First week

- [ ] Day-one orientation: company overview, org chart, communication norms (People Ops)
- [ ] HIPAA and security awareness training assigned in the LMS on day one. All new hires complete security awareness training within 30 days of start; every employee completes an annual refresher. Managers get a reminder at day 21 if their new hire hasn't finished. (People Ops)
- [ ] Policy acknowledgments signed in the LMS: Acceptable Use (POL-01), Information Security (POL-02) (People Ops)
- [ ] Role-specific system access requested by the hiring manager via IT ticket — production access additionally requires VP Engineering approval (Hiring manager)
- [ ] 30/60/90 plan reviewed with manager (Hiring manager)

### First month

- [ ] Security awareness training completion verified in the LMS tracker (People Ops)
- [ ] Benefits enrollment window closed out (People Ops)
- [ ] New-hire check-in survey sent at day 30 (People Ops)

---

## Offboarding (DRAFT — not yet in use)

**Owner:** _______________

> *Status note (Rosa): this section is a draft from our 2025 process-documentation push and has not been rolled out. In practice we still handle departures ad hoc — IT gets an email, sometimes. I want a named owner and an agreed IT turnaround before we call this a real procedure; until then, treat the steps below as a proposal, not a record of what happens.*

### On notice of departure

- [ ] People Ops confirms last day with the manager and opens a deprovisioning ticket with IT listing the departing employee's systems
- [ ] Manager identifies work/knowledge transfer items

### On or after the last day

- [ ] IT actions the deprovisioning ticket: disable Okta, revoke AWS/GitHub/production access, disable VPN account, reclaim or remote-lock the laptop
- [ ] People Ops collects badge and any peripherals; confirms mail forwarding/auto-reply per manager request
- [ ] Payroll finalized in Gusto; COBRA/benefits paperwork issued (People Ops)
- [ ] People Ops marks the roster record terminated with the termination date

### Open questions before rollout

- Who owns confirming the deprovisioning ticket was actually completed? (Currently: nobody. The ticket gets opened; there is no step where anyone verifies closure.)
- What is the required turnaround on the deprovisioning ticket? The draft doesn't set one — IT has asked that we agree an SLA before this becomes policy.
- Systems with local accounts outside Okta (e.g., the legacy VPN) need their own line item — disabling Okta does not touch them.
- Contractor offboarding is not covered at all in this draft.

---

## Revision history

| Date | Author | Change |
|------|--------|--------|
| 2024-02-06 | R. Delgado | Initial onboarding checklist |
| 2024-09-30 | R. Delgado | Added background-check and OIG screening steps |
| 2025-07-19 | R. Delgado | Added offboarding DRAFT section for future rollout; updated Kandji enrollment language |

*Feedback to Rosa in #people-ops. Please do not copy this into team wikis — link to it, so nobody follows a stale version.*
