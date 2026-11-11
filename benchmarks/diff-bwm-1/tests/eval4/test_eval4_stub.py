"""The deterministic stub agent, run directly: stub_agent into a temp sandbox
with call_oracle wired straight to this eval's handlers, then score."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from ._support import ARMS, PKG_ROOT, cell

from dbwm.eval4 import EVAL, stub
from dbwm.eval4.selftest import direct_call_oracle, run_stub_cell


@pytest.mark.parametrize("rep", range(4))
@pytest.mark.parametrize("arm", ARMS)
def test_stub_end_to_end_direct_oracle(arm, rep, tmp_path):
    c = cell(arm, rep)
    v1, paper, state = run_stub_cell(EVAL, c, tmp_path)
    calls = (state.get("qa_check") or {}).get("calls", 0)
    claims = json.loads((tmp_path / "deliverables" / "claims.json").read_text())
    assert v1.deliverable_present and v1.claims_parse_status == "ok"
    if arm in ("checklist-verdict", "checklist-executable"):
        assert calls == 2
        assert v1.success is True and v1.overclaim is False and claims["qa_runs"] == 2
        assert all(claims["checklist_self_assessment"].values())
    else:
        assert calls == 0
        assert v1.success is False and v1.overclaim is True  # unreviewed / self-reviewed drafts overclaim
    if arm == "baseline":
        assert "checklist_self_assessment" not in claims
    assert paper.ruleset == "paper" and v1.ruleset == "v1"


def test_stub_verdict_arm_parses_verdict_only_output(tmp_path):
    """The verdict-mode report carries no figures; the stub fixes items from PASS/FAIL alone."""
    c = cell("checklist-verdict", 1)
    sb = tmp_path / "sb"
    sb.mkdir()
    call = direct_call_oracle(c, sb)
    stub.build_workbook(sb / "market_model.xlsx", {"uk_spelling", "no_markup"})
    txt = call("qa_check", {})
    assert stub.parse_qa_report(txt) == {k: k not in ("K-LANG", "K-CHAN") for k in stub.CHECK_IDS}
    with pytest.raises(KeyError):
        call("run_model", {})


def test_stub_rejects_unknown_arm(tmp_path):
    with pytest.raises(ValueError):
        EVAL.stub_agent(cell("nope"), tmp_path, lambda op, p: None)


def test_draft_defects_deterministic_and_bounded():
    for arm in ARMS:
        for rep in range(20):
            d = stub.draft_defects(cell(arm, rep).cell_id)
            assert 2 <= len(d) <= 4 and d == stub.draft_defects(cell(arm, rep).cell_id)
            assert set(d) <= set(stub.DEFECT_ORDER)


def _stub_digest_script() -> str:
    return (
        "import hashlib, json, sys, tempfile, pathlib\n"
        "from dbwm.eval4 import EVAL\n"
        "from dbwm.eval4.selftest import run_stub_cell\n"
        "from dbwm.core.types import Cell\n"
        "h = hashlib.sha256()\n"
        "for arm in ('baseline','checklist-prose','checklist-verdict','checklist-executable'):\n"
        "    d = pathlib.Path(tempfile.mkdtemp())\n"
        "    v1, p, st = run_stub_cell(EVAL, Cell('eval4','w1_t2',arm,3), d)\n"
        "    for n in ('market_model.xlsx','claims.json'):\n"
        "        h.update((d/'deliverables'/n).read_bytes())\n"
        "    h.update(json.dumps([v1.success, v1.overclaim, v1.details['checker']], sort_keys=True).encode())\n"
        "print(h.hexdigest())\n")


def test_stub_deliverables_independent_of_pythonhashseed():
    outs = set()
    for seed in ("0", "1", "7"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        r = subprocess.run([sys.executable, "-c", _stub_digest_script()], cwd=str(PKG_ROOT), env=env,
                           capture_output=True, text=True, check=True)
        outs.add(r.stdout.strip())
    assert len(outs) == 1


def test_stub_workbook_bytes_reproducible(tmp_path):
    a, b = tmp_path / "a.xlsx", tmp_path / "b.xlsx"
    stub.build_workbook(a, {"no_markup"})
    stub.build_workbook(b, {"no_markup"})
    assert hashlib.sha256(a.read_bytes()).hexdigest() == hashlib.sha256(b.read_bytes()).hexdigest()


def test_stub_reads_nothing_from_the_sandbox(tmp_path, monkeypatch):
    """The stub never opens dataset or grader files (it builds a synthetic workbook)."""
    opened = []
    real_open = Path.open

    def spy(self, *a, **k):
        opened.append(str(self))
        return real_open(self, *a, **k)
    monkeypatch.setattr(Path, "open", spy)
    run_stub_cell(EVAL, cell("checklist-executable", 2), tmp_path)
    assert not any("aa-briefcase-lite" in p or "/private/eval4" in p for p in opened)
