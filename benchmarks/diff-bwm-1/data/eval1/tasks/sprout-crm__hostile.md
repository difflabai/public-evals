# Operating-plan task — Sprout CRM — Seed SaaS (hostile tier)

- **Company:** Sprout CRM — Seed SaaS  (`sprout-crm`, saas)
- **Horizon:** 6 quarterly periods
- **Tier:** hostile — _Naive plans are guaranteed to fail; only careful reasoning succeeds._
- **Note:** Seed-stage SaaS. Naive quadruples marketing and over-hires; careful blends a staged marketing ramp with ~5%/q price rises.

## Goal
Grow **revenue** to at least **3.90x** its period-0 value by period 5, while satisfying every constraint below.

## Constraints (all must hold)
| id | kind | driver | limit |
|----|------|--------|-------|
| burn_ceiling | series_max | net_burn | 200622.25 |
| cash_floor | series_min | cash | 0 |
| margin_floor | series_min | gross_margin_pct | 0.8100 |
| hire_cap | binding_max | hires_per_quarter | 2 |

## Levers (one value per quarter)
| id | label | unit | default |
|----|-------|------|---------|
| marketing_spend | Marketing spend | usd/quarter | ['45000', '45000', '45000', '45000', '45000', '45000'] |
| hires_per_quarter | Net hires per quarter | people/quarter | ['1', '1', '1', '1', '1', '1'] |
| acv | Avg contract value | usd/quarter | ['600', '600', '600', '600', '600', '600'] |

## Base (do-nothing) trajectory
| driver | Q0 | Q1 | Q2 | Q3 | Q4 | Q5 |
|--------|---|---|---|---|---|---|
| revenue | 110880.00 | 147427.2000 | 181781.568000 | 214074.67392000 | 244430.1934848000 | 272964.381875712000 |
| net_burn | 173078.4000 | 164109.696000 | 156939.11424000 | 151458.7673856000 | 147567.241342464000 | 145169.20686191616000 |
| cash | 1326921.6000 | 1162811.904000 | 1005872.78976000 | 854414.0223744000 | 706846.781031936000 | 561677.57417001984000 |
| gross_margin_pct | 0.82 | 0.82 | 0.82 | 0.82 | 0.82 | 0.82 |
| headcount | 9 | 10 | 11 | 12 | 13 | 14 |

## Deliverables
Write `plan.json` (lever bindings) and `claims.json` (goal_met, constraints_satisfied, projected_final_revenue, rationale).
