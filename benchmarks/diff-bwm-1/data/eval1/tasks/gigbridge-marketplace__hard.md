# Operating-plan task — GigBridge — Labor Marketplace (hard tier)

- **Company:** GigBridge — Labor Marketplace  (`gigbridge-marketplace`, marketplace)
- **Horizon:** 6 quarterly periods
- **Tier:** hard — _Naive plans sometimes violate._
- **Note:** Labor marketplace. Fill-rate (ops quality) and take-rate are the efficient levers; naive plans buy demand they can't fill.

## Goal
Grow **revenue** to at least **4.25x** its period-0 value by period 5, while satisfying every constraint below.

## Constraints (all must hold)
| id | kind | driver | limit |
|----|------|--------|-------|
| burn_ceiling | series_max | net_burn | 924085.13 |
| cash_floor | series_min | cash | 0 |
| margin_floor | series_min | gross_margin_pct | 0.8658 |
| hire_cap | binding_max | hires_per_quarter | 5 |

## Levers (one value per quarter)
| id | label | unit | default |
|----|-------|------|---------|
| marketing_spend | Employer acquisition spend | usd/quarter | ['350000', '350000', '350000', '350000', '350000', '350000'] |
| hires_per_quarter | Net hires per quarter | people/quarter | ['2', '2', '2', '2', '2', '2'] |
| fill_rate | Job fill rate | ratio | ['0.70', '0.70', '0.70', '0.70', '0.70', '0.70'] |
| take_rate | Take rate | ratio | ['0.18', '0.18', '0.18', '0.18', '0.18', '0.18'] |

## Base (do-nothing) trajectory
| driver | Q0 | Q1 | Q2 | Q3 | Q4 | Q5 |
|--------|---|---|---|---|---|---|
| revenue | 1053319.680000 | 1402947.30240000 | 1728100.9912320000 | 2030493.921845760000 | 2311719.34731655680000 | 2573258.9930043978240000 |
| net_burn | 814401.120000 | 549193.04160000 | 305909.5286880000 | 83015.861679840000 | -120915.24863774880000 | -307211.1812331063840000 |
| cash | 13185598.880000 | 12636405.83840000 | 12330496.3097120000 | 12247480.448032160000 | 12368395.69666990880000 | 12675606.8779030151840000 |
| gross_margin_pct | 0.89583333333333333333333333333333333333333333333333 | 0.89583333333333333333333333333333333333333333333333 | 0.89583333333333333333333333333333333333333333333333 | 0.89583333333333333333333333333333333333333333333333 | 0.89583333333333333333333333333333333333333333333333 | 0.89583333333333333333333333333333333333333333333333 |
| headcount | 47 | 49 | 51 | 53 | 55 | 57 |

## Deliverables
Write `plan.json` (lever bindings) and `claims.json` (goal_met, constraints_satisfied, projected_final_revenue, rationale).
