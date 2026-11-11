"""Regression tests for red-team finding RT-X (E4P-45): an agent workbook must
never make the checker, the qa_check oracle or the scorer read another file.

``formulas`` opens other workbooks named by an xl/externalLinks part, an inline
``'dir/[book.xlsx]Sheet'!A1`` reference, a defined name, or an ``INDIRECT``
string (literal or computed). Each case below puts a secret value (inside the
checker's Aurora range, so figures mode would print it) in a workbook OUTSIDE
the checked one and asserts it never reaches the corpus, the oracle text or the
verdict. The static scan does not need ``formulas``; the INDIRECT cases (which
only the single-book model and the solution backstop can stop) need a working
recalculation and skip with a reason otherwise.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from dbwm.core.types import RULESET_PAPER, RULESET_V1, OracleContext
from dbwm.eval4 import checker, oracle
from dbwm.eval4.arms import ARMS
from dbwm.eval4.score import score

from ._support import HAVE_FORMULAS, RECALC_UNUSABLE, cell

SECRET = 612345


def _secret_book(path: Path) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active["A1"] = SECRET
    wb.save(path)


def _agent_book(path: Path, formula: str, *, c1: str | None = None, defined: str | None = None) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Aurora birds"
    ws["B1"] = formula
    if c1 is not None:
        ws["C1"] = c1
    if defined is not None:
        from openpyxl.workbook.defined_name import DefinedName

        wb.defined_names["x"] = DefinedName("x", attr_text=defined)
    wb.save(path)


def _add_external_link_part(path: Path, target: str) -> None:
    """Rewrite B1 as ``[1]Sheet!A1`` with external book [1] = ``target``."""
    with zipfile.ZipFile(path) as zin:
        parts = {i.filename: zin.read(i.filename) for i in zin.infolist()}
    parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(
        b"</sheets>", b'</sheets><externalReferences><externalReference r:id="rIdExt"/></externalReferences>')
    parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].replace(
        b"</Relationships>",
        b'<Relationship Id="rIdExt" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        b'relationships/externalLink" Target="externalLinks/externalLink1.xml"/></Relationships>')
    parts["xl/externalLinks/externalLink1.xml"] = (
        b'<externalLink xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        b'<externalBook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        b'r:id="rId1"><sheetNames><sheetName val="Sheet"/></sheetNames></externalBook></externalLink>')
    parts["xl/externalLinks/_rels/externalLink1.xml.rels"] = (
        '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
        '2006/relationships/externalLinkPath" Target="%s" TargetMode="External"/></Relationships>' % target
    ).encode()
    with zipfile.ZipFile(path, "w") as z:
        for name, data in parts.items():
            z.writestr(name, data)


def _layout(tmp_path: Path):
    """tmp/work/<agent book>, tmp/secret/secret.xlsx; returns (work, secret_path)."""
    work, sec = tmp_path / "work", tmp_path / "secret"
    work.mkdir()
    sec.mkdir()
    _secret_book(sec / "secret.xlsx")
    return work, sec / "secret.xlsx"


def _no_secret(corpus) -> None:
    assert all(abs(n[2] - SECRET) > 0.5 for n in corpus.numbers), corpus.numbers
    assert SECRET not in corpus._solved.values()


# Cases the static scan catches (no recalculation needed to be safe).
STATIC_CASES = {
    "same-dir": lambda sec: dict(formula="='[secret.xlsx]Sheet'!A1"),
    "relative-climb": lambda sec: dict(formula="='../secret/[secret.xlsx]Sheet'!A1"),
    "absolute": lambda sec: dict(formula=f"='{sec.parent}/[secret.xlsx]Sheet'!A1"),
    "defined-name": lambda sec: dict(formula="=x+1", defined="'../secret/[secret.xlsx]Sheet'!$A$1"),
    "in-sum": lambda sec: dict(formula=f"=SUM('{sec.parent}/[secret.xlsx]Sheet'!A1:A2)"),
}


@pytest.mark.parametrize("case", sorted(STATIC_CASES))
def test_static_scan_refuses_recalculation(case, tmp_path):
    work, sec = _layout(tmp_path)
    if case == "same-dir":
        shutil.copy(sec, work / "secret.xlsx")
    wb = work / "market_model.xlsx"
    _agent_book(wb, **STATIC_CASES[case](sec))
    c = checker.Corpus(wb)
    assert c.external_refs, "the scan must name the reference"
    assert c.recalc_status == checker.RECALC_REJECTED_EXTERNAL
    _no_secret(c)


def test_external_link_part_is_detected(tmp_path):
    work, sec = _layout(tmp_path)
    wb = work / "market_model.xlsx"
    _agent_book(wb, "=C9+1")
    _add_external_link_part(wb, str(sec))
    reasons = checker.zip_external_links(wb)
    assert any("externalLinks" in r for r in reasons)
    assert any("externalReferences" in r for r in reasons)
    assert any(".rels" in r for r in reasons)


@pytest.mark.skipif(not HAVE_FORMULAS, reason=f"needs working formula recalculation: {RECALC_UNUSABLE}")
@pytest.mark.parametrize("computed", [False, True], ids=["indirect-literal", "indirect-computed"])
def test_indirect_never_reads_another_book(computed, tmp_path):
    work, _sec = _layout(tmp_path)
    wb = work / "market_model.xlsx"
    ref = "'../secret/[secret.xlsx]Sheet'!A1"
    if computed:
        _agent_book(wb, "=INDIRECT(C1)", c1=ref)  # invisible to the static scan
    else:
        _agent_book(wb, f'=INDIRECT("{ref}")')
    c = checker.Corpus(wb)
    _no_secret(c)
    assert c.recalc_status in (checker.RECALC_REJECTED_EXTERNAL, checker.RECALC_OK, checker.RECALC_EMPTY)


@pytest.mark.skipif(not HAVE_FORMULAS, reason=f"needs working formula recalculation: {RECALC_UNUSABLE}")
def test_single_book_model_refuses_a_second_book(tmp_path):
    import formulas

    work, _sec = _layout(tmp_path)
    wb = work / "market_model.xlsx"
    _agent_book(wb, "='../secret/[secret.xlsx]Sheet'!A1")
    sol = checker._single_book_model(formulas).loads(str(wb)).finish().calculate()
    vals = [getattr(v, "value", v) for v in sol.values()]
    assert not any(str(SECRET) in str(v) for v in vals), sol


@pytest.mark.skipif(not HAVE_FORMULAS, reason=f"needs working formula recalculation: {RECALC_UNUSABLE}")
def test_self_contained_formulas_still_recalculate(tmp_path):
    wb = tmp_path / "market_model.xlsx"
    _agent_book(wb, '=SUM(2,3)*2&"[note]"')  # brackets inside a string literal are fine
    import openpyxl

    book = openpyxl.load_workbook(wb)
    book.active["D1"] = "=SUM(2,3)*2"
    book.save(wb)
    c = checker.Corpus(wb)
    assert c.external_refs == []
    assert c.recalc_status == checker.RECALC_OK
    assert 10.0 in c._solved.values()


@pytest.mark.parametrize("arm", ["checklist-executable", "checklist-verdict"])
@pytest.mark.parametrize("case", ["relative-climb", "absolute", "link-part"])
def test_qa_check_refuses(arm, case, tmp_path):
    work, sec = _layout(tmp_path)
    wb = work / oracle.WORKBOOK
    if case == "link-part":
        _agent_book(wb, "=C9+1")
        _add_external_link_part(wb, str(sec))
    else:
        _agent_book(wb, **STATIC_CASES[case](sec))
    a = ARMS[arm]
    ctx = OracleContext(cell=cell(arm), arm=a, mode=a.oracle_mode, sandbox=work, state={})
    with pytest.raises(oracle.QACheckError, match="another workbook") as ei:
        oracle.qa_check({}, ctx)
    assert str(SECRET) not in str(ei.value)


def test_v1_fails_external_references_paper_scores_visible(tmp_path):
    work, sec = _layout(tmp_path)
    _agent_book(work / "market_model.xlsx", **STATIC_CASES["relative-climb"](sec))
    v1 = score(work, cell("baseline"), RULESET_V1)
    assert v1.success is False
    assert v1.details["external_workbook_refs"]
    assert v1.details["recalc"]["recalc_status"] == checker.RECALC_REJECTED_EXTERNAL
    paper = score(work, cell("baseline"), RULESET_PAPER)
    assert paper.success is False  # visible values only; nothing borrowed
    assert "external_workbook_refs" not in paper.details


def test_scorer_works_on_a_copy_outside_the_attempt_dir(tmp_path, monkeypatch):
    """The scorer never evaluates the workbook in place (a relative reference
    could otherwise resolve into the runs tree)."""
    work, _sec = _layout(tmp_path)
    _agent_book(work / "market_model.xlsx", "=1+1")
    seen = []
    real = checker.evaluate

    def spy(path):
        seen.append(Path(path))
        return real(path)

    monkeypatch.setattr(checker, "evaluate", spy)
    score(work, cell("baseline"), RULESET_V1)
    assert seen and all(work not in p.parents for p in seen)
    assert all(not p.exists() for p in seen), "the temp copy is removed"
