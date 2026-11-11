"""The core audit flagging a reach into private/ in a synthetic eval-3 stream-json transcript."""

from __future__ import annotations

import json

from dbwm.core.types import Cell, Isolation
from dbwm.eval3 import EVAL

SB = "/private/var/folders/xx/T/bwm_gen_gen-figures_abc"


def _write(path, tool_uses, results=None, result=True, cwd=SB):
    ev = [{"type": "system", "subtype": "init", "cwd": cwd, "model": "claude-x", "tools": ["Bash", "Read", "Write"]}]
    for i, (name, inp) in enumerate(tool_uses):
        ev.append(
            {
                "type": "assistant",
                "message": {
                    "id": f"m{i}",
                    "content": [{"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}],
                },
            }
        )
        out = (results or {}).get(i, "ok")
        ev.append(
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": out}]}}
        )
    if result:
        ev.append({"type": "result", "subtype": "success", "num_turns": len(tool_uses) + 1, "total_cost_usd": 0.5})
    path.write_text("\n".join(json.dumps(e) for e in ev) + "\n")
    return path


def test_core_audit_flags_a_reach_into_private(tmp_path):
    """The advisor's leak case: a gen-arm transcript that cats ../private/eval2/worlds/<w>/reference.json."""
    from dbwm.core import audit as core_audit

    cell = Cell("eval3", "orion-holdings", "gen-code", 0)
    adir = tmp_path / "attempt-1"
    (adir / "deliverables").mkdir(parents=True)
    sb = tmp_path / "sandbox"
    sb.mkdir()
    _write(
        adir / "transcript.jsonl",
        [
            ("Bash", {"command": "cat ../private/eval2/worlds/orion-holdings/reference.json"}),
        ],
        cwd=str(sb),
    )
    a = core_audit.audit_attempt(
        adir,
        EVAL,
        cell,
        sandbox=str(sb),
        isolation=Isolation.CONTAINER,
        expected_tools=["Bash", "Read", "Write"],
        answer_key_values=[],
        write=False,
    )
    assert a["flagged"] is True and core_audit.F_SCORER_ONLY in a["reasons"]
    hit = next(f for f in a["findings"] if f["reason"] == core_audit.F_SCORER_ONLY)
    assert {"private", "reference.json"} <= set(hit["detail"]["names"])
