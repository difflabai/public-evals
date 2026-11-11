# Operating-plan task — Atlas Advisory — Consulting (hard tier)

- **Company:** Atlas Advisory — Consulting  (`atlas-advisory`, services)
- **Horizon:** 6 quarterly periods
- **Tier:** hard — _Naive plans sometimes violate._
- **Note:** Consulting: hires are paid immediately but bill only after a 2-quarter ramp (with 4%/q attrition of the billable pool). Utilization and rate are the efficient levers.

## Goal
Grow **revenue** to at least **1.55x** its period-0 value by period 5, while satisfying every constraint below.

## Constraints (all must hold)
| id | kind | driver | limit |
|----|------|--------|-------|
| burn_ceiling | series_max | net_burn | 1052776.50 |
| cash_floor | series_min | cash | 0 |
| margin_floor | series_min | gross_margin_pct | 0.7900 |
| hire_cap | binding_max | hires_per_quarter | 7 |

## Levers (one value per quarter)
| id | label | unit | default |
|----|-------|------|---------|
| hires_per_quarter | Net hires per quarter | people/quarter | ['2', '2', '2', '2', '2', '2'] |
| utilization | Billable utilization | ratio | ['0.65', '0.65', '0.65', '0.65', '0.65', '0.65'] |
| rate_per_consultant | Realized revenue per billable consultant | usd/quarter | ['120000', '120000', '120000', '120000', '120000', '120000'] |
| bd_spend | Business development spend | usd/quarter | ['180000', '180000', '180000', '180000', '180000', '180000'] |

## Base (do-nothing) trajectory
| driver | Q0 | Q1 | Q2 | Q3 | Q4 | Q5 |
|--------|---|---|---|---|---|---|
| revenue | 2995200.0000 | 2875392.000000 | 2916376.32000000 | 2955721.2672000000 | 2993492.416512000000 | 3029752.71985152000000 |
| net_burn | 211936.000000 | 394178.56000000 | 444571.4176000000 | 496308.560896000000 | 549336.21846016000000 | 603602.7697217536000000 |
| cash | 17788064.000000 | 17393885.44000000 | 16949314.0224000000 | 16453005.461504000000 | 15903669.24304384000000 | 15300066.4733220864000000 |
| gross_margin_pct | 0.82 | 0.82 | 0.82 | 0.82 | 0.82 | 0.82 |
| headcount | 54 | 56 | 58 | 60 | 62 | 64 |

## Deliverables
Write `plan.json` (lever bindings) and `claims.json` (goal_met, constraints_satisfied, projected_final_revenue, rationale).
