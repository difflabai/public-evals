# Operating-plan task — HarborList — Goods Marketplace (hard tier)

- **Company:** HarborList — Goods Marketplace  (`harborlist-marketplace`, marketplace)
- **Horizon:** 6 quarterly periods
- **Tier:** hard — _Naive plans sometimes violate._
- **Note:** Goods marketplace with per-vintage cohorts (10%/q vintage decay vs 8%/q for the legacy base). Take-rate creep is the margin-friendly lever naive plans ignore.

## Goal
Grow **revenue** to at least **3.20x** its period-0 value by period 5, while satisfying every constraint below.

## Constraints (all must hold)
| id | kind | driver | limit |
|----|------|--------|-------|
| burn_ceiling | series_max | net_burn | 3010679.47 |
| cash_floor | series_min | cash | 0 |
| margin_floor | series_min | gross_margin_pct | 0.7617 |
| hire_cap | binding_max | hires_per_quarter | 6 |

## Levers (one value per quarter)
| id | label | unit | default |
|----|-------|------|---------|
| marketing_spend | Buyer acquisition spend | usd/quarter | ['600000', '600000', '600000', '600000', '600000', '600000'] |
| hires_per_quarter | Net hires per quarter | people/quarter | ['2', '2', '2', '2', '2', '2'] |
| take_rate | Marketplace take rate | ratio | ['0.12', '0.12', '0.12', '0.12', '0.12', '0.12'] |

## Base (do-nothing) trajectory
| driver | Q0 | Q1 | Q2 | Q3 | Q4 | Q5 |
|--------|---|---|---|---|---|---|
| revenue | 871200.0000 | 1060224.000000 | 1229374.08000000 | 1380715.3536000000 | 1516100.205312000000 | 1637190.06088704000000 |
| net_burn | 1922300.00000 | 1824656.0000000 | 1742745.520000000 | 1674933.67840000000 | 1619754.0041280000000 | 1575891.201797760000000 |
| cash | 12077700.00000 | 10253044.0000000 | 8510298.480000000 | 6835364.80160000000 | 5215610.7974720000000 | 3639719.595674240000000 |
| gross_margin_pct | 0.79166666666666666666666666666666666666666666666667 | 0.79166666666666666666666666666666666666666666666667 | 0.79166666666666666666666666666666666666666666666667 | 0.79166666666666666666666666666666666666666666666667 | 0.79166666666666666666666666666666666666666666666667 | 0.79166666666666666666666666666666666666666666666667 |
| headcount | 62 | 64 | 66 | 68 | 70 | 72 |

## Deliverables
Write `plan.json` (lever bindings) and `claims.json` (goal_met, constraints_satisfied, projected_final_revenue, rationale).
