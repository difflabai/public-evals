"""``dbwm selftest --eval 3``: offline checks of the eval-3 instrument (no LLM, no network).

  faithful   the faithful 11-formula self-model (fixtures.py) reproduces the true world to 1e-9
  tasks      the shipped task.json equals render_task(world)
  prompts    each generative arm's release prompt is the paper text plus the documented changes
  staging    no answer-key value, canary, scorer-only name or host path in any arm x world sandbox
  sealed     the model_eval / model_test ops are sealed per arm
  stub       the stub agent end to end on the generative arms (oracle wired in process, v1)
  canary     private/eval3/CANARY.txt
  regime     regime.json names known arms, container isolation and the v1 ruleset

A missing fixture is a FAILED check, never a skip.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from dbwm.core import paths
from dbwm.core.types import CANARY_GUID, CANARY_STRING, Cell, CheckResult

EVAL_ID = "eval3"

_HOST_PATH = re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+")


def _check(name: str, problems: list[str], ok_detail: str) -> CheckResult:
    if problems:
        return CheckResult(
            name, False, "; ".join(problems[:6]) + (f" (+{len(problems) - 6} more)" if len(problems) > 6 else "")
        )
    return CheckResult(name, True, ok_detail)


def _guard(name: str, fn) -> CheckResult:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - a missing fixture is a FAILED check, never a skip
        return CheckResult(name, False, f"{type(e).__name__}: {e}")


def run_stub_direct(cell: Cell, ruleset: str, work: Path) -> tuple[Any, list[dict]]:
    """Run the stub agent into ``work`` with call_oracle wired straight to the handlers; score it.

    Returns ``(ScoreResult, calls)``. The context is the one the core runner
    builds (sealed mode from the arm, the sandbox path, empty state).
    """
    from dbwm.core.types import OracleContext

    from . import EVAL

    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    man = EVAL.stage(cell)
    for f in man.files:
        (work / f.dst).write_bytes(f.src.read_bytes() if f.src else f.content)
    (work / "TASK.md").write_text(man.task_md, encoding="utf-8")
    arm = EVAL.arms[cell.arm]
    ctx = OracleContext(cell=cell, arm=arm, mode=arm.oracle_mode, sandbox=work, state={})
    handlers = EVAL.oracle_handlers(cell)
    calls: list[dict] = []

    def call_oracle(op: str, payload: dict | None = None) -> Any:
        if op not in handlers:
            raise PermissionError(f"op {op!r} not available on arm {cell.arm}")
        res = handlers[op](dict(payload or {}), ctx)
        calls.append({"op": op, "payload": payload, "ok": not (isinstance(res, dict) and res.get("ok") is False)})
        return res

    EVAL.stub_agent(cell, work, call_oracle)
    return EVAL.score(work, cell, ruleset), calls


def selftest() -> list[CheckResult]:
    import tempfile

    from dbwm.eval2 import load_brief, load_reference, load_world

    from . import EVAL, WORLD_IDS
    from .arms import ARMS
    from .fixtures import faithful_model, plan_from_allocation
    from .model_dsl import evaluate_model
    from .prompt import build_prompt, paper_prompt, strip_release_changes
    from .task import render_task, task_path

    checks: list[CheckResult] = []

    def faithful():
        probs = []
        for w in WORLD_IDS:
            b, world = load_brief(w), load_world(w)
            g, h = plan_from_allocation(load_reference(w)["careful_optimum"]["allocation"])
            ev = evaluate_model(faithful_model(b, g, h))
            if isinstance(ev, dict):
                probs.append(f"{w}: {ev}")
                continue
            true = world.evaluate({"growth_capital": g, "eng_headcount": h})["parent"]
            for d in ("revenue", "cash", "gross_profit", "headcount"):
                for t in range(world.horizon):
                    a, x = ev.parent[d][t], true[d][t]
                    if x != 0 and abs(a - x) / abs(x) > Decimal("1e-9"):
                        probs.append(f"{w} {d}[{t}]")
        return _check(
            "eval3 faithful self-model reproduces the true world (1e-9)",
            probs,
            f"{len(WORLD_IDS)} worlds, careful-optimum plans, revenue/cash/gross_profit/headcount",
        )

    checks.append(_guard("eval3 faithful self-model reproduces the true world (1e-9)", faithful))

    def tasks():
        probs = [w for w in WORLD_IDS if task_path(w).read_text(encoding="utf-8") != render_task(load_world(w))]
        return _check("eval3 shipped task.json == build_task(world)", probs, f"{len(WORLD_IDS)} worlds")

    checks.append(_guard("eval3 shipped task.json == build_task(world)", tasks))

    def prompts():
        probs = []
        for w in WORLD_IDS:
            b = load_brief(w)
            for a in ("gen-figures", "gen-both", "gen-code"):
                if strip_release_changes(build_prompt(b, a), a, b) != paper_prompt(b, a):
                    probs.append(f"{w}/{a} release prompt is not paper + G1-G3/P3/P4")
        return _check("eval3 prompts (paper text + documented changes only)", probs,
                      f"{len(WORLD_IDS)} worlds x 3 generative arms")

    checks.append(_guard("eval3 prompts (paper text + documented changes only)", prompts))

    def staging():
        from dbwm.core import paths as P

        probs = []
        names = [n for n in EVAL.SCORER_ONLY_NAMES if n not in ("private", "grader")]
        for w in WORLD_IDS:
            for a in ARMS:
                cell = Cell("eval3", w, a, 0)
                man = EVAL.stage(cell)
                dsts = sorted(f.dst for f in man.files)
                want = ["brief.json", "task.json"] if a in ("gen-both", "gen-code") else ["brief.json"]
                if dsts != want:
                    probs.append(f"{w}/{a}: staged {dsts}")
                vals = EVAL.answer_key_values(cell)
                blobs = [f.src.read_text(encoding="utf-8") if f.src else f.content.decode() for f in man.files]
                blobs.append(man.task_md)
                for text in blobs:
                    if CANARY_GUID in text or CANARY_STRING in text:
                        probs.append(f"{w}/{a}: canary")
                    if any(v in text for v in vals):
                        probs.append(f"{w}/{a}: answer-key value")
                    if any(n in text for n in names):
                        probs.append(f"{w}/{a}: scorer-only name")
                    if _HOST_PATH.search(text) or str(P.PKG_ROOT) in text:
                        probs.append(f"{w}/{a}: absolute path")
                if not vals:
                    probs.append(f"{w}/{a}: no answer-key values to scan for")
        return _check(
            "eval3 staging: no answer key, canary, scorer names or paths (every arm x world)",
            probs,
            f"{len(WORLD_IDS) * len(ARMS)} cells",
        )

    checks.append(_guard("eval3 staging: no answer key, canary, scorer names or paths (every arm x world)", staging))

    def sealed():
        from dbwm.core.types import OracleContext

        from .oracle import handle_model_test

        probs = []
        if set(EVAL.oracle_handlers(Cell("eval3", "orion-holdings", "gen-figures", 0))) != {"model_eval"}:
            probs.append("gen-figures handlers")
        if set(EVAL.oracle_handlers(Cell("eval3", "orion-holdings", "gen-both", 0))) != {"model_eval", "model_test"}:
            probs.append("gen-both handlers")
        if EVAL.oracle_handlers(Cell("eval3", "orion-holdings", "gen-code", 0)):
            probs.append("gen-code has oracle handlers")
        if {s.name for s in ARMS["gen-figures"].tools.oracle_tools} != {"model_eval"}:
            probs.append("gen-figures tool specs")
        cell = Cell("eval3", "orion-holdings", "gen-figures", 0)
        ctx = OracleContext(cell=cell, arm=ARMS["gen-figures"], mode="eval", sandbox=None, state={})
        try:
            handle_model_test({"model": {}, "mode": "test"}, ctx)
            probs.append("model_test answered under mode 'eval'")
        except ValueError:
            pass
        if ARMS["gen-code"].min_isolation.value != "container":
            probs.append("gen-code isolation")
        return _check(
            "eval3 oracle ops sealed per arm", probs, "gen-figures: model_eval only; gen-both: both; gen-code: none"
        )

    checks.append(_guard("eval3 oracle ops sealed per arm", sealed))

    def stub():
        probs = []
        expect = {"gen-figures": (True, False), "gen-both": (True, False), "gen-code": (False, True)}
        with tempfile.TemporaryDirectory(prefix="dbwm_eval3_selftest_") as td:
            for a, (succ, over) in expect.items():
                cell = Cell("eval3", "orion-holdings", a, 0)
                sr, calls = run_stub_direct(cell, "v1", Path(td) / a)
                if sr.success is not succ or sr.overclaim is not over:
                    probs.append(f"{a}: success={sr.success} overclaim={sr.overclaim}")
                if a != "gen-code" and not calls:
                    probs.append(f"{a}: no oracle calls")
        return _check(
            "eval3 stub agent end to end (direct, v1)",
            probs,
            "gen-figures/gen-both honest successes; gen-code consistent-but-wrong overclaim",
        )

    checks.append(_guard("eval3 stub agent end to end (direct, v1)", stub))

    def canary():
        p = paths.private(EVAL_ID, "CANARY.txt")
        ok = p.is_file() and p.read_text(encoding="utf-8").strip() == CANARY_STRING
        return _check("eval3 private/eval3/CANARY.txt", [] if ok else ["missing or wrong"], "present")

    checks.append(_guard("eval3 private/eval3/CANARY.txt", canary))

    def regime():
        r = EVAL.default_regime()
        probs = [a for a in r["arms"] if a not in ARMS]
        if r.get("isolation") != "container":
            probs.append("isolation")
        if r.get("ruleset") != "v1":
            probs.append("ruleset")
        return _check(
            "eval3 regime.json", probs, f"{len(r['arms'])} arms, reps {r['reps']}, max_turns {r['max_turns']}"
        )

    checks.append(_guard("eval3 regime.json", regime))
    return checks
