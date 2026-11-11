"""Shared constants, skip marks and helpers for the eval-4 tests (imported as ``from ._support import ...``).

Kept out of conftest.py on purpose: test modules importing ``conftest`` by name
pick whichever conftest pytest loaded last, so conftest.py holds fixtures only.

Dataset-dependent tests skip with an explicit SKIPPED-NO-DATASET reason when
AA-Briefcase-Lite has not been fetched (`dbwm fetch eval4`); tests that need
formula recalculation skip with a reason when the optional `formulas` package
is absent. A skip is never a pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dbwm.core.types import Cell
from dbwm.eval4 import checker, dataset
from dbwm.eval4.evaldef import RUBRIC_IDS

HERE = Path(__file__).resolve().parent
PKG_ROOT = HERE.parents[1]

HAVE_DATA = dataset.is_fetched("data")
HAVE_PRIVATE = dataset.is_fetched("private")
RECALC_UNUSABLE = checker.recalc_unusable_reason()  # None: a recalculation smoke passed (review M4)
HAVE_FORMULAS = RECALC_UNUSABLE is None

needs_data = pytest.mark.skipif(not HAVE_DATA, reason="SKIPPED-NO-DATASET: run `dbwm fetch eval4`")
needs_private = pytest.mark.skipif(not HAVE_PRIVATE, reason="SKIPPED-NO-DATASET: run `dbwm fetch eval4`")
needs_formulas = pytest.mark.skipif(not HAVE_FORMULAS,
                                    reason=f"needs working formula recalculation: {RECALC_UNUSABLE}")

ARMS = ("baseline", "checklist-prose", "checklist-verdict", "checklist-executable")


def cell(arm: str, rep: int = 0) -> Cell:
    return Cell("eval4", "w1_t2", arm, rep)


# Synthetic rubric rows + prompts so judge tests run without the dataset.
FAKE_MATERIALS = {
    "system": "You are grading a submitted deliverable against one binary rubric check. (test)",
    "user_tpl": ("<task>{task_markdown}</task><d>{check_description}</d><p>{score_1_criteria}</p>"
                 "<f>{score_0_criteria}</f><a><<<SUBMISSION CONTENT MESSAGES>>></a>"),
    "task_md": "Build market_model.xlsx (test brief).",
    "checks": [{"check_id": cid, "check_type": cid.split("-")[2], "check_description": f"item {cid}",
                "score_1_criteria": f"pass {cid}", "score_0_criteria": f"fail {cid}"} for cid in RUBRIC_IDS],
}
