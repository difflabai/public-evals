"""Deterministic no-LLM stub agent for eval 1 (pipeline test double, never evidence).

Adapted from the paper's STUB planner (planner.py:269-422) with one change of
principle: the stub sees only what a real agent sees. It parses TASK.md (the
prompt) for the goal, constraints, levers and base trajectory, and reaches the
model only through ``call_oracle("run_model", {"plan": ...})``, so it exercises
the sealed oracle exactly like an agent would. It never reads world files or
the calibration answer key (the paper's demo.py did; that is why demo.py is
not ported as an agent).

Policies per arm:
  ungrounded         scale the growth lever ~4x and over-hire; claim success
                     blindly; project base revenue x target (cannot verify).
  grounded-*         oracle-driven repair search (paper stub recipe: staged
                     growth, price creep, hires at the cap); claims read off the
                     last tool report. readonly respects its query budget.
  raw-figures        same search, but judges goal/constraints itself from the
                     returned figures.
  prose-feedback     brute-force plan plus up to two prose-guided repairs; claims
                     follow the prose verdict; forecast is a guess.
  broken-model       repair search against the corrupted report; claims follow
                     the (corrupted) tool output, which is how the lesion
                     produces overclaims.

Determinism: no randomness except a rep-dependent jitter on the ungrounded
growth factor, seeded with hashlib (never ``hash()``).
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from .engine import prec50

PRICE_LEVERS = {"acv", "take_rate", "price_per_job", "rate_per_consultant",
                "sub_price", "fill_rate", "utilization", "rev_share", "seat_price",
                "jobs_per_crew", "deals_per_ae", "asp", "consumable_per_unit"}


class _Stop(Exception):
    """The stub's model access ended (budget reached or oracle error)."""


@dataclass
class TaskView:
    horizon: int
    metric: str
    target: Decimal
    baseline_period: int
    target_period: int
    constraints: list[dict]
    levers: dict[str, list[str]]  # id -> defaults (ordered as in the prompt)
    base: dict[str, list[str]]
    has_tool: bool
    query_budget: int | None
    errors: list[str] = field(default_factory=list)


_LEVER_RE = re.compile(r"^  - (\S+) \((.*)\); default per quarter: (\[.*\])$")
_CONS_RE = re.compile(r"^  - (\w+): .* \(limit ([^,]+), driver (\w+), kind (\w+)\)$")
_BASE_RE = re.compile(r"^  (revenue|net_burn|cash|gross_margin_pct|headcount):\s+(\[.*\])$")
_GOAL_RE = re.compile(r"GOAL: by period (\d+), grow (\w+) to at least ([0-9.]+)x\s*\n"
                      r"the value it takes in period (\d+)")


def parse_task(text: str) -> TaskView:
    """Recover the task from the prompt text (what any agent would read)."""
    m = re.search(r"Produce a (\d+)-quarter operating plan", text)
    if not m:
        raise ValueError("TASK.md: horizon line not found")
    horizon = int(m.group(1))
    g = _GOAL_RE.search(text)
    if not g:
        raise ValueError("TASK.md: goal line not found")
    constraints, levers, base = [], {}, {}
    section = None
    for line in text.splitlines():
        if line.startswith("CONSTRAINTS"):
            section = "cons"
            continue
        if line.startswith("LEVERS"):
            section = "levers"
            continue
        if line.startswith("STARTING POINT"):
            section = "base"
            continue
        if line.startswith("DELIVERABLES"):
            section = None
        if section == "cons":
            cm = _CONS_RE.match(line)
            if cm:
                constraints.append({"id": cm.group(1), "limit": Decimal(cm.group(2)),
                                    "driver": cm.group(3), "kind": cm.group(4)})
        elif section == "levers":
            lm = _LEVER_RE.match(line)
            if lm:
                levers[lm.group(1)] = [str(v) for v in ast.literal_eval(lm.group(3))]
        elif section == "base":
            bm = _BASE_RE.match(line)
            if bm:
                base[bm.group(1)] = [str(v) for v in ast.literal_eval(bm.group(2))]
    budget = re.search(r"at most (\d+) runs of the model", text)
    return TaskView(
        horizon=horizon, metric=g.group(2), target=Decimal(g.group(3)),
        baseline_period=int(g.group(4)), target_period=int(g.group(1)),
        constraints=constraints, levers=levers, base=base,
        has_tool="run_model" in text, query_budget=int(budget.group(1)) if budget else None,
    )


def _primary_growth_lever(levers: dict) -> str:
    for pref in ("marketing_spend", "devrel_spend", "bd_spend"):
        if pref in levers:
            return pref
    for lid in levers:
        if not lid.startswith("hires"):
            return lid
    return next(iter(levers))


