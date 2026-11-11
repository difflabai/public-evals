# Operating-plan task — MeshVerse — Platform Ecosystem (hard tier)

- **Company:** MeshVerse — Platform Ecosystem  (`meshverse-platform`, ecosystem)
- **Horizon:** 6 quarterly periods
- **Tier:** hard — _Naive plans sometimes violate._
- **Note:** Ecosystem play: paid + viral developer growth compounds into app GMV; rev-share creep is high-margin but rate-limited. Enterprise reps ramp 2 quarters before selling seats.

## Goal
Grow **revenue** to at least **2.90x** its period-0 value by period 5, while satisfying every constraint below.

## Constraints (all must hold)
| id | kind | driver | limit |
|----|------|--------|-------|
| burn_ceiling | series_max | net_burn | 441363.87 |
| cash_floor | series_min | cash | 0 |
| margin_floor | series_min | gross_margin_pct | 0.8738 |
| hire_cap | binding_max | hires_per_quarter | 7 |

## Levers (one value per quarter)
| id | label | unit | default |
|----|-------|------|---------|
| devrel_spend | Developer-relations spend | usd/quarter | ['400000', '400000', '400000', '400000', '400000', '400000'] |
| hires_per_quarter | Net hires per quarter | people/quarter | ['2', '2', '2', '2', '2', '2'] |
| rev_share | Platform revenue share | ratio | ['0.15', '0.15', '0.15', '0.15', '0.15', '0.15'] |
| seat_price | Enterprise seat price | usd/quarter | ['1500', '1500', '1500', '1500', '1500', '1500'] |

## Base (do-nothing) trajectory
| driver | Q0 | Q1 | Q2 | Q3 | Q4 | Q5 |
|--------|---|---|---|---|---|---|
| revenue | 3463680.00000 | 4064769.6000000 | 4722826.512000000 | 5436141.71664000000 | 6203057.4651408000000 | 7021965.741186576000000 |
| net_burn | 238744.00000 | -255508.3200000 | -808073.070400000 | -1417200.87828800000 | -2081194.8519393600000 | -2798409.006381179200000 |
| cash | 21761256.00000 | 22016764.3200000 | 22824837.390400000 | 24242038.26868800000 | 26323233.1206273600000 | 29121642.127008539200000 |
| gross_margin_pct | 0.91182095343680709534368070953436807095343680709534 | 0.91382997944090115218338574466803727325652110761702 | 0.91662758718756002443648516555968719437052232758365 | 0.91980694009179571549294222668946273933354975229761 | 0.92312458559649730060142416882725420262233901588896 | 0.92643701865766882368031290877142744788749634437770 |
| headcount | 87 | 89 | 91 | 93 | 95 | 97 |

## Deliverables
Write `plan.json` (lever bindings) and `claims.json` (goal_met, constraints_satisfied, projected_final_revenue, rationale).
