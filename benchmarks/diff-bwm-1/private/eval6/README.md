# private/eval6: scorer-only files for eval 6

Never staged into a sandbox; read only by the scorer, the generators and the tests. The public
worlds' answer keys are derivable from the public generator (they are a dev split; see
docs/evals/eval6.md), so these files are shipped. A held-out
leaderboard must use a salted private split (`dbwm eval6-worlds make --seed-salt ...`) and a
private v1 scoring seed (`DBWM_EVAL6_V1_SCORING_SEED`).

| path | content |
|---|---|
| `worlds/<w>/ANSWER_KEY/theta.json` | true parameters of world `<w>` (byte-identical to the generator output) |
| `worlds/<w>/ANSWER_KEY/mstar.json` | grid optimum M* (plan, profit), reference plans and their profits, end-of-history latent state and customers (M* searched with 400 paths; 2026-07-16 generation) |
| `worlds/<w>/AIDED_v1/fitted_model.json` | the paper's aided fitted model; carries the true world seed and history parameters (the seed-replay leak, ledger E6-01). The scorer-side source of `AIDED_v2` (`gen/make_fitted_model.py` `strip_to_v2`), never staged |
| `certify_scan.json` | the 12-seed step-0 certification scan (normative v1 certificate; moats, tau) |
| `scoring_seeds.json` | Monte-Carlo scoring seeds: `paper` (101, the paper's seed) and `v1` |
| `CANARY.txt` | the benchmark canary string |
