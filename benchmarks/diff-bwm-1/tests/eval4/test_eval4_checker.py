"""The deterministic checker: registry goldens, known-good / known-bad workbooks,
and the dataset's published submissions."""

from __future__ import annotations

import hashlib
import sys

import openpyxl
import pytest
from ._support import HAVE_FORMULAS, needs_formulas, needs_private

from dbwm.eval4 import checker, stub
from dbwm.eval4.selftest import PAPER_CHECKLIST_SHA256

PAPER_CHECK_IDS = ["K-SCEN", "K-AURORA", "K-PROD", "K-FX", "K-DEMAND", "K-CHAN", "K-ELAST", "K-INTEG",
                   "K-TRACE", "K-LANG", "K-RECON", "K-PREM"]


def test_registry_ids_and_rubric_map():
    assert checker.CHECK_IDS == PAPER_CHECK_IDS
    assert checker.K_TO_RUBRIC["K-SCEN"] == ["W1-T2-A-01", "W1-T2-A-09"]
    assert checker.K_TO_RUBRIC["K-INTEG"] == checker.K_TO_RUBRIC["K-TRACE"] == ["W1-T2-A-07"]
    covered = sorted({r for v in checker.K_TO_RUBRIC.values() for r in v})
    assert covered == [f"W1-T2-A-0{i}" for i in range(1, 10)] + [f"W1-T2-C-0{i}" for i in range(1, 4)]


def test_checklist_md_is_the_paper_prose_output():
    b = checker.checklist_md_bytes()
    assert hashlib.sha256(b).hexdigest() == PAPER_CHECKLIST_SHA256
    assert b == (checker.prose_checklist() + "\n").encode()
    assert b.count(b"\n") == 16 and b.startswith(b"QA CHECKLIST for market_model.xlsx")


def test_prose_cli_matches_checklist(capsys):
    assert checker.main(["--mode", "prose"]) == 0
    assert capsys.readouterr().out.encode() == checker.checklist_md_bytes()


@pytest.mark.parametrize("cached", [True, False])
def test_clean_stub_workbook_passes_all_12(tmp_path, cached):
    if not cached and not HAVE_FORMULAS:
        pytest.skip("uncached workbook needs a usable 'formulas' (absent, or its scipy fails to load)")
    wb = tmp_path / "m.xlsx"
    stub.build_workbook(wb, set(), cached=cached)
    res, corpus = checker.evaluate(wb)
    assert res["summary"] == {"passed": 12, "total": 12, "failed_ids": []}
    assert checker.recalc_info(corpus)["recalc_status"] == ("not_needed" if cached else "ok")


@pytest.mark.parametrize("defect", stub.DEFECT_ORDER)
def test_each_defect_fails_exactly_its_check(tmp_path, defect):
    wb = tmp_path / "m.xlsx"
    stub.build_workbook(wb, {defect})
    res, _ = checker.evaluate(wb)
    assert res["summary"]["failed_ids"] == [stub.DEFECTS[defect]]


def test_known_bad_default_tab_names_and_constants(tmp_path):
    """K-INTEG: 'Sheet1' tab names and a constants-only workbook fail."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for i in range(60):
        ws.append([f"Input {i}", i * 1.5])
    p = tmp_path / "bad.xlsx"
    wb.save(p)
    res, c = checker.evaluate(p)
    integ = next(r for r in res["checks"] if r["id"] == "K-INTEG")
    assert integ["pass"] is False
    assert integ["measured"]["default_named_tabs"] == ["Sheet1"]
    assert integ["measured"]["formula_cells"] == 0


def test_analyse_regex_does_not_flag_analyses(tmp_path):
    """US plural 'analyses' is not a UK spelling; 'analysed' is."""
    for text, flagged in (("Three analyses were run", False), ("We analysed the data", True)):
        wb = openpyxl.Workbook()
        wb.active.title = "Notes"
        wb.active["A1"] = text
        p = tmp_path / "w.xlsx"
        wb.save(p)
        res, _ = checker.evaluate(p)
        lang = next(r for r in res["checks"] if r["id"] == "K-LANG")
        assert (lang["measured"]["uk_spellings"] == ["analyse"]) is flagged


def test_recalc_unavailable_is_recorded(tmp_path, monkeypatch):
    wb = tmp_path / "m.xlsx"
    stub.build_workbook(wb, set(), cached=False)
    monkeypatch.setitem(sys.modules, "formulas", None)  # `import formulas` -> ImportError
    res, corpus = checker.evaluate(wb)
    info = checker.recalc_info(corpus)
    assert info["recalc_status"] == checker.RECALC_UNAVAILABLE
    assert info["uncached_formula_cells"] == checker.uncached_formula_cells(wb) > 0
    assert checker.formulas_available() is False


def test_format_report_modes(tmp_path):
    wb = tmp_path / "m.xlsx"
    stub.build_workbook(wb, {"uk_spelling", "unsourced_fx"})
    res, _ = checker.evaluate(wb)
    v = checker.format_report(res, "verdict")
    f = checker.format_report(res, "figures")
    assert v.splitlines()[0] == "CHECKLIST RESULT: 10/12 PASS"
    assert len(v.splitlines()) == 13 and "criteria:" not in v and "consistency" not in v
    assert "FAIL  K-LANG" in v and "FAIL  K-FX" in v
    assert '"colour"' in f and "  consistency: " in f


@needs_private
@needs_formulas
def test_published_submissions_score_spread():
    """The checker on the dataset's six published workbooks: the spread of scores, not which workbook scored what."""
    from dbwm.eval4 import dataset

    got = sorted((checker.evaluate(dataset.submission_path(m))[0]["summary"]["passed"]
                  for m in dataset.PUBLISHED_SUBMISSIONS), reverse=True)
    assert got == [11, 10, 8, 3, 3, 3]
