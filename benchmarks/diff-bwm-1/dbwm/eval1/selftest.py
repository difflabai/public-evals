"""``dbwm selftest --eval 1``: offline checks of the eval-1 instrument (no LLM, no network).

  calibration   the calibration matrix (base/naive/careful x soft/hard/hostile, 90
                cells) and the goal and tier semantics, re-derived with the engine
  briefs        the 60 task briefs regenerate byte-identically from the worlds; the 15
                default-instance briefs match their recorded SHA-256 prefixes
  generator     gen/ regenerates the 10 worlds and the calibration report byte-identically
  oracle modes  run_model serves every sealed mode; payload keys cannot change it
  stub agent    the stub agent drives every arm end to end and is scored

A missing fixture is a FAILED check, never a skip.
"""

from __future__ import annotations

import hashlib
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from dbwm.core.types import RULESET_V1, Cell, CheckResult

from . import _paths
from .engine import check_constraints, evaluate, goal_multiple, load_calibration, load_model_and_tiers, prec50
from .score import TIERS

# SHA-256 prefixes of the 15 used briefs (advisor spec; scratch run 2026-10-01).
USED_BRIEF_SHA_PREFIXES = {
    "atlas-advisory": ("915ab211", "24d469d6", "a25ce1d1"),
    "gigbridge-marketplace": ("61bc8053", "8ecfbf48", "a15ad6d1"),
    "northwind-analytics": ("8d470e5c", "00051ec1", "d0ba5b35"),
    "sprout-crm": ("e0f89471", "4fc89f22", "96b80dfa"),
    "voltcore-devices": ("585b85e8", "c541f7c3", "19bfbd7b"),
}


@prec50
def rederive_calibration() -> dict[str, Any]:
    """Re-derive base/naive/careful x soft/hard/hostile violations + goal semantics.

    The paper's oracle selftest minus the private live-engine anchor.
    A regression check of the engine against the frozen calibration report;
    both came from the same evaluator code, so it is not an independent
    validation of the engine (the Python engine is normative).
    """
    report = load_calibration()
    cells, problems = {}, []
    for entry in report:
        mid = entry["model_id"]
        model, tiers_doc = load_model_and_tiers(mid)
        refs = entry["reference_plans"]
        plans = {"base": {}, "naive": refs["naive"], "careful": refs["careful"]}
        tgt = Decimal(str(tiers_doc["goal"]["target_multiple"]))
        per = {}
        for name, bindings in plans.items():
            periods = evaluate(model, bindings)
            m = goal_multiple(periods, tiers_doc["goal"])
            reached = m is not None and m >= tgt
            for tier_name, tier in tiers_doc["tiers"].items():
                got = sorted(d["id"] for d in check_constraints(model, periods, bindings, tier) if not d["ok"])
                exp = sorted(entry["violation_matrix"][f"{name}/{tier_name}"])
                per[f"{name}/{tier_name}"] = got
                if got != exp:
                    problems.append(f"{mid} {name}/{tier_name}: got {got}, expected {exp}")
            per[f"{name}/goal_reached"] = reached
            if reached != (name != "base"):
                problems.append(f"{mid} {name}: goal reached={reached}")
            for tier_name, tier in tiers_doc["tiers"].items():
                fv = reached and not per[f"{name}/{tier_name}"]
                want = {"base": False, "naive": tier_name == "soft", "careful": True}[name]
                if fv != want:
                    problems.append(f"{mid} {name}/{tier_name}: fully_valid={fv}, tier semantics want {want}")
        cells[mid] = per
    return {"companies": len(report), "constraint_cells": 9 * len(report), "matrix": cells,
            "problems": problems}


def check_calibration() -> CheckResult:
    try:
        res = rederive_calibration()
    except FileNotFoundError as e:
        return CheckResult("eval1/calibration matrix re-derived", False, f"calibration report missing: {e}")
    return CheckResult("eval1/calibration matrix re-derived (90 cells + goal and tier semantics)",
                       not res["problems"] and res["constraint_cells"] == 90,
                       f"re-derived {res['companies']} companies x 3 plans x 3 tiers = "
                       f"{res['constraint_cells']} cells; {len(res['problems'])} problems"
                       + (f": {res['problems'][:3]}" if res["problems"] else ""))


def _dirs_identical(a: Path, b: Path) -> tuple[bool, str]:
    fa = sorted(p.relative_to(a).as_posix() for p in a.rglob("*") if p.is_file())
    fb = sorted(p.relative_to(b).as_posix() for p in b.rglob("*") if p.is_file())
    if fa != fb:
        return False, f"file lists differ ({len(fa)} vs {len(fb)})"
    for rel in fa:
        if (a / rel).read_bytes() != (b / rel).read_bytes():
            return False, f"{rel} differs"
    return True, f"{len(fa)} files byte-identical"


