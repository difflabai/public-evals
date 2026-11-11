"""Eval 6 D3: the dispersion pilot (gate only; self-supply only; not pooled with D1).

The paper's D3 (v7 Section 8 "D3 dispersion pilot", Appendix E, Appendix A entry 23)
asked whether five independent self-supply sessions on byte-identical inputs
disagree: each Fable 5 session saw one eval-6 world's public history, a brief that
pins the profit accounting (rounded gm / fixed per segment) and names a probe /
partner segment pair, and reported six figures (two structural exponents, a
spillover, three forecasts of a pinned plan) plus its own plan. A world x figure
cell "agrees" when the five reports lie within a tolerance; KILL-1 fires at >= 70%
of cells.

This package holds two levels (plan D-4):

* offline (``brief``, ``score``): the pilot's brief and scorer ported, with the
  scorer's reference defect kept under ``paper`` and corrected under ``v1``
  (engine-consistent references, v7 E.7).
* runnable, non-canonical (this module's ``EVAL``, ``dbwm run --eval
  dbwm.eval6.d3``): one arm, ``self-supply``, staged exactly like eval 6's
  unaided arm (public/history.csv, public/task.json) with TASK.md = the v1 D3
  brief; bash/python in a container; no oracle, no fitted model. Per attempt the
  six figures are read as strict numbers (missing -> ``missing_figures``) and
  compared with the references, confidence and ``first_action`` are recorded,
  and the session's own plan is scored with the eval-6 D1 scorer (v7 G.2 item 2).
  The gate is a group statistic, computed by ``EVAL.report`` over the first
  scored attempt per cell; ``ScoreResult.success`` is None (no per-attempt
  success exists). It has never run live: every regime number is provisional.

Not in eval 6's SCORER_SOURCES (eval 6 D1's engine hash does not move); this EVAL
hashes its own scorer files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from dbwm.core.types import (CANARY_GUID, RULESET_PAPER, RULESET_V1, ArmSpec, Cell, CheckResult, Isolation,
                             RegimeConfig, ScoreResult, StagingManifest, ToolPolicy)

from .. import SCORER_ONLY_NAMES as _E6_SCORER_ONLY_NAMES
from .. import _paths
from ..arms import D1_BUILTINS, list_instances, manifest
from . import brief as B

EVAL_ID = "eval6-d3"
LABEL = "pilot, gate only; self-supply only; not pooled with D1"
SCREENED_WORLDS = ("w1", "w2", "w3", "w4", "w5", "w6", "w7", "w9")
DELIVERABLES = ("plan.json", "claims.json")
# Scorer-only names: eval 6's plus the pilot's scorer and its reference outputs.
SCORER_ONLY_NAMES = tuple(_E6_SCORER_ONLY_NAMES) + (
    "d3_score.py", "d3_planner.py", "engine_consistent_truths_with_spill.json", "d3_score_budget8.json")
# Files whose bytes define this scorer (hashed into every verdict as engine_hash).
SCORER_SOURCES = ("d3/score.py", "d3/brief.py", "latent_engine.py", "score_d1.py", "_paths.py")

SELF_SUPPLY = ArmSpec(
    name="self-supply",
    description=("D3 self-supply: the world's public history.csv and task.json plus the D3 brief (rounded gm and "
                 "fixed per segment, the probe / partner pair, a pinned plan); bash/python; no model, no oracle."),
    tools=ToolPolicy(builtin=D1_BUILTINS),
    oracle_mode=None,
    query_budget=None,
    min_isolation=Isolation.CONTAINER,
    canonical=False,
)
ARMS = {SELF_SUPPLY.name: SELF_SUPPLY}


def theta(world_id: str) -> dict:
    return json.loads(_paths.answer_key(world_id, "theta.json").read_text())


class EvalD3:
    eval_id = EVAL_ID
    eval_module = "dbwm.eval6.d3"
    title = "Eval 6 D3: self-supply dispersion gate (pilot, gate only)"
    arms = ARMS
    deliverables = DELIVERABLES
    rulesets = (RULESET_PAPER, RULESET_V1)
    SCORER_ONLY_NAMES = SCORER_ONLY_NAMES

    # -- design ----------------------------------------------------------------------

    def instances(self) -> list[str]:
        """The 11 certified eval-6 worlds (the design uses w1-w7 and w9: the regime default)."""
        return list_instances()

    def default_regime(self) -> dict[str, Any]:
        from dbwm.core.regime import read_regime_file
        return read_regime_file(Path(__file__).with_name("regime.json"))

    def cells(self, regime: RegimeConfig) -> list[Cell]:
        known = self.instances()
        insts = list(regime.instances) if regime.instances else list(SCREENED_WORLDS)
        arms = list(regime.arms) if regime.arms else list(ARMS)
        for i in insts:
            if i not in known:
                raise ValueError(f"{EVAL_ID}: unknown instance {i!r}; choose from {known}")
        for a in arms:
            if a not in ARMS:
                raise ValueError(f"{EVAL_ID}: unknown arm {a!r}; choose from {list(ARMS)}")
        return [Cell(self.eval_id, i, a, r) for r in range(regime.reps) for i in insts for a in arms]

    def _check_cell(self, cell: Cell) -> None:
        if cell.arm not in ARMS or not _paths.world_public(cell.instance, "task.json").is_file():
            raise ValueError(f"{EVAL_ID}: bad cell {cell}")

    def engine_sources(self) -> list[Path]:
        here = Path(__file__).resolve().parent.parent
        return [here / n for n in SCORER_SOURCES] + [here.parent / "core" / "claims.py"]

    # -- sandbox ---------------------------------------------------------------------

    def stage(self, cell: Cell) -> StagingManifest:
        """Eval 6's unaided staging (public/history.csv, public/task.json); TASK.md = the v1 D3 brief."""
        self._check_cell(cell)
        return manifest(cell.instance, "unaided", self.prompt(cell))

    def prompt(self, cell: Cell) -> str:
        """The pilot passed the brief as the first prompt (-p); so does this (v1 brief)."""
        self._check_cell(cell)
        return B.task_md(theta(cell.instance), B.BRIEF_V1)

    def disclosed_values_for(self, cell: Cell) -> list[str]:
        """Rounded gm (4 dp), fixed (2 dp) and reference marketing (2 dp), and the probe / partner names:
        disclosed on purpose (v7 E.5), so the core pre-check and audit never count them as leaks."""
        self._check_cell(cell)
        return B.disclosed_values(theta(cell.instance))

    def oracle_handlers(self, cell: Cell) -> dict:
        self._check_cell(cell)
        return {}

    # -- scoring ---------------------------------------------------------------------

    def score(self, deliverables_dir: Path, cell: Cell, ruleset: str) -> ScoreResult:
        from .score import score_attempt
        self._check_cell(cell)
        if ruleset not in self.rulesets:
            raise ValueError(f"{EVAL_ID}: unknown ruleset {ruleset!r}")
        d = score_attempt(Path(deliverables_dir), cell.instance, ruleset)
        present = any((Path(deliverables_dir) / n).is_file() for n in DELIVERABLES)
        return ScoreResult(deliverable_present=present, success=None, claimed_success=None,
                           claimed_success_paper=None, claims_parse_status=d["claims_parse_status"],
                           overclaim=None, ruleset=ruleset, details=d)

    def answer_key_values(self, cell: Cell) -> list[str]:
        from ..score_d1 import answer_key_values
        self._check_cell(cell)
        return answer_key_values(cell.instance, disclosed=self.disclosed_values_for(cell))

    def stub_agent(self, cell: Cell, sandbox: Path, call_oracle: Callable[[str, dict], Any]) -> None:
        from .stub_agent import stub_agent
        self._check_cell(cell)
        stub_agent(cell, Path(sandbox), call_oracle)

    def report(self, run_dir: Path) -> dict[str, Any]:
        from .report import report
        return report(run_dir)

    def report_markdown(self, rep: dict[str, Any]) -> str:
        from .report import render_markdown
        return render_markdown(rep)

    def selftest(self) -> list[CheckResult]:
        return selftest(self)


