"""qa_check oracle: the paper tool's verdict text, verdict and figures modes, sealed mode,
path safety, server round-trip."""

from __future__ import annotations

import json
import os
import secrets

import pytest

from dbwm.eval5 import EVAL
from dbwm.eval5 import dataset as D
from dbwm.eval5 import engine as E
from dbwm.eval5 import stubs
from dbwm.eval5.oracle import qa_check

from .conftest import cell, ctx_for


def test_verdict_output_equals_paper_tool_where_rulesets_agree(tmp_path):
    """For every stub whose per-check grades agree under as-run and v1, the v1 oracle prints
    exactly what the paper's `qa_check.py` printed in verdict mode."""
    compared = 0
    for sid in D.SCENARIOS:
        for tier in ("weak", "medium", "strong"):
            for ruleset in ("v1", "as-run"):
                d = tmp_path / f"{sid}-{tier}-{ruleset}"
                d.mkdir()
                stubs.render(sid, tier, d, ruleset)
                paper = E.run_checks(D.registry(sid, "as-run"), d, "as-run")  # what the paper's tool graded
                v1 = E.run_checks(D.registry(sid, "v1"), E.SafeSubmission(d), "v1")
                if [(c["id"], c["pass"]) for c in paper["checks"]] != [(c["id"], c["pass"]) for c in v1["checks"]]:
                    continue
                got = qa_check({}, ctx_for(sid, "checklist-verdict", d))
                assert got == E.paper_cli_render(paper, "verdict"), d
                compared += 1
    assert compared >= 16  # 24 stubs minus those the documented v1 deltas regrade


@pytest.mark.parametrize("sid", D.SCENARIOS)
def test_verdict_and_figures_modes_on_stubs(tmp_path, sid):
    stubs.render(sid, "medium", tmp_path, "v1")
    v = qa_check({}, ctx_for(sid, "checklist-verdict", tmp_path))
    f = qa_check({}, ctx_for(sid, "checklist-executable", tmp_path))
    assert v.startswith("CHECKLIST RESULT: ") and "measured:" not in v and "criteria:" not in v
    assert "measured:" in f and "criteria:" in f
    pf = [ln for ln in v.splitlines() if ln.startswith("  PASS") or ln.startswith("  FAIL")]
    ff = [ln for ln in f.splitlines() if ln.startswith("  PASS") or ln.startswith("  FAIL")]
    assert pf == ff and len(pf) == sum(1 for c in D.registry(sid, "v1") if c.get("auto"))
    assert '"band"' not in f and "context_regex" not in f


def test_mode_is_sealed_payload_ignored(tmp_path):
    stubs.render("meridian-soc2", "medium", tmp_path, "v1")
    ctx = ctx_for("meridian-soc2", "checklist-verdict", tmp_path)
    a = qa_check({}, ctx)
    b = qa_check({"mode": "figures", "registry": "x", "path": "/etc"}, ctx)
    assert a == b and "measured:" not in b
    with pytest.raises(RuntimeError):
        qa_check({}, ctx_for("meridian-soc2", "checklist-verdict", tmp_path, mode="prose"))
    assert ctx.state["qa_check"]["calls"] == 2


def test_symlinked_deliverable_is_not_read(tmp_path):
    good = tmp_path / "good"
    stubs.render("castellan-software", "strong", good, "v1")
    sb = tmp_path / "sb"
    sb.mkdir()
    for f in good.iterdir():
        os.symlink(f, sb / f.name)
    out = qa_check({}, ctx_for("castellan-software", "checklist-executable", sb))
    assert "CHECKLIST RESULT: 0/16 PASS" in out
    assert "was not read (symlink)" in out


def test_handlers_per_arm():
    for arm, ops in (("baseline", {}), ("checklist-prose", {}),
                     ("checklist-verdict", {"qa_check"}), ("checklist-executable", {"qa_check"})):
        assert set(EVAL.oracle_handlers(cell("bluestem-demand", arm))) == set(ops)


def test_core_oracle_server_round_trip(tmp_path):
    """The handler behind the real core server: sealed mode, override logged, text result."""
    from dbwm.core.oracle import client
    from dbwm.core.oracle.server import OracleServer
    from dbwm.eval5.arms import ARMS

    sb = tmp_path / "sb"
    stubs.render("solvara-market-access", "medium", sb, "v1")
    c = cell("solvara-market-access", "checklist-verdict")
    ctx = ctx_for("solvara-market-access", "checklist-verdict", sb)
    token = secrets.token_hex(16)
    srv = OracleServer(EVAL.oracle_handlers(c), ctx, token, tmp_path / "oracle_calls.jsonl",
                       specs=ARMS["checklist-verdict"].tools.oracle_tools)
    with srv:
        out = client.call(srv.address, token, "qa_check", {"mode": "figures"})
    assert isinstance(out, str) and out.startswith("CHECKLIST RESULT:") and "measured:" not in out
    log = [json.loads(x) for x in (tmp_path / "oracle_calls.jsonl").read_text().splitlines() if x.strip()]
    assert log and log[-1]["op"] == "qa_check" and log[-1]["override_attempt"]