def _scaled(vals, factor) -> list[str]:
    return [str((Decimal(v) * Decimal(str(factor))).quantize(Decimal("0.01"))) for v in vals]


def _scale_series(vals, factor) -> list[str]:
    return [str((Decimal(v) * Decimal(str(factor))).quantize(Decimal("0.0001"))) for v in vals]


def _ramp_series(vals, per_period) -> list[str]:
    out, f = [], Decimal("1")
    for v in vals:
        out.append(str((Decimal(v) * f).quantize(Decimal("0.0001"))))
        f *= Decimal(str(1 + per_period))
    return out


def _hire_cap(task: TaskView) -> int | None:
    for c in task.constraints:
        if c["id"] == "hire_cap":
            return int(c["limit"])
    return None


class _Model:
    """The stub's only window on the model: run_model calls, cached and budgeted."""

    def __init__(self, call_oracle: Callable[[str, dict], Any], budget: int | None):
        self.call = call_oracle
        self.budget = budget
        self.calls = 0
        self.cache: dict[str, Any] = {}
        self.last: tuple[dict, Any] | None = None

    def run(self, bindings: dict) -> Any:
        key = json.dumps(bindings, sort_keys=True)
        if key in self.cache:
            self.last = (dict(bindings), self.cache[key])
            return self.cache[key]
        if self.budget is not None and self.calls >= self.budget:
            raise _Stop("query budget reached")
        self.calls += 1
        try:
            res = self.call("run_model", {"plan": {"bindings": dict(bindings)}})
        except Exception as e:  # noqa: BLE001 - any refusal ends model access
            raise _Stop(str(e)) from e
        self.cache[key] = res
        self.last = (dict(bindings), res)
        return res


def _numeric_feedback(res) -> tuple[bool, list[str]]:
    if not isinstance(res, dict) or not res.get("ok"):
        return False, ["unevaluable"]
    return bool(res["goal"]["met"]), list(res.get("violations", []))


def _self_judge(task: TaskView, bindings: dict, res) -> tuple[bool, list[str]]:
    """raw-figures: decide goal/constraints from figures alone."""
    if not isinstance(res, dict) or not res.get("ok"):
        return False, ["unevaluable"]
    traj = res["trajectory"]
    rev = [Decimal(v) for v in traj[task.metric]]
    goal_ok = rev[task.baseline_period] != 0 and \
        rev[task.target_period] / rev[task.baseline_period] >= task.target
    viol = []
    for c in task.constraints:
        if c["kind"] == "binding_max":
            vals = bindings.get(c["driver"], task.levers.get(c["driver"], []))
            actual = max(Decimal(v) for v in vals) if vals else Decimal(0)
            ok = actual <= c["limit"]
        else:
            s = [Decimal(v) for v in traj.get(c["driver"], [])]
            if not s:
                ok = True  # not reported; cannot judge
            elif c["kind"] == "series_max":
                ok = max(s) <= c["limit"]
            else:
                ok = min(s) >= c["limit"]
        if not ok:
            viol.append(c["id"])
    return goal_ok, viol


def _prose_feedback(res) -> tuple[bool, list[str]]:
    if not isinstance(res, str):
        return False, ["unevaluable"]
    goal_ok = "The growth goal appears MET" in res
    viol = re.findall(r"^- (\w+): OVER the limit", res, flags=re.M)
    return goal_ok, viol


def _search(task: TaskView, model: _Model, judge, *, budget: int, use_price: bool) -> dict:
    """Paper stub repair loop (planner.py:359-422), driven by tool feedback."""
    levers = task.levers
    growth = _primary_growth_lever(levers)
    price_levers = sorted(lv for lv in levers if lv in PRICE_LEVERS)
    has_hire = "hires_per_quarter" in levers
    cap = _hire_cap(task)

    def evaluate_plan(b):
        return judge(b, model.run(b))

    b: dict[str, list[str]] = {}
    if has_hire and cap is not None:
        b["hires_per_quarter"] = [str(max(0, cap))] * task.horizon
    for pl in price_levers:
        b[pl] = _ramp_series(levers[pl], 0.03)
    f = Decimal("2.0")
    while f <= Decimal("8.0"):
        b[growth] = _scaled(levers[growth], float(f))
        g_ok, _ = evaluate_plan(b)
        if g_ok:
            break
        f += Decimal("0.5")
    for _ in range(budget):
        g_ok, viol = evaluate_plan(b)
        if g_ok and not viol:
            return b
        if "burn_ceiling" in viol or "cash_floor" in viol:
            b[growth] = _scale_series(b[growth], 0.95)
        if "margin_floor" in viol:
            if price_levers:
                for pl in price_levers:
                    b[pl] = _scale_series(b[pl], 1.03)
            else:
                b[growth] = _scale_series(b[growth], 0.95)
        if has_hire and cap is not None and "hire_cap" in viol:
            b["hires_per_quarter"] = [str(max(0, cap))] * task.horizon
        g_ok2, _ = evaluate_plan(b)
        if not g_ok2:
            if use_price and price_levers:
                for pl in price_levers:
                    b[pl] = _scale_series(b[pl], 1.04)
            else:
                b[growth] = _scale_series(b[growth], 1.03)
    return b


