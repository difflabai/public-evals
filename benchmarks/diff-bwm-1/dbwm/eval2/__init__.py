"""Eval 2: the coupled portfolio, and the shared coupled-world instrument (evals 2 and 3).

A *world* couples three unit operating models (the sprout-crm funnel with
per-unit economics) through two per-quarter shared pools: ``growth_capital``
feeds each unit's ``marketing_spend`` and ``eng_headcount`` feeds each unit's
``hires_per_quarter``. The agent must commit an allocation of both pools
(allocation.json) whose combined revenue multiple clears a target while the
combined cash floor, combined margin floor, per-unit capacity and pool caps
hold, plus a self-assessment (claims.json). Scoring is exact and judge-free.
The arms differ only in access to the true world through the ``world_query``
oracle tool and the channel it is sealed to (arms.py).

Package layout
    engine.py          exact per-unit roll-forward engine (+ the paper's decimal context)
    world.py           World (evaluate, check, goal_multiple, marginal_returns), build_brief,
                       loaders for the shipped worlds, the paper's 5 engine self-checks
    score.py           rulesets ``paper`` (score_scenario, bit for bit) and ``v1``
                       (validate_v1 + score_scenario_v1); submission reading -> ScoreResult
    world_query.py     the oracle: render(world, scenario, mode, ruleset) and the
                       sealed-mode OracleHandler handle_world_query
    arms.py            ArmSpecs, WORLD_QUERY_TOOL, contrast groups
    prompt.py          paper_prompt (paper text), build_prompt (release), common_prompt
    make_world_lib.py  generator library: templates, derive_headcount, search_optimum,
                       naive baselines, the flat-split family
    make_worlds.py     the one generator CLI (--out) and the v1 hardness re-certification
    stub_agent.py      deterministic LLM-free agent for every arm
    report.py          eval-2 contrasts over a run (core report + clustered gaps)
    selftest.py        selftest(): engine, data, careful optimum, staging, stub agent

STABLE API FOR EVAL 3 (import from ``dbwm.eval2``; do not reach into private helpers):

    WORLD_IDS                              the five shipped world ids, paper order
    load_world(world_id, interface=None)   cached World for a shipped world
    load_world_spec(world_id) -> dict      the true spec (private/; scorer/oracle side only)
    load_brief(world_id) -> dict           the agent-visible brief (data/)
    brief_path(world_id) -> Path           path of the agent-visible brief.json
    load_reference(world_id) -> dict       the answer key (private/; scorer only)
    World, build_brief, WorldSpecError
    evaluate(model, bindings)              exact engine (runs at prec 50)
    prec50                                 decorator: run a function in the paper's context
    score_scenario(world, scenario, claims, condition, rep)      paper ruleset verdict
    score_scenario_v1(world, scenario, claims_source, condition, rep)  v1 verdict
    validate_v1(world, scenario) -> (alloc, reasons)
    read_submission(dir), claims_readings(dir)
    score_submission(world, dir, ruleset, condition, rep) -> (verdict, ScoreResult kwargs)
    render(world, scenario, mode, ruleset)  world_query output for one candidate
    handle_world_query(payload, ctx)       OracleHandler (mode from ctx.mode only)
    WORLD_QUERY_TOOL                       OracleToolSpec of the oracle
    common_prompt(brief)                   condition-independent prompt body (verbatim)
    paper_prompt(brief, condition), build_prompt(brief, condition)
    canonicalize_deliverable(path)         audit duplicate gate: allocation.json by value
    ARMS, CANONICAL_ARMS, GROUNDED_CANONICAL, FIGURES_NO_VERDICT, VERDICT_NO_FIGURES
    ENGINE_SOURCES                         scorer source files hashed into verdict.engine_hash

Everything else is internal and may change.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from dbwm.core.types import (
    DEFAULT_EVAL_RULESETS,
    Cell,
    CheckResult,
    RegimeConfig,
    ScoreResult,
    StagedFile,
    StagingManifest)

from .arms import (
    ARMS,
    CANONICAL_ARMS,
    FIGURES_NO_VERDICT,
    GROUNDED_ARMS,
    GROUNDED_CANONICAL,
    READONLY_QUERY_BUDGET,
    VERDICT_NO_FIGURES,
    WORLD_QUERY_TOOL)
from .engine import evaluate, prec50
from .prompt import build_prompt, common_prompt, paper_prompt
from .score import (
    claims_readings,
    read_submission,
    score_scenario,
    score_scenario_v1,
    score_submission,
    validate_v1)
from .world import (
    World,
    WorldSpecError,
    brief_path,
    build_brief,
    load_brief,
    load_reference,
    load_world,
    load_world_spec)
from .world_query import handle_world_query, render

EVAL_ID = "eval2"
WORLD_IDS = ("orion-holdings", "vega-group", "lyra-partners", "cygnus-holdings", "perseus-portfolio")
DELIVERABLES = ("allocation.json", "claims.json")

# Names an agent has no business touching (audit (a): tool_use inputs naming
# these are flagged). The core defaults plus the eval-2 scorer-only files.
SCORER_ONLY_NAMES = (
    "reference.json", "world.json", "certificate-v1.json", "confirmatory-worlds.json",
    "sprout-crm.operating-model.json", "base_models", "make_world_lib.py", "make_worlds.py",
    "ANSWER_KEY", "checks.jsonl", "answer_key", "grader", "private")

_PKG_DIR = Path(__file__).resolve().parent
# The scorer: hashed into every verdict's engine_hash (the runner calls
# EVAL.engine_sources()). The v1 claims reading lives in dbwm/core/claims.py.
ENGINE_SOURCES = tuple(_PKG_DIR / f for f in ("engine.py", "world.py", "score.py", "world_query.py")) + (
    _PKG_DIR.parent / "core" / "claims.py",)


def engine_hash() -> str:
    """sha256 over ENGINE_SOURCES, paths relative to their common parent (as the runner computes it)."""
    import os

    from dbwm.core.verdict import engine_hash as _eh

    files = sorted(p.resolve() for p in ENGINE_SOURCES)
    return _eh(files, root=Path(os.path.commonpath([str(p.parent) for p in files])))


def _public_text(world_id: str) -> str:
    return brief_path(world_id).read_text(encoding="utf-8")


def _canon(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def _value_norm(obj: Any) -> str | None:
    a = obj.get("allocation", obj) if isinstance(obj, dict) else None
    if not isinstance(a, dict):
        return None
    out = {}
    try:
        for p, bu in sorted(a.items()):
            if not isinstance(bu, dict):
                continue
            out[p] = {u: [format(Decimal(str(x)).normalize(), "f")
                          for x in (s["values"] if isinstance(s, dict) else s)]
                      for u, s in sorted(bu.items())}
    except Exception:  # noqa: BLE001
        return None
    return _canon(out)


def allocation_value_form(obj: Any) -> str | None:
    """Value-normalized form of an allocation (wrapped or bare), or None if it is not one.

    Numbers compare by value whatever their JSON type or spelling (140000, 140000.0, "140000",
    "1.4e5" are equal), so re-typing a reference plan cannot hide a copy from the duplicate gate.
    """
    h = _value_norm(obj)
    return None if h is None or h == _canon({}) else h


def canonicalize_deliverable(path: Path | str) -> str | None:
    """``EVAL.canonicalize_deliverable`` for evals 2 and 3: allocation.json files by value."""
    p = Path(path)
    if p.name != "allocation.json":
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return allocation_value_form(obj)


def duplicate_corpus(world_id: str) -> list[str]:
    """Hashes for the audit's duplicate gate: the careful-optimum plan (the answer key) in its
    stored form, wrapped and bare, and by value for the eval canonical form."""
    from dbwm.core.audit import eval_canonical_digest

    co = load_reference(world_id)["careful_optimum"]["allocation"]
    out = [_canon(form) for form in (co, {"allocation": co})]
    # The careful optimum is not a file, so its value form is listed as a digest.
    vf = allocation_value_form(co)
    if vf:
        out.append(eval_canonical_digest(vf))
    return out


class Eval2:
    """The ``EVAL`` object (satisfies :class:`dbwm.core.types.EvalDef`)."""

    eval_id = EVAL_ID
    title = "Coupled portfolio: allocate shared pools across three coupled business units"
    arms = ARMS
    deliverables = DELIVERABLES
    SCORER_ONLY_NAMES = SCORER_ONLY_NAMES
    ENGINE_SOURCES = ENGINE_SOURCES

    # -- design ---------------------------------------------------------------
    def instances(self) -> list[str]:
        return list(WORLD_IDS)

    def default_regime(self) -> dict[str, Any]:
        return json.loads((_PKG_DIR / "regime.json").read_text(encoding="utf-8"))

    def cells(self, regime: RegimeConfig) -> list[Cell]:
        instances = list(regime.instances) if regime.instances else self.instances()
        unknown_w = [w for w in instances if w not in WORLD_IDS]
        if unknown_w:
            raise ValueError(f"unknown eval2 instance(s) {unknown_w}; known: {list(WORLD_IDS)}")
        unknown_a = [a for a in regime.arms if a not in ARMS]
        if unknown_a:
            raise ValueError(f"unknown eval2 arm(s) {unknown_a}; known: {list(ARMS)}")
        if regime.reps < 1:
            raise ValueError("reps must be >= 1")
        return [Cell(EVAL_ID, w, a, r)
                for r in range(regime.reps) for w in instances for a in regime.arms]

    def _check_cell(self, cell: Cell) -> None:
        if cell.instance not in WORLD_IDS:
            raise ValueError(f"unknown eval2 instance {cell.instance!r}")
        if cell.arm not in ARMS:
            raise ValueError(f"unknown eval2 arm {cell.arm!r}")

    # -- what the agent sees --------------------------------------------------
    def stage(self, cell: Cell) -> StagingManifest:
        """brief.json + TASK.md (the prompt). Nothing else: no world spec, no answer
        key, no harness code, no meta.json naming the condition."""
        self._check_cell(cell)
        return StagingManifest(
            files=[StagedFile(dst="brief.json", src=brief_path(cell.instance))],
            task_md=self.prompt(cell),
            writable_dirs=[])

    def prompt(self, cell: Cell) -> str:
        self._check_cell(cell)
        return build_prompt(load_brief(cell.instance), cell.arm)

    def oracle_handlers(self, cell: Cell) -> dict[str, Callable]:
        self._check_cell(cell)
        if ARMS[cell.arm].oracle_mode is None:
            return {}
        return {WORLD_QUERY_TOOL.name: handle_world_query}

    # -- scoring --------------------------------------------------------------
    def score(self, deliverables_dir: Path, cell: Cell, ruleset: str) -> ScoreResult:
        self._check_cell(cell)
        if ruleset not in DEFAULT_EVAL_RULESETS:  # paper and v1; no "as-run" (core RULESETS, eval 5 only)
            raise ValueError(f"unknown ruleset {ruleset!r}")
        world = load_world(cell.instance)
        _verdict, kw = score_submission(world, Path(deliverables_dir), ruleset,
                                        condition=cell.arm, rep=cell.rep)
        return ScoreResult(**kw)

    def answer_key_values(self, cell: Cell) -> list[str]:
        """Strings only the answer key holds (reference.json), >= 8 characters.

        The careful optimum's multiple and parent trajectory, the naive
        baselines' multiples and the hardness-floor multiples, minus anything
        that also appears in the agent-visible brief or prompt. Allocation
        values are NOT listed: template values such as "140000.0" are numbers
        an honest agent can write; exact copies of the careful-optimum plan
        are caught by the duplicate gate instead (:meth:`duplicate_corpus`).
        """
        self._check_cell(cell)
        ref = load_reference(cell.instance)
        vals: set[str] = set()
        co = ref.get("careful_optimum", {})
        if co.get("multiple"):
            vals.add(str(co["multiple"]))
        for series in (co.get("parent_trajectory") or {}).values():
            vals.update(str(x) for x in series)
        for nb in (ref.get("naive_baselines") or {}).values():
            if nb.get("multiple"):
                vals.add(str(nb["multiple"]))
        hf = ref.get("hardness_floor") or {}
        for k in ("best_feasible_naive_multiple", "min_feasible_multiple", "careful_optimum_multiple"):
            if hf.get(k):
                vals.add(str(hf[k]))
        public = _public_text(cell.instance) + "\n" + self.prompt(cell)
        return sorted(v for v in vals if len(v) >= 8 and v not in public)

    def duplicate_corpus(self, cell: Cell) -> list[str]:
        """Canonical hashes a deliverable must not equal (audit (c)): the
        careful-optimum plan."""
        self._check_cell(cell)
        return duplicate_corpus(cell.instance)

    def canonicalize_deliverable(self, path: Path) -> str | None:
        """Audit duplicate gate: allocation.json compared by value, so numbers
        re-typed as strings (or 140000 vs 140000.0) still match the duplicate corpus."""
        return canonicalize_deliverable(path)

    # -- agents, reports, checks ----------------------------------------------
    def stub_agent(self, cell: Cell, sandbox: Path, call_oracle: Callable[[str, dict], Any]) -> None:
        self._check_cell(cell)
        from .stub_agent import stub_agent

        stub_agent(cell, Path(sandbox), call_oracle)

    def report(self, run_dir: Path) -> dict[str, Any]:
        from .report import build_eval2_report

        return build_eval2_report(Path(run_dir))

    def selftest(self) -> list[CheckResult]:
        from .selftest import selftest

        return selftest()

    def engine_sources(self) -> list[Path]:
        return list(ENGINE_SOURCES)

    def engine_hash(self) -> str:
        return engine_hash()


EVAL = Eval2()


__all__ = [
    "EVAL", "EVAL_ID", "WORLD_IDS", "DELIVERABLES", "SCORER_ONLY_NAMES", "ENGINE_SOURCES", "engine_hash",
    "ARMS", "CANONICAL_ARMS", "GROUNDED_ARMS", "GROUNDED_CANONICAL", "FIGURES_NO_VERDICT",
    "VERDICT_NO_FIGURES", "READONLY_QUERY_BUDGET", "WORLD_QUERY_TOOL",
    "World", "WorldSpecError", "build_brief", "load_world", "load_world_spec", "load_brief",
    "brief_path", "load_reference", "evaluate", "prec50",
    "score_scenario", "score_scenario_v1", "validate_v1", "read_submission", "claims_readings",
    "score_submission", "render", "handle_world_query",
    "common_prompt", "paper_prompt", "build_prompt", "canonicalize_deliverable", "duplicate_corpus",
]
