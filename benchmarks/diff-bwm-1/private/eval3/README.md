# private/eval3

Eval 3 runs on eval 2's coupled worlds and is scored by eval 2's scorer, so its scorer-only data (the true
world specs and the answer keys) is `private/eval2/` and is read through `dbwm.eval2`. Nothing eval-3-specific
needs hiding from agents: the public task (`data/eval3/worlds/<world>/task.json`) carries only targets the
prompt already states. This directory holds the benchmark canary (`CANARY.txt`), which must never appear in a
sandbox, prompt, transcript or deliverable.
