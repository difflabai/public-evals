"""``EVAL`` for eval 4: AA-Briefcase-Lite w1_t2 under the checklist-executability manipulation.

One task instance (``w1_t2``: build ``market_model.xlsx`` from 147 source files
of a simulated private-equity week), four arms that differ only in how the QA
criteria are available (none / prose / runnable verdict-only / runnable with
figures). Every arm needs bash to script the workbook, so every arm requires
``Isolation.CONTAINER``.

Scoring is two-layered. ``score()`` is the deterministic part (the checker's
all-12 pass as ``success``, claims, checker-referenced self-consistency); the
PRIMARY measure, the dataset's own rubric judged by an LLM panel, is a separate
step (:func:`dbwm.eval4.judge.judge_run`, CLI ``dbwm eval4-judge``) whose
output :meth:`Eval4.report` folds in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from dbwm.core.types import (
    ArmSpec,
    Cell,
    CheckResult,
    RegimeConfig,
    ScoreResult,
    StagedFile,
    StagingManifest)

from . import _paths, checker, dataset, oracle, prompt, score as score_mod, stub
from .arms import ARM_ORDER, ARMS, ARMS_WITH_CHECKLIST

EVAL_ID = "eval4"
INSTANCES = ("w1_t2",)
DELIVERABLES = ("market_model.xlsx", "claims.json")
_PKG_DIR = Path(__file__).resolve().parent

# Names that only scorer/judge material carries. The audit flags a tool INPUT
# whose path has one of these as a segment or basename (a bare listing is a
# note). Deliberately NOT listed: "checker.py" / "qa_check" (prose agents
# legitimately write their own QA scripts under such names), "CHECKLIST.md"
# (staged).
SCORER_ONLY_NAMES = (
    "reference.json", "world.json", "ANSWER_KEY", "answer_key", "grader", "private",
    "checks.jsonl", "tasks.jsonl", "grading_rubric_w1.json", "source_graph_w1.json",
    "traceability_w1.json", "judge_system.txt", "judge_user.txt", "eval_submission.txt",
    "eval_system.txt", "submissions", "aa-briefcase-lite", "_qa_impl.py",
    "checker_result.json", "judged.json", "panel.json", "CANARY.txt")

# Private identifiers an honest agent cannot produce from what it is given
# (audit rule (b): never an achievable target, never an identifier an agent
# might coin itself -- so no checker constant names such as UK_SPELLINGS, which
# a prose agent writing its own QA script could plausibly use). None of them
# occurs in any staged file (TASK.md, CHECKLIST.md, the 147 sources incl. the
# XML inside .docx/.xlsx) -- tested per arm. The broader "no checker source in
# the sandbox" property is tested separately (tests/eval4/test_eval4_staging.py).
RUBRIC_IDS = tuple(f"W1-T2-A-0{i}" for i in range(1, 10)) + tuple(f"W1-T2-C-0{i}" for i in range(1, 4))
PRIVATE_IDENTIFIERS = ("_qa_impl", "verdict_lock_source", "K_TO_RUBRIC")
JUDGE_SENTINELS_FALLBACK = ("<<<SUBMISSION CONTENT MESSAGES>>>",)


def engine_sources() -> list[Path]:
    """The files whose bytes define the deterministic scorer (hashed into verdicts)."""
    core_claims = _paths.PKG_ROOT / "dbwm" / "core" / "claims.py"
    files = [_PKG_DIR / "checker.py", _PKG_DIR / "score.py"]
    if core_claims.is_file():
        files.append(core_claims)
    return files


class Eval4:
    eval_id = EVAL_ID
    title = ("Eval 4 -- AA-Briefcase-Lite w1_t2 (market_model.xlsx): does a runnable QA checklist "
             "beat the same checklist as prose?")
    arms: dict[str, ArmSpec] = ARMS
    deliverables = DELIVERABLES

    # -- design ------------------------------------------------------------

    def instances(self) -> list[str]:
        return list(INSTANCES)

    def default_regime(self) -> dict[str, Any]:
        return json.loads((_PKG_DIR / "regime.json").read_text(encoding="utf-8"))

    def cells(self, regime: RegimeConfig) -> list[Cell]:
        instances = list(regime.instances) if regime.instances else self.instances()
        unknown_i = [i for i in instances if i not in INSTANCES]
        if unknown_i:
            raise ValueError(f"unknown eval4 instance(s) {unknown_i}; known: {list(INSTANCES)}")
        unknown_a = [a for a in regime.arms if a not in ARMS]
        if unknown_a:
            raise ValueError(f"unknown eval4 arm(s) {unknown_a}; known: {list(ARM_ORDER)}")
        if regime.reps < 1:
            raise ValueError("reps must be >= 1")
        return [Cell(EVAL_ID, inst, arm, rep)
                for rep in range(regime.reps) for inst in instances for arm in regime.arms]

    def _check_cell(self, cell: Cell) -> None:
        if cell.instance not in INSTANCES:
            raise ValueError(f"unknown eval4 instance {cell.instance!r}")
        if cell.arm not in ARMS:
            raise ValueError(f"unknown eval4 arm {cell.arm!r}")

    # -- what the agent sees -------------------------------------------------

    def stage(self, cell: Cell) -> StagingManifest:
        """The 147 source files as ``shared/...`` and ``week/...`` (as the paper
        staged them), CHECKLIST.md for the three checklist arms, and TASK.md.
        Nothing else: no checker source, no qa_check implementation (the runnable
        arms get only the oracle CLI shim, staged by the sandbox builder)."""
        self._check_cell(cell)
        if not dataset.is_fetched("data"):
            raise FileNotFoundError(
                "eval4: the AA-Briefcase-Lite agent-visible files are not installed; run `dbwm fetch eval4`")
        files = [StagedFile(dst=dst, src=src) for dst, src in dataset.sources()]
        if cell.arm in ARMS_WITH_CHECKLIST:
            files.append(StagedFile(dst="CHECKLIST.md", content=checker.checklist_md_bytes()))
        return StagingManifest(files=files, task_md=prompt.build_task_md(cell.arm, "v1"), writable_dirs=[])

    def prompt(self, cell: Cell) -> str:
        self._check_cell(cell)
        return prompt.PROMPT

    def oracle_handlers(self, cell: Cell) -> dict[str, Callable]:
        self._check_cell(cell)
        return oracle.handlers_for(self.arms[cell.arm].oracle_mode)

    # -- scoring -------------------------------------------------------------

    def score(self, deliverables_dir: Path, cell: Cell, ruleset: str) -> ScoreResult:
        self._check_cell(cell)
        return score_mod.score(Path(deliverables_dir), cell, ruleset)

    def answer_key_values(self, cell: Cell) -> list[str]:
        """Rubric check IDs, private harness identifiers and the judge prompt's opening line."""
        self._check_cell(cell)
        vals = list(RUBRIC_IDS) + list(PRIVATE_IDENTIFIERS)
        try:
            first = dataset.read_private("prompts/judge_system.txt").strip().splitlines()[0].strip()
            if len(first) >= 30:
                vals.append(first)
        except FileNotFoundError:
            pass
        vals += list(JUDGE_SENTINELS_FALLBACK)
        return vals

    def duplicate_corpus(self, cell: Cell) -> list[Path]:
        """Workbooks an attempt must not simply copy: the dataset's six
        published submissions (if fetched)."""
        out: list[Path] = []
        for m in dataset.PUBLISHED_SUBMISSIONS:
            p = dataset.submission_path(m)
            if p.is_file():
                out.append(p)
        return out

    # -- agents ----------------------------------------------------------------

    def stub_agent(self, cell: Cell, sandbox: Path, call_oracle: Callable[[str, dict], Any]) -> None:
        self._check_cell(cell)
        stub.stub_agent(cell, Path(sandbox), call_oracle)

    # -- analysis ----------------------------------------------------------------

    def report(self, run_dir: Path) -> dict[str, Any]:
        from . import report as report_mod

        return report_mod.run_report(Path(run_dir), self)

    def selftest(self) -> list[CheckResult]:
        from . import selftest as selftest_mod

        return selftest_mod.selftest(self)

    def engine_sources(self) -> list[Path]:
        return engine_sources()


EVAL = Eval4()
