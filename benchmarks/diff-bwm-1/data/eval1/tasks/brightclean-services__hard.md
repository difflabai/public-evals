# Operating-plan task — BrightClean — Field Services (hard tier)

- **Company:** BrightClean — Field Services  (`brightclean-services`, services)
- **Horizon:** 6 quarterly periods
- **Tier:** hard — _Naive plans sometimes violate._
- **Note:** Field services. Crew hires train for 2 quarters before producing; price and route-density (jobs/crew) are the margin-safe levers.

## Goal
Grow **revenue** to at least **1.70x** its period-0 value by period 5, while satisfying every constraint below.

## Constraints (all must hold)
| id | kind | driver | limit |
|----|------|--------|-------|
| burn_ceiling | series_max | net_burn | 687775.25 |
| cash_floor | series_min | cash | 0 |
| margin_floor | series_min | gross_margin_pct | 0.1769 |
| hire_cap | binding_max | hires_per_quarter | 4 |

## Levers (one value per quarter)
| id | label | unit | default |
|----|-------|------|---------|
| hires_per_quarter | Net hires per quarter | people/quarter | ['1', '1', '1', '1', '1', '1'] |
| price_per_job | Price per job | usd | ['320', '320', '320', '320', '320', '320'] |
| jobs_per_crew | Jobs per crew per quarter | jobs | ['130', '130', '130', '130', '130', '130'] |
| marketing_spend | Local marketing | usd/quarter | ['120000', '120000', '120000', '120000', '120000', '120000'] |

## Base (do-nothing) trajectory
| driver | Q0 | Q1 | Q2 | Q3 | Q4 | Q5 |
|--------|---|---|---|---|---|---|
| revenue | 998400 | 998400 | 1040000 | 1081600 | 1123200 | 1164800 |
| net_burn | 263408.00 | 283408.00 | 294800.00 | 306192.00 | 317584.00 | 328976.00 |
| cash | 7736592.00 | 7453184.00 | 7158384.00 | 6852192.00 | 6534608.00 | 6205632.00 |
| gross_margin_pct | 0.20692307692307692307692307692307692307692307692308 | 0.20692307692307692307692307692307692307692307692308 | 0.20692307692307692307692307692307692307692307692308 | 0.20692307692307692307692307692307692307692307692308 | 0.20692307692307692307692307692307692307692307692308 | 0.20692307692307692307692307692307692307692307692308 |
| headcount | 13 | 14 | 15 | 16 | 17 | 18 |

## Deliverables
Write `plan.json` (lever bindings) and `claims.json` (goal_met, constraints_satisfied, projected_final_revenue, rationale).
