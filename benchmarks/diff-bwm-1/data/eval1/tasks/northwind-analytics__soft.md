# Operating-plan task — Northwind Analytics — Series B SaaS (soft tier)

- **Company:** Northwind Analytics — Series B SaaS  (`northwind-analytics`, saas)
- **Horizon:** 6 quarterly periods
- **Tier:** soft — _Naive plans usually work._
- **Note:** Series B SaaS with an AE-capacity engine: Q1 AE hires only produce bookings from Q3 (2-quarter ramp), while payroll starts immediately — the H4 coherence trap.

## Goal
Grow **revenue** to at least **2.70x** its period-0 value by period 5, while satisfying every constraint below.

## Constraints (all must hold)
| id | kind | driver | limit |
|----|------|--------|-------|
| burn_ceiling | series_max | net_burn | 772910.00 |
| cash_floor | series_min | cash | 0 |
| margin_floor | series_min | gross_margin_pct | 0.7500 |
| hire_cap | binding_max | hires_per_quarter | 18 |

## Levers (one value per quarter)
| id | label | unit | default |
|----|-------|------|---------|
| marketing_spend | Marketing spend | usd/quarter | ['350000', '350000', '350000', '350000', '350000', '350000'] |
| hires_per_quarter | Net hires per quarter | people/quarter | ['2', '2', '2', '2', '2', '2'] |
| acv | Avg contract value | usd/quarter | ['9000', '9000', '9000', '9000', '9000', '9000'] |
| deals_per_ae | Deals per ramped AE per quarter | deals | ['3', '3', '3', '3', '3', '3'] |

## Base (do-nothing) trajectory
| driver | Q0 | Q1 | Q2 | Q3 | Q4 | Q5 |
|--------|---|---|---|---|---|---|
| revenue | 3527090.0000000000000000000000000000000000000000000 | 4041277.3000000000000000000000000000000000000000000 | 4594038.9810000000000000000000000000000000000000000 | 5184217.8115700000000000000000000000000000000000000 | 5810691.2772229000000000000000000000000000000000000 | 6472370.5389062130000000000000000000000000000000001 |
| net_burn | 178328.0000000000000000000000000000000000000000000 | -173021.8400000000000000000000000000000000000000000 | -555231.1848000000000000000000000000000000000000000 | -967374.2492560000000000000000000000000000000000000 | -1408553.0217783200000000000000000000000000000000000 | -1877896.4311249704000000000000000000000000000000001 |
| cash | 17821672.000000000000000000000000000000000000000000 | 17994693.840000000000000000000000000000000000000000 | 18549925.024800000000000000000000000000000000000000 | 19517299.274056000000000000000000000000000000000000 | 20925852.295834320000000000000000000000000000000000 | 22803748.726959290400000000000000000000000000000000 |
| gross_margin_pct | 0.8 | 0.8 | 0.8 | 0.8 | 0.8 | 0.80000000000000000000000000000000000000000000000000 |
| headcount | 80 | 82 | 84 | 86 | 88 | 90 |

## Deliverables
Write `plan.json` (lever bindings) and `claims.json` (goal_met, constraints_satisfied, projected_final_revenue, rationale).
