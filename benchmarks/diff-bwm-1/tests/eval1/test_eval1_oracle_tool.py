"""Eval-1 run_model oracle: paper tool goldens, documented v1 differences, sealed mode, budget."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from dbwm.core.types import Cell, OracleContext
from dbwm.eval1.oracle_tool import BROKEN_WARNING, MODES, render_paper, render_v1, run_model

NW = "northwind-analytics"
TOKEN = "eval1-test-token-0123456789"


def _text(r) -> str:
    return (r if isinstance(r, str) else json.dumps(r, indent=2)) + "\n"


def test_paper_tool_goldens(goldens, golden_inputs):
    """render_paper prints exactly what bwm_query.py (local backend) printed, for 612 cases."""
    n = 0
    for case in goldens["tool_cases"]:
        plan = golden_inputs["plans"][case["company"]][case["plan"]]
        if "error" in case:  # the paper CLI died with a traceback and printed nothing
            with pytest.raises(Exception) as ei:
                render_paper(case["company"], case["tier"], plan, case["mode"])
            assert type(ei.value).__name__ == case["error"]
            assert case["sha256"] == hashlib.sha256(b"").hexdigest()
        else:
            got = _text(render_paper(case["company"], case["tier"], plan, case["mode"]))
            assert hashlib.sha256(got.encode()).hexdigest() == case["sha256"], case
        n += 1
    assert n == 612


def test_paper_tool_full_texts(goldens, golden_inputs):
    for key, text in goldens["full_tool_stdout"].items():
        company, tier, pname, mode = key.split("|")
        assert _text(render_paper(company, tier, golden_inputs["plans"][company][pname], mode)) == text


def test_paper_mode_override_escape_is_closed(goldens, golden_inputs, ev):
    """The paper tool let ``--mode numeric`` override the sealed BWM_QUERY_MODE=broken (E1-D3).
    The v1 handler reads the mode from ctx only; a payload ``mode`` key changes nothing."""
    esc = goldens["mode_override_escape"]
    assert esc["env_mode"] == "broken" and json.loads(esc["stdout"])["mode"] == "numeric"
    plan = golden_inputs["plans"][NW]["naive"]
    cell = Cell("eval1", f"{NW}__hostile", "broken-model", 0)
    arm = ev.arms["broken-model"]
    ctx = OracleContext(cell=cell, arm=arm, mode=arm.oracle_mode, sandbox=Path("."), state={})
    sealed = run_model({"plan": plan}, ctx)
    for override in ({"mode": "numeric"}, {"backend": "live"}, {"mode": "numeric", "channel": "x"}):
        assert run_model(dict({"plan": plan}, **override), ctx) == sealed
    assert sealed == render_v1(NW, "hostile", plan, "broken")


@pytest.mark.parametrize("mode", MODES)
def test_v1_output_equals_paper_minus_documented_keys(golden_inputs, mode):
    """E1-T01/T02: v1 = paper output without mode/backend/_warning and with the neutral prose line."""
    for company, plans in golden_inputs["plans"].items():
        for name in ("base", "careful", "naive", "spike_q5"):
            plan = plans[name]
            p = render_paper(company, "hard", plan, mode)
            v = render_v1(company, "hard", plan, mode)
            if mode == "prose":
                assert isinstance(v, str)
                assert "notably higher" not in v
                pl, vl = p.splitlines(), v.splitlines()
                assert len(pl) == len(vl)
                diff = [(a, b) for a, b in zip(pl, vl) if a != b]
                assert len(diff) <= 1
                for a, b in diff:
                    assert "notably higher" in a and b.startswith("- Revenue growth from the starting level")
                    assert a.endswith(b.rsplit(" ", 1)[-1])  # same strong/modest reading
            else:
                stripped = {k: x for k, x in p.items() if k not in ("mode", "backend", "_warning")}
                assert v == stripped
                if mode == "broken":
                    assert p["_warning"] == BROKEN_WARNING and "_warning" not in v
                assert "mode" not in v and "backend" not in v


@pytest.mark.parametrize("company,factor", [
    ("atlas-advisory", "1.0952"), ("sprout-crm", "1.0952"), ("northwind-analytics", "1.0870"),
    ("gigbridge-marketplace", "1.0800"), ("voltcore-devices", "1.0800"),
])
def test_broken_lesion_inflates_goal_multiple_by_company_factor(calibration, company, factor):
    """The corruption is kept bit-for-bit, so its design dependence is too (ledger E1-D13):
    revenue's sorted-key index fixes the inflation of the goal multiple per company."""
    plan = {"bindings": calibration[company]["reference_plans"]["careful"]}
    num = render_v1(company, "hostile", plan, "numeric")
    brk = render_v1(company, "hostile", plan, "broken")
    ratio = Decimal(brk["goal"]["actual_multiple"]) / Decimal(num["goal"]["actual_multiple"])
    assert str(ratio.quantize(Decimal("0.0001"))) == factor
    # inflate-only: every corrupted revenue figure is above the true one
    for t, b in zip(num["trajectory"]["revenue"], brk["trajectory"]["revenue"]):
        assert Decimal(b) > Decimal(t)


