# Eval 5 grading material (scorer / oracle only)

Never staged into a sandbox. The harness, the oracle server and the scorer read it;
agents never do. `CANARY.txt` carries the benchmark canary; if you find it in a
training corpus, this directory leaked.

```
CANARY.txt                         benchmark canary string
as-run-pins.json                   the v1.0 text edits, the as-run auto-spec hashes, the lab
                                   registry hash
probes.json                        57 golden probes: minimal submissions + the correct grade
                                   under each ruleset
scenarios/<id>/
  registry_v0.1.jsonl              ruleset "as-run": the registry as run (auto specs unchanged;
                                   hygiene renames in free-text fields only); ruleset "paper"
                                   reads it for the three scenarios without a fixed file
  registry_v0.1-fixed.jsonl        ruleset "paper", solvara-market-access only: v0.1 with the
                                   lab's 2026-10-03 SOLVARA-A-13 line (the oracle of record)
  registry_v1.0.jsonl              ruleset "v1": the release registry (fixes in v1_change fields)
  answer_key/blueprint.json        world bible (ground truth, cast, file plan)
  answer_key/anchors.json          source-anchored figures + acceptance bands
  answer_key/contradictions.json   planted contradictions + authoritative resolutions
```

Publishing these files makes the four scenarios a contaminated development set:
held-out evaluation needs new scenarios. License: CC-BY-4.0.