EVAL = EvalD3()


def selftest(ev: EvalD3 = EVAL) -> list[CheckResult]:
    """Fast offline checks: briefs and staging leak-free, the probe-pair rule, the stub scored."""
    import tempfile

    out: list[CheckResult] = []
    try:
        import numpy  # noqa: F401
    except ImportError:
        return [CheckResult("numpy", False, "numpy missing: install the eval6 extra; D3 checks skipped",
                            skipped=True)]
    leaks, n = [], 0
    for w in ev.instances():
        cell = Cell(ev.eval_id, w, "self-supply", 0)
        m = ev.stage(cell)
        vals = ev.answer_key_values(cell)
        for dst, b in [(f.dst, f.src.read_bytes() if f.src else f.content) for f in m.files] + \
                [("TASK.md", m.task_md.encode())]:
            t = b.decode("utf-8", errors="replace")
            n += 1
            if CANARY_GUID in t or any(v in t for v in vals) or "ANSWER_KEY" in t or "private/" in t:
                leaks.append(f"{w}:{dst}")
    out.append(CheckResult("staging-leaks", not leaks,
                           f"{len(ev.instances())} worlds, {n} staged texts (D3 v1 brief + public files): no canary, "
                           f"full-precision answer-key value or private path" + (f"; leaks {leaks[:4]}" if leaks else "")))
    from .score import references
    from .stub_agent import stub_agent
    with tempfile.TemporaryDirectory(prefix="dbwm_d3self_") as td:
        cell = Cell(ev.eval_id, "w1", "self-supply", 0)
        sb = Path(td)
        m = ev.stage(cell)
        for f in m.files:
            p = sb / f.dst
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(f.src.read_bytes() if f.src else f.content)
        (sb / "TASK.md").write_text(m.task_md)
        stub_agent(cell, sb)
        r = ev.score(sb, cell, RULESET_V1)
        ref = references("w1")
        ok = (r.details["complete_report"] and r.success is None and r.details["plan"]["no_plan"] is False
              and r.details["references"] == ref)
        out.append(CheckResult("stub-scored", ok, "the stub's six figures and plan score under v1 against the "
                               "engine-consistent references (whose walk equals held_out_profit: the E.7 check)"))
    return out
