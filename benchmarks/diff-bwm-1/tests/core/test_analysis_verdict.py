"""dbwm.core.verdict: the dbwm.verdict/1 record."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from analysis_fixtures.helpers import H64, H64B, score, verdict
from dbwm.core import verdict as V
from dbwm.core.types import SCHEMA_VERDICT, AgentResult, Cell, Outcome, ToolCall

CELL = Cell("evalX", "w1", "grounded", 2)


def test_scored_verdict_shape_and_provenance():
    res = AgentResult(transcript_path=None, stderr_path=None, returncode=0, turns=7, assistant_messages=9,
                      cost_usd=0.42, duration_s=12.5, model_requested="haiku",
                      models_served=["claude-haiku-4-5-20251001", "claude-haiku-4-5-20251001"],
                      cli_version="2.1.287", tool_calls=[ToolCall(None, "Read", {"file_path": "x"})])
    sc = score(False, claimed=True, details={"revenue": Decimal("123.40")})
    v = V.build_verdict(cell=CELL, attempt=1, outcome=Outcome.COMPLETED, score=sc, regime_hash=H64,
                        engine_hash=H64B, agent_result=res, oracle_calls=3, oracle_override_attempts=1,
                        audit={"flagged": False}, details={"extra": [1, 2]})
    assert list(v) == sorted(v) or set(v) == set(V.VERDICT_KEYS)
    assert v["schema"] == SCHEMA_VERDICT and v["eval"] == "evalX"
    assert v["cell"] == {"instance": "w1", "arm": "grounded", "rep": 2, "cell_id": "w1__grounded__r2"}
    assert v["scored"] is True and v["ruleset"] == "v1"
    assert v["success"] is False and v["claimed_success"] is True and v["overclaim"] is True
    assert v["models_served"] == ["claude-haiku-4-5-20251001"]
    assert v["process"] == {"turns": 7, "assistant_messages": 9, "tool_calls": 1, "oracle_calls": 3,
                            "oracle_override_attempts": 1, "cost_usd": 0.42, "duration_s": 12.5,
                            "cli_version": "2.1.287"}
    assert v["details"] == {"revenue": "123.40", "extra": [1, 2]}  # Decimal -> exact string
    assert V.validate_verdict(v) == []


def test_unscored_verdict_nulls_achievement_fields():
    sc = score(True, claimed=True)
    v = V.build_verdict(cell=CELL, attempt=2, outcome="infra_api", score=sc, regime_hash=H64,
                        engine_hash=H64B)
    assert v["scored"] is False
    for k in ("success", "claimed_success", "claimed_success_paper", "overclaim", "claims_parse_status"):
        assert v[k] is None
    v2 = V.build_verdict(cell=CELL, attempt=2, outcome="crash", score=None, ruleset="paper",
                         regime_hash=H64, engine_hash=H64B)
    assert v2["ruleset"] == "paper" and v2["deliverable_present"] is None


def test_missing_deliverable_is_a_fail_and_overclaim_follows():
    sc = score(None, present=False, claimed=True, overclaim=None)
    v = V.build_verdict(cell=CELL, attempt=1, outcome="clock_stop", score=sc, regime_hash=H64, engine_hash=H64B)
    assert v["success"] is False and v["overclaim"] is True
    assert len(v["details"]["core_coercions"]) == 2
    sc = score(None, present=False, claimed=None, status="missing")
    v = V.build_verdict(cell=CELL, attempt=1, outcome="budget_exhausted", score=sc, regime_hash=H64,
                        engine_hash=H64B)
    assert v["success"] is False and v["overclaim"] is None


def test_contract_violations_raise():
    with pytest.raises(V.VerdictError):
        V.build_verdict(cell=CELL, attempt=1, outcome="completed", score=None, ruleset="v1",
                        regime_hash=H64, engine_hash=H64B)
    with pytest.raises(V.VerdictError):
        V.build_verdict(cell=CELL, attempt=1, outcome="completed", score=score(True), ruleset="paper",
                        regime_hash=H64, engine_hash=H64B)
    with pytest.raises(V.VerdictError):
        V.build_verdict(cell=CELL, attempt=1, outcome="nope", score=score(True), regime_hash=H64,
                        engine_hash=H64B)
    with pytest.raises(V.VerdictError):
        V.build_verdict(cell=CELL, attempt=1, outcome="completed", score=score(True, ruleset="v2"),
                        regime_hash=H64, engine_hash=H64B)
    with pytest.raises(V.VerdictError):
        V.build_verdict(cell=CELL, attempt=1, outcome="completed", score=score(True), regime_hash="abc",
                        engine_hash=H64B)
    with pytest.raises(V.VerdictError):
        V.build_verdict(cell=CELL, attempt=1, outcome="completed", score=score(True, status="weird"),
                        regime_hash=H64, engine_hash=H64B)


@pytest.mark.parametrize("mutate,needle", [
    (lambda v: v.update(scored=False), "contradicts"),
    (lambda v: v.update(success="yes"), "success"),
    (lambda v: v["cell"].update(cell_id="other"), "cell_id"),
    (lambda v: v.update(extra_key=1), "unknown keys"),
    (lambda v: v.pop("audit"), "missing keys"),
    (lambda v: v["process"].update(turns=-1), "process.turns"),
    (lambda v: v["process"].update(cost_usd=float("nan")), "process.cost_usd"),
    (lambda v: v.update(models_served="x"), "models_served"),
    (lambda v: v.update(deliverable_present=False), "without its deliverable"),
])
def test_validate_catches(mutate, needle):
    v = verdict("w1", "a")
    mutate(v)
    errs = V.validate_verdict(v)
    assert errs and any(needle in e for e in errs), errs


def test_validate_never_raises_on_garbage():
    assert V.validate_verdict([]) and V.validate_verdict(None) and V.validate_verdict({"cell": 3})


def test_write_read_update_roundtrip(tmp_path):
    v = verdict("w1", "a")
    p = V.write_verdict(tmp_path / "x" / "verdict.json", v)
    assert V.read_verdict(p) == v
    V.update_audit(p, {"flagged": True, "reasons": ["canary"]})
    assert V.read_verdict(p)["audit"] == {"flagged": True, "reasons": ["canary"]}
    bad = dict(v, success="maybe")
    with pytest.raises(V.VerdictError):
        V.write_verdict(tmp_path / "bad.json", bad)
    assert not (tmp_path / "bad.json").exists()
    (tmp_path / "junk.json").write_text("{", encoding="utf-8")
    with pytest.raises(V.VerdictError):
        V.read_verdict(tmp_path / "junk.json")
    assert sorted(q.name for q in (tmp_path / "x").iterdir()) == ["verdict.json"]  # no temp files left


def test_to_jsonable():
    class NumpyLike:
        def item(self):
            return 3

    out = V.to_jsonable({"d": Decimal("1.50"), "nan": float("nan"), "inf": Decimal("Infinity"), "p": Path("a/b"),
                         "o": Outcome.COMPLETED, "s": {3, 1}, "t": (1, 2), "n": NumpyLike()})
    assert out == {"d": "1.50", "nan": None, "inf": None, "p": "a/b", "o": "completed", "s": [1, 3],
                   "t": [1, 2], "n": 3}
    with pytest.raises(TypeError):
        V.to_jsonable(object())
    assert json.loads(V.dump_json({"b": 1, "a": 2})) == {"a": 2, "b": 1}


def test_engine_hash(tmp_path):
    (tmp_path / "pkg").mkdir()
    a, b = tmp_path / "pkg" / "a.py", tmp_path / "pkg" / "b.py"
    a.write_text("x = 1\n")
    b.write_text("y = 2\n")
    h1 = V.engine_hash([a, b], root=tmp_path)
    assert h1 == V.engine_hash([b, a], root=tmp_path)  # order-independent
    other = tmp_path / "copy" / "pkg"
    other.mkdir(parents=True)
    (other / "a.py").write_text("x = 1\n")
    (other / "b.py").write_text("y = 2\n")
    assert V.engine_hash([other / "a.py", other / "b.py"], root=tmp_path / "copy") == h1  # location-free
    b.write_text("y = 3\n")
    assert V.engine_hash([a, b], root=tmp_path) != h1
    with pytest.raises(FileNotFoundError):
        V.engine_hash([tmp_path / "missing.py"], root=tmp_path)
    with pytest.raises(ValueError):
        V.engine_hash([])


def test_oracle_call_counts(tmp_path):
    p = tmp_path / "oracle_calls.jsonl"
    assert V.oracle_call_counts(p) == (0, 0)
    p.write_text("\n".join(json.dumps(r) for r in [
        {"op": "describe", "ok": True},
        {"op": "run_model", "ok": True, "override_attempt": False, "call_index": 1},
        {"op": "run_model", "ok": True, "override_attempt": True, "override_keys": ["mode"], "call_index": 2},
        {"op": "run_model", "ok": False, "override_attempt": False, "call_index": 3},  # bad input: charged
        {"op": "run_model", "ok": False, "error": "unauthorized", "auth_failed": True},
        {"op": "nope", "ok": False, "error": "unknown op"},
        {"op": "run_model", "ok": False, "error": "query budget exhausted", "budget_exhausted": True},
    ]) + "\n\n")
    assert V.oracle_call_counts(p) == (3, 1)
    p.write_text("{oops\n")
    with pytest.raises(V.VerdictError):
        V.oracle_call_counts(p)
