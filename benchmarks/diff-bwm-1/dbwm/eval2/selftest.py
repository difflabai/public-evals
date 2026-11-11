"""``dbwm selftest --eval 2``: offline checks of the eval-2 instrument (no LLM, no network).

  world        the engine's own self-checks (world.selftest_checks)
  data         the pinned sprout-crm base model, CANARY.txt, the shipped world list, and every
               public brief.json equal to build_brief(private world.json)
  score        each world's careful optimum passes the paper and v1 rulesets at its recorded multiple
  staging      every arm x world stages only brief.json + TASK.md, with no answer-key value,
               canary or scorer path
  stub         the stub agent commits a plan on every arm (oracle wired in process) and is scored
  engine       a worker thread scores at the paper's prec-50 context

A missing fixture is a FAILED check, never a skip.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
from pathlib import Path

from dbwm.core import paths as _paths
from dbwm.core.types import CANARY_GUID, CANARY_STRING, Cell, CheckResult, OracleContext

SPROUT_CRM_SHA256 = "2f6ddcc4f729eab4b36d70173ea2001f5f7aa1a95199204c51e272528e907f95"


def selftest() -> list[CheckResult]:
    """Fast (< ~20 s) integrity checks. A missing fixture is a FAILED check, never a skip."""
    out: list[CheckResult] = []

    def guard(name, fn):
        try:
            r = fn()
            out.extend(r if isinstance(r, list) else [r])
        except Exception as e:  # noqa: BLE001
            out.append(CheckResult(name, False, f"{type(e).__name__}: {e}"))

    from . import ARMS, EVAL, WORLD_IDS
    from .world import brief_path, build_brief, load_reference, load_world, selftest_checks

    guard("eval2.world.selftest", lambda: [CheckResult("eval2." + n, ok, d) for n, ok, d in selftest_checks()])

    def files():
        res = []
        sprout = _paths.private("eval2", "base_models", "sprout-crm.operating-model.json")
        res.append(CheckResult("eval2.data.base_model",
                               sprout.exists() and hashlib.sha256(sprout.read_bytes()).hexdigest() == SPROUT_CRM_SHA256,
                               "sprout-crm base model present with the pinned sha256"))
        canary = _paths.private("eval2", "CANARY.txt")
        res.append(CheckResult("eval2.data.canary",
                               canary.exists() and CANARY_STRING in canary.read_text(),
                               "private/eval2/CANARY.txt carries the canary string"))
        conf = json.loads(_paths.private("eval2", "confirmatory-worlds.json").read_text())
        res.append(CheckResult("eval2.data.worlds", list(conf) == list(WORLD_IDS),
                               f"confirmatory-worlds.json lists {conf}"))
        for w in WORLD_IDS:
            brief = json.loads(brief_path(w).read_text())
            same = brief == build_brief(load_world(w))
            res.append(CheckResult(f"eval2.data.brief.{w}", same,
                                   "public brief.json == build_brief(private world.json)"))
        return res
    guard("eval2.data", files)

    def careful():
        from .score import score_scenario, score_scenario_v1

        res = []
        for w in WORLD_IDS:
            world = load_world(w)
            ref = load_reference(w)
            sc = {"allocation": ref["careful_optimum"]["allocation"]}
            p, v = score_scenario(world, sc), score_scenario_v1(world, sc)
            res.append(CheckResult(f"eval2.score.careful_optimum.{w}",
                                   p["fully_valid"] and v["fully_valid"]
                                   and p["goal_multiple_actual"] == ref["careful_optimum"]["multiple"],
                                   "careful optimum (feasibility witness) passes paper and v1 at the recorded multiple"))
        return res
    guard("eval2.score.careful_optimum", careful)

    def staging():
        bad = []
        for w in WORLD_IDS:
            for a in ARMS:
                cell = Cell("eval2", w, a, 0)
                m = EVAL.stage(cell)
                names = [f.dst for f in m.files]
                text = m.task_md + "\n" + "\n".join(
                    (f.src.read_text() if f.src else f.content.decode()) for f in m.files)
                secrets = EVAL.answer_key_values(cell)
                problems = []
                if names != ["brief.json"]:
                    problems.append(f"staged {names}")
                for s in secrets:
                    if s in text:
                        problems.append("answer-key value staged")
                for tok in (CANARY_GUID, "reference.json", "world.json", "private/", "ANSWER_KEY",
                            "/" + "Users/", "/" + "home/", "..", "BWM_WORLD", "--mode"):
                    if tok in m.task_md:
                        problems.append(f"prompt contains {tok!r}")
                if problems:
                    bad.append(f"{cell.cell_id}: {problems}")
        return CheckResult("eval2.staging", not bad,
                           f"{len(WORLD_IDS) * len(ARMS)} arm x world sandboxes stage only brief.json + TASK.md "
                           "with no answer-key value, canary or scorer path" + (f"; {bad[:3]}" if bad else ""))
    guard("eval2.staging", staging)

    def stub_e2e():
        res = []
        w = WORLD_IDS[0]
        for a, arm in ARMS.items():
            cell = Cell("eval2", w, a, 0)
            with tempfile.TemporaryDirectory(prefix="dbwm_e2_stub_") as td:
                sb = Path(td)
                for f in EVAL.stage(cell).files:
                    (sb / f.dst).write_bytes(f.src.read_bytes())
                calls = run_stub_direct(cell, sb)
                r = EVAL.score(sb, cell, "v1")
                res.append(CheckResult(f"eval2.stub.{a}", r.deliverable_present and r.success is not None,
                                       f"stub committed a plan ({calls} oracle calls); v1 success={r.success}, "
                                       f"claimed={r.claimed_success}, overclaim={r.overclaim}"))
        return res
    guard("eval2.stub", stub_e2e)

    def threads():
        from .world import load_world as lw
        from .score import score_scenario

        world = lw(WORLD_IDS[0])
        sc = {"allocation": load_reference(WORLD_IDS[0])["careful_optimum"]["allocation"]}
        main = score_scenario(world, sc)["goal_multiple_actual"]
        got = []
        t = threading.Thread(target=lambda: got.append(score_scenario(world, sc)["goal_multiple_actual"]))
        t.start()
        t.join()
        return CheckResult("eval2.engine.thread_context", got == [main],
                           "a worker thread scores at the paper's prec-50 context (same digits as the main thread)")
    guard("eval2.engine.thread_context", threads)
    return out


class _BudgetRefused(RuntimeError):
    pass


def run_stub_direct(cell: Cell, sandbox: Path) -> int:
    """Run EVAL.stub_agent with call_oracle wired straight to the handlers (no server).

    Mirrors the oracle server's contract: sealed mode from the arm, payload
    override keys stripped, query budget enforced (raises on exhaustion).
    Returns the number of oracle calls made.
    """
    from . import ARMS, EVAL

    arm = ARMS[cell.arm]
    handlers = EVAL.oracle_handlers(cell)
    ctx = OracleContext(cell=cell, arm=arm, mode=arm.oracle_mode, sandbox=Path(sandbox), state={})
    n = {"calls": 0}

    def call_oracle(op: str, payload: dict | None = None):
        if op not in handlers:
            raise _BudgetRefused(f"unknown tool {op}")
        if arm.query_budget is not None and n["calls"] >= arm.query_budget:
            raise _BudgetRefused("query budget exhausted")
        n["calls"] += 1
        clean = {k: v for k, v in (payload or {}).items()
                 if k not in ("mode", "channel", "backend", "world", "path")}
        return handlers[op](clean, ctx)

    EVAL.stub_agent(cell, Path(sandbox), call_oracle)
    return n["calls"]
