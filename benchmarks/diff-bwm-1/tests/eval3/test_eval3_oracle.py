"""The model_eval / model_test oracle handlers: sealed mode, payloads, sandbox confinement, the real server."""

from __future__ import annotations

import json
import os
import re
import secrets

import pytest

from dbwm.core.types import Cell, OracleContext
from dbwm.eval2 import load_brief
from dbwm.eval3 import EVAL
from dbwm.eval3.fixtures import faithful_model
from dbwm.eval3.oracle import (
    MODEL_EVAL_TOOL,
    MODEL_TEST_TOOL,
    handle_model_eval,
    handle_model_test,
    render,
)
from dbwm.eval3.task import load_task


def _model(world="orion-holdings", hires="1"):
    b = load_brief(world)
    uids = [u["id"] for u in b["units"]]
    return faithful_model(b, {u: ["50000"] * 6 for u in uids}, {u: [hires] * 6 for u in uids})


def _ctx(arm, sandbox=None, world="orion-holdings"):
    cell = Cell("eval3", world, arm, 0)
    a = EVAL.arms[arm]
    return OracleContext(cell=cell, arm=a, mode=a.oracle_mode, sandbox=sandbox, state={})


def test_model_eval_inline_and_file_equal_v1_render(tmp_path):
    m = _model()
    ctx = _ctx("gen-figures", tmp_path)
    inline = handle_model_eval({"model": m}, ctx)
    (tmp_path / "model.json").write_text(json.dumps(m))
    from_file = handle_model_eval({"file": "model.json"}, ctx)
    as_string = handle_model_eval({"model": json.dumps(m)}, ctx)
    assert inline == from_file == as_string == render(m, "model_eval", "v1")
    assert inline["ok"] is True and inline["mode"] == "eval" and "goal" not in inline and "violations" not in inline
    assert ctx.state["model_eval_calls"] == 3


def test_model_eval_ignores_a_mode_in_the_payload():
    """The server strips and logs mode-like keys; even if one got through, the handler never reads it."""
    out = handle_model_eval({"model": _model(), "mode": "test", "task": "task.json"}, _ctx("gen-figures"))
    assert out["mode"] == "eval" and "goal" not in out


def test_model_test_is_sealed_off_in_gen_figures():
    with pytest.raises(ValueError, match="sealed off"):
        handle_model_test({"model": _model()}, _ctx("gen-figures"))
    assert set(EVAL.oracle_handlers(Cell("eval3", "orion-holdings", "gen-figures", 0))) == {"model_eval"}


def test_model_test_uses_the_shipped_task_not_the_payload_or_sandbox(tmp_path):
    m = _model()
    ctx = _ctx("gen-both", tmp_path)
    (tmp_path / "model.json").write_text(json.dumps(m))
    easy = load_task("orion-holdings")
    easy["goal"]["target_multiple"] = "0.5"
    (tmp_path / "task.json").write_text(json.dumps(easy))  # the agent edits its copy: no effect (V1)
    out = handle_model_test({"file": "model.json", "task": easy}, ctx)
    assert out == render(m, "model_test", "v1", task=load_task("orion-holdings"))
    assert out["goal"]["target_multiple"] == "2.33"
    assert ctx.state["model_test_calls"] == 1


def test_model_test_reports_the_action_space_under_v1():
    out = handle_model_test({"model": _model(hires="-1")}, _ctx("gen-both"))
    act = out["constraints"][-1]
    assert act["id"] == "action_space" and act["ok"] is False and "action_space" in out["violations"]
    assert act["reasons"][0].startswith("layoff:hires:")


@pytest.mark.parametrize(
    "payload,needle",
    [
        ({}, 'pass your model as {"file": "model.json"}'),
        ({"file": "model.json", "model": {}}, "not both"),
        ({"file": "/etc/passwd"}, "inside the working directory"),
        ({"file": "../outside.json"}, "inside the working directory"),
        ({"file": "missing.json"}, "evaluation failed: [Errno 2] No such file or directory: 'missing.json'"),
        ({"file": 7}, "relative path"),
        ({"model": "{not json"}, "evaluation failed:"),
    ],
)
def test_payload_errors_are_agent_safe(tmp_path, payload, needle):
    out = handle_model_eval(payload, _ctx("gen-figures", tmp_path))
    assert out["ok"] is False and needle in out["error"]
    assert str(tmp_path) not in out["error"] and "/" + "Users/" not in out["error"]


def test_symlink_escape_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "m.json").write_text(json.dumps(_model()))
    sb = tmp_path / "sb"
    sb.mkdir()
    os.symlink(outside / "m.json", sb / "model.json")
    out = handle_model_eval({"file": "model.json"}, _ctx("gen-figures", sb))
    assert out["ok"] is False and "inside the working directory" in out["error"]


def test_tool_specs_are_neutral():
    for spec in (MODEL_EVAL_TOOL, MODEL_TEST_TOOL):
        text = spec.description + json.dumps(spec.input_schema)
        assert not re.search(r"\bmodes?\b|--mode", text), spec.name  # no channel is named or offered
        for bad in ("model_tool.py", "reference", "world.json", "oracle server", "/" + "Users", "/tmp"):
            assert bad not in text, (spec.name, bad)
        assert set(spec.input_schema["properties"]) == {"file", "model"}
        assert spec.input_schema["additionalProperties"] is False
    assert MODEL_EVAL_TOOL.name == "model_eval" and MODEL_TEST_TOOL.name == "model_test"


def test_real_oracle_server_round_trip_and_override_logging(tmp_path):
    from dbwm.core.oracle.client import OracleError, call
    from dbwm.core.oracle.server import OracleServer

    m = _model()
    (tmp_path / "model.json").write_text(json.dumps(m))
    for arm, ops in (("gen-figures", {"model_eval"}), ("gen-both", {"model_eval", "model_test"})):
        cell = Cell("eval3", "orion-holdings", arm, 0)
        a = EVAL.arms[arm]
        ctx = OracleContext(cell=cell, arm=a, mode=a.oracle_mode, sandbox=tmp_path, state={})
        token = secrets.token_urlsafe(24)
        log = tmp_path / f"{arm}.oracle_calls.jsonl"
        server = OracleServer(
            EVAL.oracle_handlers(cell),
            ctx,
            token,
            log,
            a.query_budget,
            specs=a.tools.oracle_tools,
            scrub_paths=[str(tmp_path)],
        )
        addr = server.start()
        try:
            out = call(addr, token, "model_eval", {"file": "model.json", "mode": "test"})
            assert out == render(m, "model_eval", "v1")
            if "model_test" in ops:
                t = call(addr, token, "model_test", {"file": "model.json"})
                assert t["mode"] == "test" and t["goal"]["target_multiple"] == "2.33"
            else:
                with pytest.raises(OracleError):
                    call(addr, token, "model_test", {"file": "model.json"})
        finally:
            server.stop()
        rows = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
        assert rows[0]["op"] == "model_eval" and rows[0]["override_attempt"]
