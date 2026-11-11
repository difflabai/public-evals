# Operating-plan task — VoltCore — Hardware Devices (hostile tier)

- **Company:** VoltCore — Hardware Devices  (`voltcore-devices`, hardware)
- **Horizon:** 6 quarterly periods
- **Tier:** hostile — _Naive plans are guaranteed to fail; only careful reasoning succeeds._
- **Note:** Hardware: thin device margins + consumables attach. Naive cuts price to chase volume (margin floor breach) while quadrupling demand spend (burn breach).

## Goal
Grow **revenue** to at least **1.20x** its period-0 value by period 5, while satisfying every constraint below.

## Constraints (all must hold)
| id | kind | driver | limit |
|----|------|--------|-------|
| burn_ceiling | series_max | net_burn | 3104595.63 |
| cash_floor | series_min | cash | 0 |
| margin_floor | series_min | gross_margin_pct | 0.3901 |
| hire_cap | binding_max | hires_per_quarter | 3 |

## Levers (one value per quarter)
| id | label | unit | default |
|----|-------|------|---------|
| marketing_spend | Demand-gen spend | usd/quarter | ['900000', '900000', '900000', '900000', '900000', '900000'] |
| hires_per_quarter | Net hires per quarter | people/quarter | ['2', '2', '2', '2', '2', '2'] |
| asp | Avg selling price per unit | usd | ['1800', '1800', '1800', '1800', '1800', '1800'] |
| consumable_per_unit | Consumables revenue per installed unit | usd/quarter | ['90', '90', '90', '90', '90', '90'] |
| capex | Tooling capex | usd/quarter | ['250000', '250000', '900000', '250000', '250000', '250000'] |

## Base (do-nothing) trajectory
| driver | Q0 | Q1 | Q2 | Q3 | Q4 | Q5 |
|--------|---|---|---|---|---|---|
| revenue | 2721960.00 | 2813101.2000 | 2901508.164000 | 2987262.91908000 | 3070445.0315076000 | 3151131.680562372000 |
| net_burn | 2354922.0000 | 2358794.340000 | 3014170.50980000 | 2371005.3945060000 | 2379255.232670820000 | 2388877.57569069540000 |
| cash | 27645078.0000 | 25286283.660000 | 22272113.15020000 | 19901107.7556940000 | 17521852.523023180000 | 15132974.94733248460000 |
| gross_margin_pct | 0.40010801040426751311554908962659260238945465767315 | 0.40496433615683644797421436527061308707983914691729 | 0.40938347337353898963559835084441278863139535181401 | 0.41342012368778926020772423988414996986385717005276 | 0.41712023963520673311841128422699140388108096483619 | 0.42052270696374529300514176928401558037213144645068 |
| headcount | 72 | 74 | 76 | 78 | 80 | 82 |

## Deliverables
Write `plan.json` (lever bindings) and `claims.json` (goal_met, constraints_satisfied, projected_final_revenue, rationale).