def check_briefs() -> list[CheckResult]:
    from .brief import generate_tasks

    out = []
    with tempfile.TemporaryDirectory(prefix="dbwm_eval1_tasks_") as td:
        n = generate_tasks(Path(td))
        ok, detail = _dirs_identical(Path(td), _paths.data("tasks"))
    out.append(CheckResult("eval1/the 60 task briefs regenerate byte-identically from the worlds",
                           ok and n == 30, f"{n} briefs; {detail}"))
    bad = []
    for company, prefixes in USED_BRIEF_SHA_PREFIXES.items():
        for tier, pre in zip(TIERS, prefixes):
            p = _paths.data("tasks", f"{company}__{tier}.json")
            h = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "missing"
            if not h.startswith(pre):
                bad.append(f"{company}__{tier} {h[:8]} != {pre}")
    out.append(CheckResult("eval1/the 15 default-instance briefs match their recorded SHA-256 prefixes",
                           not bad, "15/15" if not bad else "; ".join(bad)))
    return out


def check_generator() -> CheckResult:
    from .gen.generate_matrix import emit

    with tempfile.TemporaryDirectory(prefix="dbwm_eval1_gen_") as td:
        td = Path(td)
        emit(td / "worlds", td / "calibration" / "report.json")
        ok1, d1 = _dirs_identical(td / "worlds", _paths.private("worlds"))
        ok2 = (td / "calibration" / "report.json").read_bytes() == \
            _paths.private("calibration", "report.json").read_bytes()
    return CheckResult("eval1/generator regenerates the 10 worlds + calibration report byte-identically",
                       ok1 and ok2, f"worlds: {d1}; calibration report identical: {ok2}")


def _guard(results: list[CheckResult], name: str, fn: Callable[[], Any]) -> Any:
    try:
        r = fn()
    except Exception as e:  # noqa: BLE001 - a broken check is a failed check
        results.append(CheckResult(name, False, f"{type(e).__name__}: {e}"))
        return None
    if isinstance(r, CheckResult):
        results.append(r)
    elif isinstance(r, tuple) and r and isinstance(r[0], (CheckResult, list)):
        first = r[0]
        results.extend(first if isinstance(first, list) else [first])
    elif isinstance(r, list):
        results.extend(r)
    return r


def selftest() -> list[CheckResult]:
    """Fast instrument checks. A missing fixture is a FAILED check, never a skip."""
    results: list[CheckResult] = []
    _guard(results, "eval1/calibration", check_calibration)
    _guard(results, "eval1/briefs", check_briefs)
    _guard(results, "eval1/generator", check_generator)
    _guard(results, "eval1/oracle modes", _check_oracle_modes)
    _guard(results, "eval1/stub agent", _check_stub)
    return results


def _check_oracle_modes() -> list[CheckResult]:
    """run_model serves every sealed mode for the careful plan; payload keys cannot change it."""
    from . import EVAL
    from dbwm.core.types import OracleContext

    out = []
    cal = {e["model_id"]: e for e in load_calibration()}
    cell_inst = "northwind-analytics__hostile"
    plan = {"bindings": cal["northwind-analytics"]["reference_plans"]["careful"]}
    for arm_name, arm in EVAL.arms.items():
        if arm.oracle_mode is None:
            continue
        cell = Cell("eval1", cell_inst, arm_name, 0)
        h = EVAL.oracle_handlers(cell)["run_model"]
        ctx = OracleContext(cell=cell, arm=arm, mode=arm.oracle_mode, sandbox=Path("."), state={})
        a = h({"plan": plan}, ctx)
        b = h({"plan": plan, "mode": "numeric"}, ctx)
        ok = a == b and (isinstance(a, str) if arm.oracle_mode == "prose" else a.get("ok") is True)
        out.append(CheckResult(f"eval1/run_model sealed mode {arm.oracle_mode} ({arm_name})", ok,
                               "careful plan served; extra payload keys ignored" if ok else f"got {str(a)[:120]}"))
    return out


def _check_stub() -> list[CheckResult]:
    """The stub agent drives every arm on one instance end to end (in process) and scores."""
    from . import EVAL
    from dbwm.core.types import OracleContext

    out = []
    inst = "sprout-crm__hard"
    for arm_name, arm in EVAL.arms.items():
        cell = Cell("eval1", inst, arm_name, 0)
        with tempfile.TemporaryDirectory(prefix="dbwm_eval1_stub_") as td:
            sb = Path(td)
            (sb / "TASK.md").write_text(EVAL.stage(cell).task_md, encoding="utf-8")
            handlers = EVAL.oracle_handlers(cell)
            ctx = OracleContext(cell=cell, arm=arm, mode=arm.oracle_mode, sandbox=sb, state={})
            calls = {"n": 0}

            def call(op, payload, _h=handlers, _ctx=ctx):
                if op not in _h:
                    raise RuntimeError(f"no oracle op {op!r} in this arm")
                if arm.query_budget is not None and calls["n"] >= arm.query_budget:
                    raise RuntimeError("query budget exhausted")
                calls["n"] += 1
                return _h[op](payload, _ctx)

            EVAL.stub_agent(cell, sb, call)
            res = EVAL.score(sb, cell, RULESET_V1)
        ok = res.deliverable_present and res.success is not None
        out.append(CheckResult(f"eval1/stub agent {arm_name} on {inst}", ok,
                               f"success={res.success} claimed={res.claimed_success} oracle_calls={calls['n']}"))
    return out
