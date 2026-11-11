"""The ``world_query`` oracle: run a candidate allocation through the true world.

Ported from the paper harness ``bwm_coupled/world_query.py``, which grounded
agents ran through Bash as ``BWM_WORLD=<abs path> python3 <abs path>/world_query.py
--alloc candidate.json --mode <m>``. Three things were wrong with that and are fixed here by construction:

* The channel was agent-selectable: ``--mode`` overrode the harness's
  ``BWM_QUERY_MODE`` (world_query.py:87-88) and the prompt printed the flag.
  Here the handler takes the mode from ``OracleContext.mode`` only; it is
  sealed in the per-trial oracle server, and the server strips and logs any
  ``mode``-like payload key before the handler sees it.
* The "verdict-only" boolean channel printed the goal multiple and every
  constraint's limit, actual value and worst quarter (world_query.py:154-166).
  v1 boolean output is the whitelist ``{ok, goal:{met}, constraints:[{id, ok}],
  violations}``.
* The tool needed the world spec on the agent's disk and its absolute path in
  the prompt. Here it runs in the harness; the sandbox holds only brief.json.

Output formats. :func:`render` reproduces the paper tool's stdout for every
mode when called with ``ruleset="paper"`` (a dict for the JSON modes and for
errors, a string for prose); ``tests/eval2/test_eval2_world_query.py``
compares it with outputs captured from the paper script. With
``ruleset="v1"`` (what the handler uses) the differences are exactly:

1. boolean -> the whitelist above;
2. a candidate outside the v1 action space (missing unit, wrong length,
   non-number, negative capital, negative or fractional hires) is refused
   with ``{"ok": false, "error": ...}`` naming the rule breaks, in every mode,
   so the tool never certifies a plan the v1 scorer would reject;
3. nothing else. The localizing hint (world_query.py:186-194, prescriptive
   advice, E23-D23) is kept for comparability and documented.

The paper's docstring advertised modes and mentioned a "reference optimum /
hidden hardness floor" (world_query.py:9-20); none of that text reaches the
agent now (the tool description is neutral, see arms.WORLD_QUERY_TOOL).
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from dbwm.core.safe_io import SafeReadError, read_nofollow

from .engine import prec50
from .score import _normalize_alloc, describe_reasons, validate_v1
from .world import World, load_world

MODES = ("localizing", "boolean", "raw", "prose", "broken")
MAX_CANDIDATE_BYTES = 1_000_000


def _fmt_series(xs):
    return [str(x) for x in xs]


def _worst_quarter_unit_for_cash(world, alloc, ev):
    """Which unit contributes the most negative cash at the worst combined-cash
    quarter — decidable localization, no judge."""
    parent_cash = ev["parent"]["cash"]
    H = world.horizon
    wq = min(range(H), key=lambda t: parent_cash[t])
    contribs = {uid: ev["units"][uid][wq]["cash"] for uid in ev["units"]}
    worst_unit = min(contribs, key=lambda u: contribs[u])
    return wq, worst_unit, {u: str(contribs[u]) for u in contribs}


def _sensitivity(world, alloc, pool_id, quarter, eps="10000"):
    """Marginal effect on the combined revenue multiple of +eps capital to each
    unit at `quarter`. A localizing hint the agent can act on; still leaves the
    search to the agent."""
    eps = Decimal(str(eps))
    base = world.goal_multiple(world.evaluate(alloc))
    out = {}
    for u in world.units:
        uid = u["id"]
        bumped = copy.deepcopy(alloc)
        pool = bumped.setdefault(pool_id, {})
        series = pool.get(uid)
        if series is None:
            lever = next(p for p in world.pools if p["id"] == pool_id)["feeds_lever"]
            series = [str(v) for v in world.models[uid]["nodes"][lever]["default"]["values"]]
        series = [str(v) for v in series]
        series[quarter] = str(Decimal(series[quarter]) + eps)
        pool[uid] = series
        m = world.goal_multiple(world.evaluate(bumped))
        out[uid] = str((m - base)) if (m is not None and base is not None) else None
    return out


@prec50
def render(world: World, scenario: Any, mode: str, ruleset: str = "v1"):
    """The tool's output for one candidate. ``world`` must already carry the
    interface the mode implies (``interface="broken"`` for mode ``broken``)."""
    if mode not in MODES:
        raise ValueError(f"unknown world_query mode {mode!r}")
    if ruleset == "v1":
        alloc, reasons = validate_v1(world, scenario)
        if reasons:
            return {"ok": False,
                    "error": "candidate is outside the allowed action space: "
                             + "; ".join(describe_reasons(reasons))}
    else:
        alloc = _normalize_alloc(scenario)

    try:
        ev = world.evaluate(alloc)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}

    details, violations = world.check(alloc, ev)
    mult = world.goal_multiple(ev)
    target = Decimal(str(world.goal["target_multiple"]))
    goal_ok = mult is not None and mult >= target
    parent = ev["parent"]
    tp = int(world.goal.get("target_period", world.horizon - 1))

    # ---- prose: verdict, no figures ----------------------------------------
    if mode == "prose":
        lines = [f"Projection for {world.name} over {world.horizon} quarters:"]
        lines.append(f"- Combined-revenue growth goal: {'MET' if goal_ok else 'NOT met'} "
                     f"(target {target}x).")
        for d in details:
            lines.append(f"- {d['id']}: {'OK' if d['ok'] else 'VIOLATED'} ({d.get('description','')})")
        lines.append("(Figures withheld; qualitative verdict only.)")
        return "\n".join(lines)

    # ---- raw: figures, no verdict ------------------------------------------
    if mode == "raw":
        return {
            "ok": True, "mode": "raw", "world_id": world.id,
            "parent_trajectory": {
                d: _fmt_series(parent[d]) for d in
                ["revenue", "gross_profit", "cash", "gross_margin_pct", "headcount", "net_burn"]
                if d in parent},
            "unit_trajectories": {
                uid: {d: _fmt_series([p[d] for p in ev["units"][uid]])
                      for d in ["revenue", "customers", "cash", "headcount", "gross_margin_pct"]}
                for uid in ev["units"]},
            "_note": ("Figures only. No pass/fail is provided. Decide for yourself, from "
                      "these figures, whether the combined revenue multiple "
                      f"(revenue[{tp}]/revenue[0]) >= {target}, combined cash stays >= 0, "
                      "combined margin stays >= its floor, every unit's capacity holds, "
                      "and no pool is over-allocated."),
        }

    # ---- v1 boolean: the whitelist (closes the figure leak) -------------------------------
    if mode == "boolean" and ruleset == "v1":
        return {
            "ok": True,
            "goal": {"met": goal_ok},
            "constraints": [{"id": d["id"], "ok": d["ok"]} for d in details],
            "violations": violations,
        }

    # ---- boolean (paper) / localizing / broken: verdict (+ figures) ---------
    out = {
        "ok": True, "mode": mode, "world_id": world.id,
        "goal": {"target_multiple": str(target),
                 "actual_multiple": str(mult) if mult is not None else None,
                 "met": goal_ok},
        "constraints": [{"id": d["id"], "ok": d["ok"],
                         **({"limit": d.get("limit"), "actual": d.get("actual")}
                            if "limit" in d else {}),
                         "worst_quarter": d.get("worst_quarter")}
                        for d in details],
        "violations": violations,
    }

    if mode != "boolean":
        out["parent_trajectory"] = {
            d: _fmt_series(parent[d]) for d in
            ["revenue", "gross_profit", "cash", "gross_margin_pct", "headcount", "net_burn"]
            if d in parent}
        out["unit_trajectories"] = {
            uid: {d: _fmt_series([p[d] for p in ev["units"][uid]])
                  for d in ["revenue", "customers", "cash", "headcount"]}
            for uid in ev["units"]}

    if mode == "localizing" and violations:
        loc = {}
        cap_pool = next((p["id"] for p in world.pools if p.get("marginal_return_lever")),
                        world.pools[0]["id"])
        for v in violations:
            if v == "cash_floor":
                wq, wu, contribs = _worst_quarter_unit_for_cash(world, alloc, ev)
                loc["cash_floor"] = {
                    "binding_quarter": wq, "worst_unit": wu,
                    "unit_cash_at_quarter": contribs,
                    "sensitivity_multiple_per_+10k_capital_at_q0":
                        _sensitivity(world, alloc, cap_pool, 0),
                    "hint": (f"combined cash is most negative in quarter {wq}; unit {wu} "
                             f"holds the most-negative cash there. Growth is margin-"
                             f"accretive here, so the fix is usually to route MORE early "
                             f"capital to the highest-marginal-return unit, not less."),
                }
            else:
                d = next(x for x in details if x["id"] == v)
                loc[v] = {"binding_quarter": d.get("worst_quarter"),
                          "detail": {k: d[k] for k in d if k not in ("constraints",)}}
        out["localization"] = loc
    elif mode == "broken":
        out["_warning"] = ("interface is BROKEN: the parent rollup is internally "
                           "inconsistent (a unit's cross-boundary values are mis-scaled) "
                           "even though each unit is individually fine. Figures may not "
                           "add up across the boundary.")
    return out


def render_paper_text(world_spec: Mapping[str, Any], candidate_text: str, mode: str) -> tuple[str, int]:
    """Exactly what the paper script printed (stdout, exit code) for a candidate file's text.

    Used by the golden tests; not used by any runnable arm.
    """
    spec = dict(world_spec)
    if mode == "broken":
        spec = copy.deepcopy(spec)
        spec["interface"] = "broken"
    world = World(spec)
    try:
        scenario = json.loads(candidate_text)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"allocation is not valid JSON: {e}"}) + "\n", 2
    out = render(world, scenario, mode, ruleset="paper")
    if isinstance(out, str):
        return out + "\n", 0
    if out.get("ok") is False:
        return json.dumps(out, indent=2) + "\n", 1
    return json.dumps(out, indent=2) + "\n", 0


# ---------------------------------------------------------------------------
# The oracle-server handler
# ---------------------------------------------------------------------------

class CandidateError(ValueError):
    """The payload did not carry a readable candidate."""


def _read_candidate_file(sandbox: Path, rel: str) -> str:
    """Read a sandbox-relative file, refusing anything that resolves outside the sandbox."""
    if not isinstance(rel, str) or not rel.strip():
        raise CandidateError("'file' must be a non-empty sandbox-relative path")
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        raise CandidateError("'file' must be a relative path inside the working directory")
    root = Path(sandbox).resolve()
    target = (root / p).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise CandidateError("'file' must stay inside the working directory") from None
    if not target.is_file():
        raise CandidateError(f"no such file in the working directory: {rel}")
    if target.stat().st_size > MAX_CANDIDATE_BYTES:
        raise CandidateError(f"{rel} is larger than {MAX_CANDIDATE_BYTES} bytes")
    # Read through one O_NOFOLLOW descriptor, never by name again: a symlink
    # swapped in after the checks above is not followed (red-team RT-T2).
    try:
        data = read_nofollow(target, MAX_CANDIDATE_BYTES)
    except SafeReadError as e:
        if e.reason == "too_large":
            raise CandidateError(f"{rel} is larger than {MAX_CANDIDATE_BYTES} bytes") from None
        raise CandidateError(f"no such file in the working directory: {rel}") from None
    return data.decode("utf-8", errors="replace")


def candidate_from_payload(payload: Mapping[str, Any], sandbox: Path | None):
    """Extract the candidate allocation: inline ``candidate`` or a sandbox ``file``.

    Returns the parsed scenario, or a paper-format error dict for unreadable JSON.
    """
    if not isinstance(payload, Mapping):
        raise CandidateError("payload must be an object")
    if "candidate" in payload and payload["candidate"] is not None:
        cand = payload["candidate"]
        if isinstance(cand, str):
            try:
                return json.loads(cand)
            except Exception as e:  # noqa: BLE001
                return {"__error__": f"allocation is not valid JSON: {e}"}
        return cand
    if "file" in payload and payload["file"] is not None:
        if sandbox is None:
            raise CandidateError("this oracle has no working directory to read files from")
        text = _read_candidate_file(sandbox, payload["file"])
        try:
            return json.loads(text)
        except Exception as e:  # noqa: BLE001
            return {"__error__": f"allocation is not valid JSON: {e}"}
    raise CandidateError("pass the candidate allocation as {\"file\": \"candidate.json\"} "
                         "or inline as {\"candidate\": {...}}")


def handle_world_query(payload: Mapping[str, Any], ctx) -> Any:
    """OracleHandler for op ``world_query``. Mode comes from ``ctx.mode`` only.

    ``ctx.cell.instance`` names the world; the world spec is read from the
    package's private directory inside the harness process. Payload keys other
    than ``candidate``/``file`` are ignored.
    """
    mode = ctx.mode
    if mode not in MODES:
        raise ValueError(f"oracle misconfigured: sealed mode {mode!r} is not a world_query mode")
    try:
        scenario = candidate_from_payload(payload, getattr(ctx, "sandbox", None))
    except CandidateError as e:
        return {"ok": False, "error": str(e)}
    if isinstance(scenario, dict) and "__error__" in scenario and len(scenario) == 1:
        return {"ok": False, "error": scenario["__error__"]}
    world = load_world(ctx.cell.instance, interface="broken" if mode == "broken" else None)
    state = getattr(ctx, "state", None)
    if isinstance(state, dict):
        state["world_query_calls"] = state.get("world_query_calls", 0) + 1
    return render(world, scenario, mode, ruleset="v1")
