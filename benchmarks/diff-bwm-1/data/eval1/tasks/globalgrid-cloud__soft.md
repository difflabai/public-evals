# Operating-plan task — GlobalGrid Cloud — Public-scale SaaS (soft tier)

- **Company:** GlobalGrid Cloud — Public-scale SaaS  (`globalgrid-cloud`, saas)
- **Horizon:** 6 quarterly periods
- **Tier:** soft — _Naive plans usually work._
- **Note:** Public-scale SaaS. Efficiency, not survival: constraints bind on burn discipline at large absolute numbers.

## Goal
Grow **revenue** to at least **1.45x** its period-0 value by period 5, while satisfying every constraint below.

## Constraints (all must hold)
| id | kind | driver | limit |
|----|------|--------|-------|
| burn_ceiling | series_max | net_burn | 92103125.00 |
| cash_floor | series_min | cash | 0 |
| margin_floor | series_min | gross_margin_pct | 0.7300 |
| hire_cap | binding_max | hires_per_quarter | 450 |

## Levers (one value per quarter)
| id | label | unit | default |
|----|-------|------|---------|
| marketing_spend | Marketing spend | usd/quarter | ['30000000', '30000000', '30000000', '30000000', '30000000', '30000000'] |
| hires_per_quarter | Net hires per quarter | people/quarter | ['40', '40', '40', '40', '40', '40'] |
| acv | Avg contract value | usd/quarter | ['5000', '5000', '5000', '5000', '5000', '5000'] |

## Base (do-nothing) trajectory
| driver | Q0 | Q1 | Q2 | Q3 | Q4 | Q5 |
|--------|---|---|---|---|---|---|
| revenue | 205375000.00 | 210642500.0000 | 215804650.000000 | 220863557.00000000 | 225821285.8600000000 | 230679860.142800000000 |
| net_burn | 19207500.0000 | 16498850.000000 | 13872373.00000000 | 11326425.5400000000 | 8859397.029200000000 | 6469709.08861600000000 |
| cash | 880792500.0000 | 864293650.000000 | 850421277.00000000 | 839094851.4600000000 | 830235454.430800000000 | 823765745.34218400000000 |
| gross_margin_pct | 0.78 | 0.78 | 0.78 | 0.78 | 0.78 | 0.78 |
| headcount | 3840 | 3880 | 3920 | 3960 | 4000 | 4040 |

## Deliverables
Write `plan.json` (lever bindings) and `claims.json` (goal_met, constraints_satisfied, projected_final_revenue, rationale).