def test_v1_tool_validates_like_the_v1_scorer(calibration):
    careful = calibration[NW]["reference_plans"]["careful"]
    exploit = dict(careful, fixed_opex=["0"] * 6)
    assert render_v1(NW, "hostile", {"bindings": exploit}, "numeric")["ok"] is False
    assert render_paper(NW, "hostile", {"bindings": exploit}, "numeric")["ok"] is True
    assert render_v1(NW, "hostile", {"bindings": {"acv": "1.0"}}, "numeric")["ok"] is False
    for bad in ("NaN", "abc"):
        r = render_v1(NW, "hostile", {"bindings": {"acv": ["9000"] * 5 + [bad]}}, "numeric")
        assert r["ok"] is False  # never a crash (E1-T05)


def test_handler_payload_forms(ev):
    cell = Cell("eval1", f"{NW}__soft", "grounded-iterate", 0)
    arm = ev.arms["grounded-iterate"]
    ctx = OracleContext(cell=cell, arm=arm, mode=arm.oracle_mode, sandbox=Path("."), state={})
    as_obj = run_model({"plan": {"bindings": {"acv": ["9500"] * 6}}}, ctx)
    as_str = run_model({"plan": json.dumps({"bindings": {"acv": ["9500"] * 6}})}, ctx)
    assert as_obj == as_str and as_obj["ok"] is True
    assert run_model({}, ctx)["ok"] is False
    assert run_model({"plan": "{nope"}, ctx)["ok"] is False
    bad_ctx = OracleContext(cell=cell, arm=arm, mode="omniscient", sandbox=Path("."), state={})
    with pytest.raises(RuntimeError):
        run_model({"plan": {}}, bad_ctx)


def test_ungrounded_has_no_handlers_and_tool_arms_one(ev):
    for arm_name, arm in ev.arms.items():
        h = ev.oracle_handlers(Cell("eval1", f"{NW}__soft", arm_name, 0))
        assert sorted(h) == ([] if arm.oracle_mode is None else ["run_model"])


def test_readonly_budget_enforced_by_core_server(ev, calibration, tmp_path):
    """The readonly arm's query_budget (regime.json: 3) is enforced server-side: the 4th call
    is refused (the paper enforced none). Override keys are logged, never forwarded."""
    server_mod = pytest.importorskip("dbwm.core.oracle.server")
    client_mod = pytest.importorskip("dbwm.core.oracle.client")
    arm = ev.arms["grounded-readonly"]
    assert arm.query_budget == 3
    cell = Cell("eval1", f"{NW}__hostile", arm.name, 0)
    ctx = OracleContext(cell=cell, arm=arm, mode=arm.oracle_mode, sandbox=tmp_path, state={})
    log = tmp_path / "oracle_calls.jsonl"
    srv = server_mod.OracleServer(ev.oracle_handlers(cell), ctx, TOKEN, log, arm.query_budget,
                                  specs=arm.tools.oracle_tools)
    addr = srv.start()
    try:
        plan = {"bindings": calibration[NW]["reference_plans"]["careful"]}
        results = []
        for i in range(3):
            results.append(client_mod.call(addr, TOKEN, "run_model", {"plan": plan, "mode": "broken"}))
        assert all(r["goal"]["met"] for r in results)  # numeric, not broken: override ignored
        with pytest.raises(Exception):
            client_mod.call(addr, TOKEN, "run_model", {"plan": plan})
    finally:
        srv.stop()
    lines = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
    assert sum(1 for x in lines if x.get("override_attempt")) == 3
