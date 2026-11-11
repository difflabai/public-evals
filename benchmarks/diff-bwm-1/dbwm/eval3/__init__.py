"""Eval 3: generative vs grounded -- separating the self-built model from the external oracle.

Same coupled worlds, same scorer and same answer keys as eval 2 (imported from
``dbwm.eval2`` through its documented API only). What varies is the *model
source*: none (``ungrounded``), self-built (the agent declares and runs its
OWN model -- ``gen-figures``, ``gen-both`` -- or writes its own code --
``gen-code``), or the true world (``grounded-iterate``). Achievement is
scored against the true world; the generative arms add self-model fidelity.
Pre-registration: numerical-models-llm-planning/eval3-generative-vs-grounded.md.

Package layout
    model_dsl.py         the paper's model_tool.py DSL (compile + exact engine + rollup + checks), verbatim
    oracle.py            the ``model_eval`` / ``model_test`` oracle ops (sealed mode), tool specs, v1 changes
    task.py              the public task.json (goal + constraints the agent is told)
    fixtures.py          the faithful 11-formula funnel (stub, selftest, goldens)
    arms.py              ArmSpecs (3 generative arms + eval 2's ungrounded/grounded-iterate), contrasts
    prompt.py            paper_prompt (paper text) and build_prompt (release)
    score.py             eval 2's scorer + declared_model / self-model fidelity fields
    stub_agent.py        deterministic LLM-free agent for every arm
    report.py            eval-3 run report: overclaim of-all and of-fails, the four contrasts
    selftest.py          selftest(): faithful self-model, tasks, prompts, staging, sealed ops, stub agent
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from dbwm.core.types import (
    DEFAULT_EVAL_RULESETS,
    Cell,
    CheckResult,
    RegimeConfig,
    ScoreResult,
    StagedFile,
    StagingManifest,
)
from dbwm.eval2 import ENGINE_SOURCES as EVAL2_ENGINE_SOURCES
from dbwm.eval2 import EVAL as EVAL2
from dbwm.eval2 import SCORER_ONLY_NAMES as EVAL2_SCORER_ONLY_NAMES
from dbwm.eval2 import WORLD_QUERY_TOOL, brief_path, canonicalize_deliverable, handle_world_query, load_brief

from .arms import ARMS, BASELINE_ARMS, CANONICAL_ARMS, CONTRASTS, DEFAULT_ARMS, DSL_ARMS, GEN_ARMS
from .oracle import MODEL_EVAL_TOOL, MODEL_TEST_TOOL, handle_model_eval, handle_model_test
from .prompt import COMMAND_TIMEOUT_S, build_prompt, paper_prompt
from .score import score_eval3
from .task import load_task, task_path

EVAL_ID = "eval3"
# The pre-registered eval-3 worlds (eval3-generative-vs-grounded.md section 9).
WORLD_IDS = ("orion-holdings", "vega-group", "lyra-partners")
# allocation.json + claims.json (scored); model.json and the agent's own code
# (the generative evidence the paper copied back, coupled_planner.py:521-529).
DELIVERABLES = ("allocation.json", "claims.json", "model.json", "*.py")
SCORER_ONLY_NAMES = tuple(EVAL2_SCORER_ONLY_NAMES)

_PKG_DIR = Path(__file__).resolve().parent
ENGINE_SOURCES = tuple(EVAL2_ENGINE_SOURCES) + tuple(
    _PKG_DIR / f for f in ("score.py", "model_dsl.py", "oracle.py", "task.py", "arms.py")
)


def engine_hash() -> str:
    """sha256 over ENGINE_SOURCES, paths relative to their common parent (as the runner computes it)."""
    from dbwm.core.verdict import engine_hash as _eh

    files = sorted(p.resolve() for p in ENGINE_SOURCES)
    return _eh(files, root=Path(os.path.commonpath([str(p.parent) for p in files])))


def _eval2_cell(cell: Cell, arm: str = "ungrounded") -> Cell:
    return Cell("eval2", cell.instance, arm, cell.rep)


class Eval3:
    """The ``EVAL`` object (satisfies :class:`dbwm.core.types.EvalDef`)."""

    eval_id = EVAL_ID
    title = "Generative vs grounded: the self-built model vs the external oracle (coupled portfolio)"
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
            raise ValueError(f"unknown eval3 instance(s) {unknown_w}; known: {list(WORLD_IDS)}")
        unknown_a = [a for a in regime.arms if a not in ARMS]
        if unknown_a:
            raise ValueError(f"unknown eval3 arm(s) {unknown_a}; known: {list(ARMS)}")
        if regime.reps < 1:
            raise ValueError("reps must be >= 1")
        return [Cell(EVAL_ID, w, a, r) for r in range(regime.reps) for w in instances for a in regime.arms]

    def _check_cell(self, cell: Cell) -> None:
        if cell.instance not in WORLD_IDS:
            raise ValueError(f"unknown eval3 instance {cell.instance!r}")
        if cell.arm not in ARMS:
            raise ValueError(f"unknown eval3 arm {cell.arm!r}")

    # -- what the agent sees --------------------------------------------------
    def stage(self, cell: Cell) -> StagingManifest:
        """brief.json (+ task.json for gen-both and gen-code) and TASK.md. Nothing else:
        no model_tool source, no engine, no world spec, no answer key, no meta.json."""
        self._check_cell(cell)
        files = [StagedFile(dst="brief.json", src=brief_path(cell.instance))]
        if cell.arm in ("gen-both", "gen-code"):
            files.append(StagedFile(dst="task.json", src=task_path(cell.instance)))
        return StagingManifest(files=files, task_md=self.prompt(cell), writable_dirs=[])

    def prompt(self, cell: Cell) -> str:
        self._check_cell(cell)
        return build_prompt(load_brief(cell.instance), cell.arm)

    def oracle_handlers(self, cell: Cell) -> dict[str, Callable]:
        self._check_cell(cell)
        if cell.arm == "gen-figures":
            return {MODEL_EVAL_TOOL.name: handle_model_eval}
        if cell.arm == "gen-both":
            return {MODEL_EVAL_TOOL.name: handle_model_eval, MODEL_TEST_TOOL.name: handle_model_test}
        if cell.arm == "grounded-iterate":
            return {WORLD_QUERY_TOOL.name: handle_world_query}
        return {}

    def command_timeout_s(self, cell: Cell) -> int | None:
        """Per-command limit the gen-code prompt states (None for arms without bash).

        The harness should enforce it (Claude Code: BASH_DEFAULT_TIMEOUT_MS =
        BASH_MAX_TIMEOUT_MS = 1000 x this; openai_compat: bash_timeout_s).
        """
        self._check_cell(cell)
        return COMMAND_TIMEOUT_S if ARMS[cell.arm].tools.needs_bash else None

    # -- scoring --------------------------------------------------------------
    def score(self, deliverables_dir: Path, cell: Cell, ruleset: str) -> ScoreResult:
        self._check_cell(cell)
        if ruleset not in DEFAULT_EVAL_RULESETS:  # paper and v1; no "as-run" (core RULESETS, eval 5 only)
            raise ValueError(f"unknown ruleset {ruleset!r}")
        _verdict, kw = score_eval3(cell.instance, Path(deliverables_dir), ruleset, cell.arm, cell.rep)
        return ScoreResult(**kw)

    def answer_key_values(self, cell: Cell) -> list[str]:
        """eval 2's answer-key strings for the world (the careful optimum's multiple and
        trajectory, the naive and hardness-floor multiples; >= 8 characters), minus anything
        in eval 3's agent-visible text (brief, task.json, prompt)."""
        self._check_cell(cell)
        vals = EVAL2.answer_key_values(_eval2_cell(cell))
        public = "\n".join(
            [
                brief_path(cell.instance).read_text(encoding="utf-8"),
                task_path(cell.instance).read_text(encoding="utf-8"),
                self.prompt(cell),
            ]
        )
        return sorted(v for v in vals if v not in public)

    def duplicate_corpus(self, cell: Cell) -> list[str]:
        """eval 2's corpus for the world: the careful optimum (audit (c))."""
        self._check_cell(cell)
        return list(EVAL2.duplicate_corpus(_eval2_cell(cell)))

    def canonicalize_deliverable(self, path: Path) -> str | None:
        """Audit duplicate gate: eval 2's value form of allocation.json (same allocation schema),
        so re-typed numbers cannot hide a copy of the careful optimum."""
        return canonicalize_deliverable(path)

    # -- agents, reports, checks ----------------------------------------------
    def stub_agent(self, cell: Cell, sandbox: Path, call_oracle: Callable[[str, dict], Any]) -> None:
        self._check_cell(cell)
        from .stub_agent import stub_agent

        stub_agent(cell, Path(sandbox), call_oracle)

    def report(self, run_dir: Path) -> dict[str, Any]:
        from .report import build_eval3_report

        return build_eval3_report(Path(run_dir))

    def selftest(self) -> list[CheckResult]:
        from .selftest import selftest

        return selftest()

    def engine_sources(self) -> list[Path]:
        return list(ENGINE_SOURCES)

    def engine_hash(self) -> str:
        return engine_hash()


EVAL = Eval3()


__all__ = [
    "EVAL",
    "EVAL_ID",
    "WORLD_IDS",
    "DELIVERABLES",
    "SCORER_ONLY_NAMES",
    "ENGINE_SOURCES",
    "engine_hash",
    "ARMS",
    "CANONICAL_ARMS",
    "DEFAULT_ARMS",
    "GEN_ARMS",
    "DSL_ARMS",
    "BASELINE_ARMS",
    "CONTRASTS",
    "MODEL_EVAL_TOOL",
    "MODEL_TEST_TOOL",
    "handle_model_eval",
    "handle_model_test",
    "build_prompt",
    "paper_prompt",
    "score_eval3",
    "load_task",
    "task_path",
    "COMMAND_TIMEOUT_S",
]
