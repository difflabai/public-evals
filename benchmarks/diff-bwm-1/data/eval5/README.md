# Eval 5 scenarios (agent-visible half), dataset v1.0

Four open-ended consulting engagements in the shape of
[AA-Briefcase-Lite](https://huggingface.co/datasets/ArtificialAnalysis/AA-Briefcase-Lite):
each pairs a deliberately contradictory, text-only source pool (about 30 files) with
a brief that asks for a prose deliverable plus a machine-readable artifact.

| scenario | client (fictional) | deliverables |
|---|---|---|
| `solvara-market-access` | Veloxa Therapeutics (specialty pharma) | `market_access_plan.md`, `pa_workflow.yaml` |
| `meridian-soc2` | Meridian Health Loop (healthcare communications) | `soc2_readiness_report.md`, `controls_matrix.csv` |
| `castellan-software` | Castellan Grid Consulting (transmission-line engineering) | `software_needs_assessment.md`, `capability_plan.json` |
| `bluestem-demand` | Bluestem Foodservice Distributors (broadline wholesale) | `demand_growth_plan.md`, `initiative_portfolio.json` |

Every scenario also asks for `claims.json` (self-assessment and headline key figures).

**All organizations, people, products, figures, documents and events in these
scenarios are fictional.** Real vendors, standards bodies and public programs
(cloud providers, SOC 2 / AICPA Trust Services Criteria, NERC, Medicare Part D, ...)
appear only in neutral roles. Nothing here is legal, regulatory, medical or
financial guidance. Email domains use the reserved `.example` TLD and IP addresses
use the RFC 5737 documentation ranges.

## Layout

```
manifest.json                 scenario ids, deliverable names, headline key-figure names, checklist ids
scenarios/<id>/
  scenario_overview.md        in-world briefing (embedded in TASK.md)
  tasks/t1.md                 the task brief (embedded in TASK.md)
  sources/shared/**           staged into the sandbox as shared/
  sources/engagement/**       staged into the sandbox as engagement/
  CHECKLIST.md                the client review checklist (checklist arms only)
```

The grading material (registries, answer keys, golden probes) lives in
`private/eval5/` and never enters a sandbox. Changes relative to the v0.1 dataset
the paper's runs used (renamed entities, `.example` domains,
paraphrased reference sheet, two brief edits) are listed in
`docs/instrument-changes/eval5.md`; the exact substitution table and the sha256
of every original file stay with the lab.

License: CC-BY-4.0 (see LICENSE-DATA at the repository root).
