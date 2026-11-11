# Historical pre-registrations

The design documents the evals were run against, as they stood before the paper's runs, ported from the lab's
internal harness for the record. They are **historical**: their status lines, budgets, tooling and "how to run"
sections describe that harness, not diff-bwm-1. Each file reproduces the original verbatim except for redactions of
private system internals and private repository references (marked "[redacted: private system internals]") and of
the results and schedule of earlier runs (marked "[redacted: earlier runs]"), harness paths shortened to the
snapshot-relative form `scripts/...` used in `docs/instrument-changes/`, and links renamed to the files here. Each
ends with a **package appendix**: how this package differs from the design.

| file | eval | what it is |
|---|---|---|
| [eval1-original-plan.md](eval1-original-plan.md) | 1 | the first evaluation plan (draft v0.1), superseded before any run |
| [eval1-preregistration.md](eval1-preregistration.md) | 1 | the eval-1 pre-registration (v1.0): conditions, metrics, hypotheses and decision rule |
| [eval2-preregistration.md](eval2-preregistration.md) | 2 | the coupled-world design and pre-registered hypotheses |
| [eval4-preregistration.md](eval4-preregistration.md) | 4 | the AA-Briefcase-Lite transfer design and pre-registration |
| [eval5-preregistration.md](eval5-preregistration.md) | 5 | the consulting-oracle design and pre-registration |
| [eval5-elo-twin.md](eval5-elo-twin.md) | 5 | the ELO-scored companion design (registry-independent quality read) |

Not included:

- **Eval 3.** Its pre-registration is a working document in the lab's research repository
  (`eval3-generative-vs-grounded.md`, sections 1-9), which `docs/evals/eval3.md` cites by section. It was not part of
  the harness snapshot ported here.
- **Eval 6.** Its D1/D2 design and kill rules were written in a private specification that is not part of this
  package. The D3 kill line (KILL-1, agreement on at least 70% of cells) is fixed in its scorer and is not
  pre-registered.
  `docs/evals/eval6.md` documents the world certification and the D3 port.

The redaction script is not shipped; the originals are not public. If you find a statement in these files that
contradicts the package, the package's eval cards and `docs/instrument-changes/` describe what actually runs.
