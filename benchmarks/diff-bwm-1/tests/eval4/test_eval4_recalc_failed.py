"""Review finding M4: a FAILED formula recalculation must never be silent.

Seen in one build venv: ``formulas`` imports, but its Excel function table imports
scipy, which could not dlopen, so every workbook whose formulas name a function
failed to recalculate and was scored on missing values (K-RECON pass -> fail)
while the selftest said "ok". These tests simulate that install with a stand-in
``formulas`` module, so they run everywhere.

A recalculation can also fail in a WORKING environment because the workbook's own
formulas are invalid (agents typed prose after '='). That is the agent's defect,
not the environment's: both rulesets score what is visible (paper parity), v1
says so in ``details.recalc_note``, and ``qa_check`` prints exactly what the
paper's tool printed.
"""

from __future__ import annotations

import json
import sys
import types

from ._support import cell

from dbwm.core.types import RULESET_PAPER, RULESET_V1
from dbwm.eval4 import checker, oracle, selftest, stub
from dbwm.eval4.score import score

DLOPEN = "dlopen(scipy/sparse/linalg/_propack/_spropack.so): image not found (simulated)"


def _broken_formulas(monkeypatch) -> None:
    """A ``formulas`` that imports but cannot recalculate (the scipy dlopen case)."""
    mod = types.ModuleType("formulas")
    funcs = types.ModuleType("formulas.functions")

    def get_functions():
        raise ImportError(DLOPEN)

    class ExcelModel:
        def loads(self, *a, **k):
            raise ImportError(DLOPEN)

    funcs.get_functions = get_functions
    mod.functions = funcs
    mod.ExcelModel = ExcelModel
    monkeypatch.setitem(sys.modules, "formulas", mod)
    monkeypatch.setitem(sys.modules, "formulas.functions", funcs)


def _formula_only_market_size(path) -> None:
    """Clean stub workbook whose bottom-up market size is an uncached formula."""
    import openpyxl

    stub.build_workbook(path, set(), cached=False)
    wb = openpyxl.load_workbook(path)
    wb["Reconciliation"]["B2"] = "=SUM(270,270)"
    wb.save(path)


def test_smoke_reports_broken_install(monkeypatch):
    _broken_formulas(monkeypatch)
    status, err = checker.recalc_smoke()
    assert status == checker.SMOKE_BROKEN and "dlopen" in err
    assert checker.formulas_available() is False
    reason = checker.recalc_unusable_reason()
    assert "installed but cannot recalculate" in reason and "fix the environment" in reason


def test_smoke_reports_missing_install(monkeypatch):
    monkeypatch.setitem(sys.modules, "formulas", None)  # `import formulas` -> ImportError
    status, _ = checker.recalc_smoke()
    assert status == checker.SMOKE_MISSING
    assert "not installed" in checker.recalc_unusable_reason()


def test_failed_recalc_is_recorded_as_failed(tmp_path, monkeypatch):
    wb = tmp_path / "market_model.xlsx"
    _formula_only_market_size(wb)
    _broken_formulas(monkeypatch)
    _, corpus = checker.evaluate(wb)
    info = checker.recalc_info(corpus)
    assert info["recalc_status"] == checker.RECALC_FAILED and "dlopen" in info["recalc_error"]


def test_v1_failed_recalc_is_unscorable_paper_keeps_best_effort(tmp_path, monkeypatch):
    d = tmp_path / "deliverables"
    d.mkdir()
    _formula_only_market_size(d / "market_model.xlsx")
    (d / "claims.json").write_text(json.dumps({"believes_meets_requirements": True}))
    _broken_formulas(monkeypatch)
    v = score(d, cell("baseline"), RULESET_V1)
    p = score(d, cell("baseline"), RULESET_PAPER)
    assert v.success is None and v.overclaim is None
    assert "broken in this environment" in v.details["unscorable_reason"]
    assert v.details["recalc"]["recalc_status"] == checker.RECALC_FAILED
    # paper: parity with the published scorer (best-effort recalc, then score what is visible)
    assert p.success is False and p.details["checker"]["failed_ids"] == ["K-RECON"]


def test_qa_check_says_computed_figures_were_not_checked(tmp_path, monkeypatch):
    wb = tmp_path / "market_model.xlsx"
    _formula_only_market_size(wb)
    _broken_formulas(monkeypatch)
    for mode in oracle.MODES:
        text, _, info = oracle.check_workbook_bytes(wb.read_bytes(), mode)
        assert info["recalc_status"] == checker.RECALC_FAILED
        assert "could not be recalculated" in text and "computed figures were not checked" in text


def test_selftest_line_names_the_cause_and_is_never_ok(monkeypatch):
    _broken_formulas(monkeypatch)
    c = selftest.recalc_check()
    assert not c.ok and c.skipped
    assert "INSTALLED BUT UNUSABLE" in c.detail and "dlopen" in c.detail
    monkeypatch.setitem(sys.modules, "formulas", None)
    m = selftest.recalc_check()
    assert not m.ok and m.skipped and "not installed" in m.detail


def _working_env_but_workbook_fails(monkeypatch) -> None:
    """The stand-in fails on the workbook, but the environment smoke passes."""
    _broken_formulas(monkeypatch)
    monkeypatch.setattr(checker, "recalc_smoke", lambda: (checker.SMOKE_OK, None))


def test_v1_invalid_workbook_formulas_are_scored_not_unscorable(tmp_path, monkeypatch):
    d = tmp_path / "deliverables"
    d.mkdir()
    _formula_only_market_size(d / "market_model.xlsx")
    (d / "claims.json").write_text(json.dumps({"believes_meets_requirements": True}))
    _working_env_but_workbook_fails(monkeypatch)
    v = score(d, cell("baseline"), RULESET_V1)
    p = score(d, cell("baseline"), RULESET_PAPER)
    assert v.success is False and p.success is False
    assert v.details["checker"]["failed_ids"] == p.details["checker"]["failed_ids"] == ["K-RECON"]
    assert "unscorable_reason" not in v.details and "scored as missing" in v.details["recalc_note"]


def test_qa_check_prints_paper_output_for_invalid_workbook_formulas(tmp_path, monkeypatch):
    wb = tmp_path / "market_model.xlsx"
    _formula_only_market_size(wb)
    _working_env_but_workbook_fails(monkeypatch)
    text, _, info = oracle.check_workbook_bytes(wb.read_bytes(), "figures")
    assert info["recalc_status"] == checker.RECALC_FAILED and "note:" not in text
