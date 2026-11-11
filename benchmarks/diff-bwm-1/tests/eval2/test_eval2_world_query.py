"""world_query oracle: goldens vs the paper tool, the v1 fixes, the sealed mode."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from dbwm.core import paths
from dbwm.core.types import Cell, OracleContext
from dbwm.eval2 import ARMS, EVAL, WORLD_IDS, load_reference, load_world, load_world_spec
from dbwm.eval2.world_query import MODES, handle_world_query, render, render_paper_text

# Candidate allocations per world, in and outside the v1 action space, and the paper tool's stdout on each
# (world_query_golden.json, captured by running the paper's world_query.py on these candidates).
FIXTURES = Path(__file__).parent / "fixtures"
CANDIDATES = json.loads((FIXTURES / "world_query_candidates.json").read_text())


@pytest.fixture(scope="module")
def golden():
    return json.loads((FIXTURES / "world_query_golden.json").read_text())


def ctx_for(world_id: str, arm: str, sandbox: Path, mode: str | None = None) -> OracleContext:
    a = ARMS[arm]
    return OracleContext(cell=Cell("eval2", world_id, arm, 0), arm=a,
                         mode=a.oracle_mode if mode is None else mode, sandbox=sandbox, state={})


def test_golden_fixture_covers_every_world_mode_and_candidate(golden):
    assert sorted(golden["worlds"]) == sorted(WORLD_IDS)
    for wid, entry in golden["worlds"].items():
        assert len(entry["outputs"]) == len(entry["candidates"]) * len(MODES)
        assert {k: json.loads(v) for k, v in entry["candidates"].items() if k != "invalid_json"} == CANDIDATES[wid]


@pytest.mark.parametrize("wid", WORLD_IDS)
def test_paper_ruleset_equals_captured_paper_tool_stdout(wid, golden):
    entry = golden["worlds"][wid]
    spec = load_world_spec(wid)
    for key, rec in entry["outputs"].items():
        cname, mode = key.split("|")
        text, rc = render_paper_text(spec, entry["candidates"][cname], mode)
        assert rc == rec["rc"], key
        assert hashlib.sha256(text.encode()).hexdigest() == rec["sha256"], key
        if "stdout" in rec:
            assert text == rec["stdout"], key


@pytest.mark.parametrize("wid", WORLD_IDS)
def test_v1_boolean_is_the_whitelist_with_no_figures(wid):
    world = load_world(wid)
    for cname, cand in CANDIDATES[wid].items():
        out = render(world, cand, "boolean", ruleset="v1")
        if out.get("ok") is False:
            assert set(out) == {"ok", "error"}
            continue
        assert set(out) == {"ok", "goal", "constraints", "violations"}
        assert set(out["goal"]) == {"met"}
        assert all(set(c) == {"id", "ok"} for c in out["constraints"])
        # no number of any kind (the paper leaked actual_multiple, limit/actual, worst_quarter)
        assert not re.search(r"\d", json.dumps(out)), (cname, out)
        paper = render(world, cand, "boolean", ruleset="paper")
        assert paper["goal"]["met"] == out["goal"]["met"]
        assert [c["ok"] for c in paper["constraints"]] == [c["ok"] for c in out["constraints"]]
        assert "actual_multiple" in paper["goal"]  # the leak, kept only in the paper ruleset


@pytest.mark.parametrize("mode", ["localizing", "raw", "prose", "broken"])
def test_v1_equals_paper_for_in_space_candidates(mode):
    wid = "orion-holdings"
    world = load_world(wid, interface="broken" if mode == "broken" else None)
    for cname, cand in CANDIDATES[wid].items():
        v1 = render(world, cand, mode, ruleset="v1")
        paper = render(world, cand, mode, ruleset="paper")
        if cname in ("omit_unit", "flat90_layoffs", "wrong_length"):
            assert v1["ok"] is False and "outside the allowed action space" in v1["error"], cname
        else:
            assert v1 == paper, cname


def test_v1_refuses_out_of_space_candidates_naming_the_rule():
    world = load_world("orion-holdings")
    cands = CANDIDATES["orion-holdings"]
    out = render(world, cands["omit_unit"], "localizing")
    assert out["ok"] is False and "missing_unit" in out["error"] and "every unit must appear" in out["error"]
    out = render(world, cands["flat90_layoffs"], "raw")
    assert out["ok"] is False and "layoff" in out["error"]


def test_handler_mode_comes_from_ctx_only(tmp_path):
    cand = load_reference("lyra-partners")["careful_optimum"]["allocation"]
    (tmp_path / "candidate.json").write_text(json.dumps({"allocation": cand}))
    ctx = ctx_for("lyra-partners", "grounded-boolean", tmp_path)
    plain = handle_world_query({"file": "candidate.json"}, ctx)
    for key in ("mode", "channel", "backend", "world", "path"):
        tried = handle_world_query({"file": "candidate.json", key: "localizing"}, ctx)
        assert tried == plain
    assert set(plain) == {"ok", "goal", "constraints", "violations"}
    assert ctx.mode == "boolean"
    # inline candidate gives the same answer
    assert handle_world_query({"candidate": {"allocation": cand}}, ctx) == plain
    assert handle_world_query({"candidate": json.dumps({"allocation": cand})}, ctx) == plain


@pytest.mark.parametrize("arm", ["grounded-readonly", "grounded-iterate", "grounded-boolean",
                                 "raw-figures", "prose-feedback", "broken-interface"])
def test_each_arm_gets_its_channel(arm, tmp_path):
    wid = "perseus-portfolio"
    cand = {"allocation": load_reference(wid)["careful_optimum"]["allocation"]}
    (tmp_path / "c.json").write_text(json.dumps(cand))
    out = EVAL.oracle_handlers(Cell("eval2", wid, arm, 0))["world_query"]({"file": "c.json"},
                                                                           ctx_for(wid, arm, tmp_path))
    mode = ARMS[arm].oracle_mode
    if mode == "prose":
        assert isinstance(out, str) and "Figures withheld" in out and not re.search(r"\d{4,}", out)
    elif mode == "raw":
        assert out["mode"] == "raw" and "goal" not in out and "violations" not in out
    elif mode == "boolean":
        assert set(out) == {"ok", "goal", "constraints", "violations"}
    else:
        assert out["mode"] == mode and "parent_trajectory" in out and "goal" in out
        if mode == "broken":
            assert "_warning" in out
    assert EVAL.oracle_handlers(Cell("eval2", wid, "ungrounded", 0)) == {}


def test_handler_refuses_paths_outside_the_sandbox(tmp_path):
    ctx = ctx_for("vega-group", "grounded-iterate", tmp_path)
    secret = paths.private("eval2", "worlds", "vega-group", "reference.json")
    for bad in ("../x.json", str(secret), "/etc/passwd", "", "nope.json"):
        out = handle_world_query({"file": bad}, ctx)
        assert out["ok"] is False
        assert "reference" not in out["error"] and str(paths.PKG_ROOT) not in out["error"]
    (tmp_path / "link.json").symlink_to(secret)
    out = handle_world_query({"file": "link.json"}, ctx)
    assert out["ok"] is False
    (tmp_path / "bad.json").write_text("{not json")
    out = handle_world_query({"file": "bad.json"}, ctx)
    assert out["ok"] is False and out["error"].startswith("allocation is not valid JSON")
    assert handle_world_query({}, ctx)["ok"] is False


def test_handler_rejects_a_misconfigured_mode(tmp_path):
    ctx = ctx_for("vega-group", "grounded-iterate", tmp_path, mode="numeric")
    with pytest.raises(ValueError):
        handle_world_query({"candidate": {}}, ctx)


def test_tool_description_is_neutral():
    from dbwm.eval2 import WORLD_QUERY_TOOL as T

    blob = json.dumps({"d": T.description, "s": T.input_schema, "u": T.cli_usage}).lower()
    for word in ("mode", "modes", "reference", "optimum", "boolean", "localizing", "prose", "raw",
                 "broken", "world.json", "hint"):
        assert not re.search(r"\b" + re.escape(word) + r"\b", blob), word
    assert "/users" not in blob and "private" not in blob
    assert set(T.input_schema["properties"]) == {"file", "candidate"}


def _serve(cell, sandbox, log):
    from dbwm.core.oracle.server import OracleServer

    arm = ARMS[cell.arm]
    ctx = OracleContext(cell=cell, arm=arm, mode=arm.oracle_mode, sandbox=sandbox, state={})
    return OracleServer(EVAL.oracle_handlers(cell), ctx, "t" * 32, log,
                        query_budget=arm.query_budget, specs=arm.tools.oracle_tools)


def test_through_the_core_oracle_server_mode_is_sealed_and_overrides_logged(tmp_path):
    from dbwm.core.oracle.client import call

    cell = Cell("eval2", "orion-holdings", "grounded-boolean", 0)
    cand = {"allocation": load_reference("orion-holdings")["careful_optimum"]["allocation"]}
    (tmp_path / "candidate.json").write_text(json.dumps(cand))
    log = tmp_path / "logs" / "oracle_calls.jsonl"
    with _serve(cell, tmp_path, log) as srv:
        plain = call(srv.address, "t" * 32, "world_query", {"file": "candidate.json"})
        forced = call(srv.address, "t" * 32, "world_query", {"file": "candidate.json", "mode": "localizing"})
    assert plain == forced == {"ok": True, "goal": {"met": True},
                               "constraints": plain["constraints"], "violations": []}
    recs = [json.loads(l) for l in log.read_text().splitlines()]
    assert [r["override_attempt"] for r in recs] == [False, True]
    assert recs[1]["override_keys"] == ["mode"]


def test_through_the_core_oracle_server_readonly_budget_is_enforced(tmp_path):
    from dbwm.core.oracle.client import OracleError, call

    cell = Cell("eval2", "vega-group", "grounded-readonly", 0)
    cand = {"allocation": load_reference("vega-group")["careful_optimum"]["allocation"]}
    with _serve(cell, tmp_path, tmp_path / "oracle_calls.jsonl") as srv:
        for _ in range(3):
            out = call(srv.address, "t" * 32, "world_query", {"candidate": cand})
            assert out["mode"] == "localizing"
        with pytest.raises(OracleError) as ei:
            call(srv.address, "t" * 32, "world_query", {"candidate": cand})
        assert ei.value.calls_remaining == 0


def test_stub_through_the_core_oracle_server_matches_direct_wiring(tmp_path):
    """The stub agent driven over TCP gives the same deliverables as the direct wiring."""
    import shutil

    from dbwm.core.oracle.client import make_caller
    from dbwm.eval2.selftest import run_stub_direct

    for arm in ("grounded-readonly", "raw-figures", "prose-feedback"):
        cell = Cell("eval2", "lyra-partners", arm, 2)
        a, b = tmp_path / arm / "tcp", tmp_path / arm / "direct"
        for d in (a, b):
            d.mkdir(parents=True)
            for f in EVAL.stage(cell).files:
                shutil.copy(f.src, d / f.dst)
        with _serve(cell, a, tmp_path / arm / "calls.jsonl") as srv:
            EVAL.stub_agent(cell, a, make_caller(srv.address, "t" * 32))
        run_stub_direct(cell, b)
        for name in ("allocation.json", "claims.json"):
            assert (a / name).read_bytes() == (b / name).read_bytes(), (arm, name)
