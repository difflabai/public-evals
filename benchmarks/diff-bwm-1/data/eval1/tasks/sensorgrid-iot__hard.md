# Operating-plan task — SensorGrid — IoT Hardware + SaaS (hard tier)

- **Company:** SensorGrid — IoT Hardware + SaaS  (`sensorgrid-iot`, hardware)
- **Horizon:** 6 quarterly periods
- **Tier:** hard — _Naive plans sometimes violate._
- **Note:** Hybrid hardware: the fleet's recurring subscription compounds from every unit sold (fleet roll-forward) — the coherence chain naive plans under-exploit.

## Goal
Grow **revenue** to at least **1.25x** its period-0 value by period 5, while satisfying every constraint below.

## Constraints (all must hold)
| id | kind | driver | limit |
|----|------|--------|-------|
| burn_ceiling | series_max | net_burn | 2563166.14 |
| cash_floor | series_min | cash | 0 |
| margin_floor | series_min | gross_margin_pct | 0.4726 |
| hire_cap | binding_max | hires_per_quarter | 6 |

## Levers (one value per quarter)
| id | label | unit | default |
|----|-------|------|---------|
| marketing_spend | Demand-gen spend | usd/quarter | ['500000', '500000', '500000', '500000', '500000', '500000'] |
| hires_per_quarter | Net hires per quarter | people/quarter | ['2', '2', '2', '2', '2', '2'] |
| asp | Device ASP | usd | ['950', '950', '950', '950', '950', '950'] |
| sub_price | Subscription per active device | usd/quarter | ['75', '75', '75', '75', '75', '75'] |
| capex | Production line capex | usd/quarter | ['150000', '150000', '150000', '600000', '150000', '150000'] |

## Base (do-nothing) trajectory
| driver | Q0 | Q1 | Q2 | Q3 | Q4 | Q5 |
|--------|---|---|---|---|---|---|
| revenue | 1679750.0000000000000000000000000000000000000000000 | 1732857.5000000000000000000000000000000000000000000 | 1784371.7750000000000000000000000000000000000000000 | 1834340.6217500000000000000000000000000000000000000 | 1882810.4030975000000000000000000000000000000000000 | 1929826.0910045750000000000000000000000000000000000 |
| net_burn | 1570280.0000000000000000000000000000000000000000000 | 1582961.6000000000000000000000000000000000000000000 | 1597002.7520000000000000000000000000000000000000000 | 2062362.6694400000000000000000000000000000000000000 | 1629001.7893568000000000000000000000000000000000000 | 1646881.7356760960000000000000000000000000000000000 |
| cash | 20429720.000000000000000000000000000000000000000000 | 18846758.400000000000000000000000000000000000000000 | 17249755.648000000000000000000000000000000000000000 | 15187392.978560000000000000000000000000000000000000 | 13558391.189203200000000000000000000000000000000000 | 11911509.453527104000000000000000000000000000000000 |
| gross_margin_pct | 0.57908617353772882869474624200029766334275933918739 | 0.58749112376522593461955180965543906524339133483278 | 0.59516590818076574877452317917324151801269104920694 | 0.60219858703567959623963443963893670447741639563350 | 0.60866362792443913232264373918404690024944345244606 | 0.61462443162765389994165203946751227850377121433884 |
| headcount | 57 | 59 | 61 | 63 | 65 | 67 |

## Deliverables
Write `plan.json` (lever bindings) and `claims.json` (goal_met, constraints_satisfied, projected_final_revenue, rationale).
