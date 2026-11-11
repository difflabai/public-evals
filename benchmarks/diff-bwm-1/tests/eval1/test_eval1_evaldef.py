"""Eval-1 EvalDef contract: protocol, provenance hooks, audit hooks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dbwm.core.types import Cell, CheckResult, EvalDef
from dbwm.eval1 import ENGINE_SOURCES, SCORER_ONLY_NAMES, engine_hash
from dbwm.eval1 import selftest as ST


def test_protocol(ev):
    assert isinstance(ev, EvalDef)
    assert ev.eval_id == "eval1" and ev.title and tuple(ev.deliverables) == ("plan.json", "claims.json")
    for name in ("reference.json", "world.json", "ANSWER_KEY", "checks.jsonl", "answer_key", "grader", "private"):
        assert name in SCORER_ONLY_NAMES  # the core defaults are kept
    assert {"operating-model.json", "eval-tiers.json", "calibration", "bwm_query.py"} <= set(SCORER_ONLY_NAMES)


def test_engine_hash_stable():
    h = engine_hash()
    assert len(h) == 64 and h == engine_hash()
    assert all(Path(p).is_file() for p in ENGINE_SOURCES)


def test_selftest_has_no_silent_skips(ev):
    checks = ev.selftest()
    assert checks and all(isinstance(c, CheckResult) for c in checks)
    assert all(c.ok for c in checks), [(c.name, c.detail) for c in checks if not c.ok]
    skipped = [c for c in checks if c.skipped]
    assert not skipped, [c.name for c in skipped]
    msg = " ".join(c.detail for c in checks)
    assert "live" not in msg.lower()  # no "reproduces the live bwm engine" overclaim


def test_selftest_fails_when_calibration_missing(ev, tmp_path, monkeypatch):
    """A missing fixture is a FAILED check, never a skip."""
    import shutil

    from dbwm.eval1 import _paths

    priv = tmp_path / "private"
    shutil.copytree(_paths.private(), priv / "eval1")
    (priv / "eval1" / "calibration" / "report.json").unlink()
    monkeypatch.setenv("DBWM_PRIVATE_DIR", str(priv))
    c = ST.check_calibration()
    assert not c.ok and not c.skipped and "missing" in c.detail


def _h(obj):
    canon = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode()).hexdigest()


def test_duplicate_corpus_covers_the_private_careful_plan(ev, calibration):
    corpus = set(ev.duplicate_corpus(Cell("eval1", "northwind-analytics__hostile", "ungrounded", 0)))
    # the private careful plan is a fingerprint, in both shapes
    careful = calibration["northwind-analytics"]["reference_plans"]["careful"]
    assert _h(careful) in corpus and _h({"bindings": careful}) in corpus
    # a trivial one-lever flat plan is never a fingerprint
    trivial = {"bindings": {"acv": ["9000"] * 6}}
    canon = json.dumps(trivial, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canon.encode()).hexdigest() not in corpus
    audit = pytest.importorskip("dbwm.core.audit")
    assert hashlib.sha256(canon.encode()).hexdigest() == audit.canonical_sha256(_write(trivial))


def _write(obj) -> Path:
    import tempfile

    d = Path(tempfile.mkdtemp(prefix="dbwm_e1_"))
    p = d / "plan.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p
