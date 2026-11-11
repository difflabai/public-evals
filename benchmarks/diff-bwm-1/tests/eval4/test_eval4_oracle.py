"""The sealed qa_check oracle: the paper tool's text in both modes, mode sealing,
payload ignored, workbook-location hardening, and a round trip through the core
oracle server."""

from __future__ import annotations

import json
import secrets
from pathlib import Path

import pytest
from ._support import HAVE_FORMULAS, RECALC_UNUSABLE, cell

from dbwm.core.types import OracleContext
from dbwm.eval4 import EVAL, checker, oracle, stub
from dbwm.eval4.arms import ARMS


def ctx_for(arm: str, sandbox: Path, mode: str | None = "use-arm") -> OracleContext:
    a = ARMS[arm]
    return OracleContext(cell=cell(arm), arm=a, mode=a.oracle_mode if mode == "use-arm" else mode,
                         sandbox=sandbox, state={})


# The ported paper checker CLI's stdout on stub workbooks (fixture "produced_by").
GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "qa_check_golden.json").read_text())["cases"]


@pytest.mark.parametrize("case", sorted(GOLDEN))
@pytest.mark.parametrize("arm,mode", [("checklist-verdict", "verdict"), ("checklist-executable", "figures")])
def test_handler_equals_paper_tool_output(case, arm, mode, sandbox):
    c = GOLDEN[case]
    if not c["cached"] and not HAVE_FORMULAS:
        pytest.skip(f"this golden workbook needs formula recalculation: {RECALC_UNUSABLE}")
    stub.build_workbook(sandbox / "market_model.xlsx", set(c["defects"]), cached=c["cached"])
    assert oracle.qa_check({}, ctx_for(arm, sandbox)) == c[mode]


def test_goldens_cover_every_defect_and_have_no_host_paths():
    assert {d for c in GOLDEN.values() for d in c["defects"]} == set(stub.DEFECT_ORDER)
    assert {c["cached"] for c in GOLDEN.values()} == {True, False}
    for c in GOLDEN.values():
        for t in (c["verdict"], c["figures"]):
            assert t.startswith("CHECKLIST RESULT: ")
            assert "/" + "Users/" not in t and "/private/var" not in t
            assert "market_model.xlsx" not in t.splitlines()[0]


def test_payload_cannot_change_mode_or_path(sandbox):
    stub.build_workbook(sandbox / "market_model.xlsx", set())
    plain = oracle.qa_check({}, ctx_for("checklist-verdict", sandbox))
    hostile = oracle.qa_check({"mode": "figures", "path": "/etc/passwd", "workbook": "../x.xlsx",
                               "channel": "figures"}, ctx_for("checklist-verdict", sandbox))
    assert hostile == plain and "criteria:" not in hostile


def test_bad_sealed_mode_is_a_harness_error(sandbox):
    stub.build_workbook(sandbox / "market_model.xlsx", set())
    with pytest.raises(RuntimeError):
        oracle.qa_check({}, ctx_for("checklist-verdict", sandbox, mode=None))
    with pytest.raises(RuntimeError):
        oracle.qa_check({}, ctx_for("checklist-verdict", sandbox, mode="serialize"))


def test_missing_and_unreadable_workbook(sandbox):
    with pytest.raises(oracle.QACheckError, match="not found"):
        oracle.qa_check({}, ctx_for("checklist-verdict", sandbox))
    (sandbox / "market_model.xlsx").write_text("not a workbook")
    with pytest.raises(oracle.QACheckError, match="could not read"):
        oracle.qa_check({}, ctx_for("checklist-verdict", sandbox))


def test_symlink_and_directory_refused(sandbox, tmp_path):
    target = tmp_path / "outside.xlsx"
    stub.build_workbook(target, set())
    (sandbox / "market_model.xlsx").symlink_to(target)
    with pytest.raises(oracle.QACheckError, match="symlink"):
        oracle.qa_check({}, ctx_for("checklist-executable", sandbox))
    (sandbox / "market_model.xlsx").unlink()
    (sandbox / "market_model.xlsx").mkdir()
    with pytest.raises(oracle.QACheckError):
        oracle.qa_check({}, ctx_for("checklist-executable", sandbox))


def test_size_cap(sandbox, monkeypatch):
    stub.build_workbook(sandbox / "market_model.xlsx", set())
    monkeypatch.setattr(oracle, "MAX_WORKBOOK_BYTES", 1000)
    with pytest.raises(oracle.QACheckError, match="larger than"):
        oracle.qa_check({}, ctx_for("checklist-executable", sandbox))


def test_checker_never_touches_the_sandbox_file(sandbox, monkeypatch):
    """The checker runs on a private copy outside the sandbox."""
    stub.build_workbook(sandbox / "market_model.xlsx", set())
    seen = []
    real = checker.evaluate

    def spy(path):
        seen.append(Path(path).resolve())
        return real(path)
    monkeypatch.setattr(checker, "evaluate", spy)
    oracle.qa_check({}, ctx_for("checklist-verdict", sandbox))
    assert seen and sandbox.resolve() not in seen[0].parents


def test_recalc_note_when_unavailable(sandbox, monkeypatch):
    import sys

    stub.build_workbook(sandbox / "market_model.xlsx", set(), cached=False)
    monkeypatch.setitem(sys.modules, "formulas", None)
    out = oracle.qa_check({}, ctx_for("checklist-executable", sandbox))
    assert out.rstrip().endswith("computed figures were not checked.")


def test_state_records_calls(sandbox):
    stub.build_workbook(sandbox / "market_model.xlsx", {"no_markup"})
    ctx = ctx_for("checklist-verdict", sandbox)
    oracle.qa_check({}, ctx)
    oracle.qa_check({}, ctx)
    st = ctx.state["qa_check"]
    assert st["calls"] == 2 and st["history"][0]["passed"] == 11


def test_handlers_per_arm():
    assert EVAL.oracle_handlers(cell("baseline")) == {}
    assert EVAL.oracle_handlers(cell("checklist-prose")) == {}
    assert set(EVAL.oracle_handlers(cell("checklist-verdict"))) == {"qa_check"}
    assert set(EVAL.oracle_handlers(cell("checklist-executable"))) == {"qa_check"}


def test_round_trip_through_core_oracle_server(sandbox, tmp_path):
    """The real server keeps the arm's sealed mode and logs a `mode` override."""
    from dbwm.core.oracle import client
    from dbwm.core.oracle.server import OracleServer

    stub.build_workbook(sandbox / "market_model.xlsx", {"uk_spelling"})
    c = cell("checklist-verdict")
    arm = ARMS[c.arm]
    ctx = OracleContext(cell=c, arm=arm, mode=arm.oracle_mode, sandbox=sandbox, state={})
    token = secrets.token_hex(16)
    log = tmp_path / "oracle_calls.jsonl"
    with OracleServer(EVAL.oracle_handlers(c), ctx, token, log, specs=arm.tools.oracle_tools) as srv:
        addr = srv.address
        out = client.call(addr, token, "qa_check", {"mode": "figures"})
    assert "FAIL  K-LANG" in out and "criteria:" not in out
    recs = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    assert any(r.get("override_attempt") for r in recs)
    assert ctx.mode == "verdict"