def _brute_force(task: TaskView, factor: Decimal) -> dict:
    lever = _primary_growth_lever(task.levers)
    b = {lever: _scaled(task.levers[lever], float(factor))}
    if "hires_per_quarter" in task.levers:
        cap = _hire_cap(task)
        over = (cap + 2) if cap is not None else 6
        b["hires_per_quarter"] = [str(over)] * task.horizon
    return b


def _jitter(cell_id: str) -> Decimal:
    h = int(hashlib.sha256(cell_id.encode("utf-8")).hexdigest()[:8], 16)
    return Decimal(h % 3) * Decimal("0.05")


@prec50
def plan_for(task: TaskView, arm: str, cell_id: str,
             call_oracle: Callable[[str, dict], Any]) -> tuple[dict, dict]:
    """Return (bindings, claims) for one stub trial."""
    base0 = Decimal(task.base[task.metric][task.baseline_period])
    guess = str((base0 * task.target).quantize(Decimal("0.01")))

    if arm == "ungrounded" or not task.has_tool:
        factor = Decimal("4.0") * (Decimal(1) + _jitter(cell_id))
        b = _brute_force(task, factor)
        return b, {"goal_met": True, "constraints_satisfied": True,
                   "projected_final_revenue": guess,
                   "rationale": "Scaled acquisition aggressively to clear the target; "
                                "assumed the plan stays within limits."}

    model = _Model(call_oracle, task.query_budget)

    if arm == "prose-feedback":
        b = _brute_force(task, Decimal("4.0"))
        verdict = (False, ["unknown"])
        try:
            for _ in range(3):
                verdict = _prose_feedback(model.run(b))
                g_ok, viol = verdict
                if g_ok and not viol:
                    break
                lever = _primary_growth_lever(task.levers)
                b[lever] = _scale_series(b[lever], 0.8)
                cap = _hire_cap(task)
                if "hires_per_quarter" in b and cap is not None:
                    b["hires_per_quarter"] = [str(cap)] * task.horizon
        except _Stop:
            pass
        if model.last is not None:
            b = model.last[0]
            verdict = _prose_feedback(model.last[1])
        g_ok, viol = verdict
        return b, {"goal_met": bool(g_ok), "constraints_satisfied": not viol,
                   "projected_final_revenue": str((Decimal(guess) * Decimal("0.95"))
                                                  .quantize(Decimal("0.01"))),
                   "rationale": "Followed the model's prose verdicts; figures were withheld, "
                                "so the revenue projection is my own estimate."}

    if arm == "raw-figures":
        def judge(bb, res):
            return _self_judge(task, bb, res)
        use_price, loop = True, 30
    elif arm == "broken-model":
        judge = (lambda bb, res: _numeric_feedback(res))  # noqa: E731
        use_price, loop = False, 10
    else:  # grounded-readonly / grounded-iterate
        judge = (lambda bb, res: _numeric_feedback(res))  # noqa: E731
        use_price = arm == "grounded-iterate"
        loop = 60 if arm == "grounded-iterate" else 18
    try:
        _search(task, model, judge, budget=loop, use_price=use_price)
    except _Stop:
        pass
    if model.last is None:
        return {}, {"goal_met": False, "constraints_satisfied": False,
                    "projected_final_revenue": guess,
                    "rationale": "Could not run the model; committing the default plan."}
    b, res = model.last
    g_ok, viol = judge(b, res)
    traj = res.get("trajectory", {}) if isinstance(res, dict) else {}
    final = traj.get(task.metric, [guess])[-1]
    return b, {"goal_met": bool(g_ok), "constraints_satisfied": not viol,
               "projected_final_revenue": str(final),
               "rationale": "Searched plans against the model and committed the last plan "
                            "it reported on; claims follow that report."}


def run_stub(task_text: str, arm: str, cell_id: str, sandbox: Path,
             call_oracle: Callable[[str, dict], Any]) -> None:
    task = parse_task(task_text)
    bindings, claims = plan_for(task, arm, cell_id, call_oracle)
    sandbox = Path(sandbox)
    (sandbox / "plan.json").write_text(json.dumps({"bindings": bindings}, indent=2) + "\n",
                                       encoding="utf-8")
    (sandbox / "claims.json").write_text(json.dumps(claims, indent=2) + "\n", encoding="utf-8")
