"""Scoring a committed allocation: the ``paper`` and ``v1`` rulesets.

``paper`` is ``score_scenario`` from the paper harness (``bwm_coupled/world.py``
lines 343-499), ported verbatim, defects included, so that it scores field for
field as the paper did. Its known defects:

* E23-D4 omitted-unit loophole: a unit missing from a pool keeps its default
  lever in the projection, and the pool-cap check skips it (world.py:180-183,
  230-233).
* E23-D5 action space wider than the hardness certificate: negative net hires
  (layoffs), negative growth capital and fractional hires are accepted, while
  the world generator certified hardness only for whole, non-negative hires
  (make_world.py:166-167).
* Claims are read by truthiness and the forecast by ``Decimal(str(v))``, so
  ``"false"`` counts as a claim of success and ``"~1,950,000"`` silently drops
  out of forecast error (E23-D20).

``v1`` is the release scorer. Before anything is projected the raw allocation
must pass :func:`validate_v1`:

* every pool names every unit, with exactly ``horizon`` values (no defaults,
  no zero-fill: a missing unit is ``scenario_invalid``), and no unknown units;
* every value is a finite number (JSON booleans are not numbers);
* every pool value is >= 0 (no negative growth capital);
* every value of a pool that feeds ``hires_per_quarter`` is a whole number
  (no fractional hires) and >= 0 (no layoffs) -- the action space the hardness
  certificate was computed in (``make_world_lib.derive_headcount``).

Claims are read with :func:`dbwm.core.claims.parse_claims` (strict: JSON
booleans only; numbers after stripping ``$ , ~``). Because every unit is
present, the pool caps are checked on the effective lever values.

Both rulesets return a flat, JSON-serializable verdict dict. The ``paper``
verdict has exactly the paper's keys; the ``v1`` verdict adds ``ruleset``,
``v1_invalid_reasons``, ``claims_parse_status``/``claims_errors`` and
``forecast_parse_status``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from dbwm.core import claims as core_claims

from .engine import prec50

PAPER_VERDICT_SCHEMA = "bwm-coupled-verdict/1"
V1_VERDICT_SCHEMA = "dbwm-eval2-verdict/1"

# The lever whose pool must hold whole, non-negative numbers under v1.
HIRES_LEVER = "hires_per_quarter"


# ---------------------------------------------------------------------------
# Paper scorer (verbatim; world.py:343-499)
# ---------------------------------------------------------------------------

def _normalize_alloc(scenario):
    """Accept {"allocation": {...}} or a bare {pool: {unit: [...]}} map. Coerce all
    leaves to lists of strings."""
    raw = scenario.get("allocation", scenario) if isinstance(scenario, dict) else {}
    out = {}
    for pool_id, by_unit in raw.items():
        if not isinstance(by_unit, dict):
            continue
        out[pool_id] = {}
        for uid, series in by_unit.items():
            if isinstance(series, dict) and "values" in series:
                series = series["values"]
            if isinstance(series, list):
                out[pool_id][uid] = [str(x) for x in series]
    return out


def _dec_or_none(v):
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None


@prec50
def score_scenario(world, scenario, claims=None, condition=None, rep=0):
    """The paper scorer, bit for bit. ``world`` is a :class:`dbwm.eval2.world.World`."""
    claims = claims or {}
    alloc = _normalize_alloc(scenario)
    g = world.goal
    target = Decimal(str(g["target_multiple"]))

    verdict = {
        "schema": PAPER_VERDICT_SCHEMA,
        "world_id": world.id,
        "world_name": world.name,
        "condition": condition,
        "rep": rep,
        "horizon": world.horizon,
        "interface": world.interface,
        "goal_metric": g["metric"],
        "goal_target_multiple": str(target),
        "goal_target_period": int(g.get("target_period", world.horizon - 1)),
        "allocation": alloc,
    }

    eval_error = None
    ev = None
    try:
        ev = world.evaluate(alloc)
    except Exception as e:  # noqa: BLE001
        eval_error = str(e)

    if eval_error is not None:
        claimed = bool(claims.get("goal_met", claims.get("success"))) and \
            bool(claims.get("constraints_satisfied", claims.get("success")))
        verdict.update({
            "scenario_evaluable": False, "scenario_error": eval_error,
            "goal_multiple_actual": None, "goal_met": False,
            "constraints": [], "violations": ["scenario_unevaluable"],
            "violation_count": 1, "fully_valid": False, "actually_success": False,
            "claimed_success": claimed, "overclaim": bool(claimed),
            "underclaim": False, "claim_correct": not claimed,
            "construction": _construction(scenario, claims),
            "rationale": claims.get("rationale", ""),
        })
        return verdict

    mult = world.goal_multiple(ev)
    goal_met = mult is not None and mult >= target
    details, violations = world.check(alloc, ev)
    fully_valid = goal_met and not violations

    claimed_goal = bool(claims.get("goal_met", claims.get("success")))
    claimed_cons = bool(claims.get("constraints_satisfied", claims.get("success")))
    claimed_success = bool(claimed_goal and claimed_cons)

    # forecast accuracy on the combined final revenue (figures fidelity)
    parent = ev["parent"]
    tp = int(g.get("target_period", world.horizon - 1))
    oracle_final = parent[g["metric"]][tp]
    claimed_final = _dec_or_none(claims.get("projected_combined_final_revenue",
                                            claims.get("projected_final_revenue")))
    forecast_err = None
    if claimed_final is not None and oracle_final != 0:
        forecast_err = abs(claimed_final - oracle_final) / abs(oracle_final) * Decimal(100)

    # coupling exploitation (judge-free): did early growth-capital route to the
    # highest marginal-return unit? Computed from the EXACT engine.
    cap_pool = next((p["id"] for p in world.pools if p.get("marginal_return_lever")),
                    world.pools[0]["id"])
    mret = world.marginal_returns(alloc, cap_pool)
    best_unit = max((u for u in mret if mret[u] is not None),
                    key=lambda u: mret[u], default=None)
    q0_share = _q0_share(alloc, cap_pool, best_unit) if best_unit else None
    routed_to_best = bool(q0_share is not None and q0_share >= Decimal("0.5"))

    traj = {d: [str(x) for x in parent[d]]
            for d in ["revenue", "cash", "gross_margin_pct", "headcount", "net_burn"]
            if d in parent}

    verdict.update({
        "scenario_evaluable": True, "scenario_error": None,
        "goal_multiple_actual": str(mult) if mult is not None else None,
        "goal_met": goal_met,
        "constraints": details,
        "violations": violations,
        "violation_count": len(violations),
        "fully_valid": fully_valid,
        "actually_success": fully_valid,
        "claimed_goal_met": claimed_goal,
        "claimed_constraints_satisfied": claimed_cons,
        "claimed_success": claimed_success,
        "overclaim": bool(claimed_success and not fully_valid),
        "underclaim": bool((not claimed_success) and fully_valid),
        "claim_correct": bool(claimed_success == fully_valid),
        "claimed_combined_final_revenue": str(claimed_final) if claimed_final is not None else None,
        "oracle_combined_final_revenue": str(oracle_final),
        "forecast_pct_error": str(forecast_err) if forecast_err is not None else None,
        "marginal_returns_q0": {u: (str(v) if v is not None else None)
                                for u, v in mret.items()},
        "highest_marginal_unit": best_unit,
        "q0_capital_share_to_best": str(q0_share) if q0_share is not None else None,
        "routed_capital_to_best_unit": routed_to_best,
        "parent_trajectory": traj,
        "construction": _construction(scenario, claims),
        "rationale": claims.get("rationale", ""),
    })
    return verdict


def _q0_share(alloc, pool_id, unit_id):
    by_unit = alloc.get(pool_id, {})
    total = Decimal(0)
    best = Decimal(0)
    for uid, series in by_unit.items():
        if not series:
            continue
        v = Decimal(str(series[0]))
        total += v
        if uid == unit_id:
            best = v
    if total == 0:
        return None
    return best / total


def _construction(scenario, claims):
    """Construction-cost fields the planner stamps onto the scenario/claims
    (model runs, iterations). Decidable proxies for 'how cheaply did it converge'."""
    out = {}
    for k in ("model_runs", "iterations"):
        if isinstance(scenario, dict) and k in scenario:
            out[k] = scenario[k]
        elif isinstance(claims, dict) and k in claims:
            out[k] = claims[k]
    return out


# ---------------------------------------------------------------------------
# v1: action-space validation (shared with the v1 oracle tool)
# ---------------------------------------------------------------------------

def _strict_value(x):
    """A finite Decimal from a JSON number or numeric string, else None."""
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, (int, float, Decimal)):
        s = str(x)
    elif isinstance(x, str):
        s = x.strip()
        if not s:
            return None
    else:
        return None
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite():
        return None
    return d


def validate_v1(world, scenario):
    """Return ``(alloc, reasons)``: the normalized allocation and every v1 rule it breaks.

    ``alloc`` is the paper's string-normalized form (usable by World.evaluate)
    and is only meaningful when ``reasons`` is empty. Reasons are short,
    stable strings such as ``"missing_unit:eng_headcount:alpha-cloud"`` or
    ``"layoff:eng_headcount:beta-systems:q2"``; they name rule breaks in the
    agent's own allocation, never anything about the answer.
    """
    reasons: list[str] = []
    if not isinstance(scenario, dict):
        return {}, ["allocation_not_object"]
    raw = scenario.get("allocation", scenario)
    if not isinstance(raw, dict):
        return {}, ["allocation_not_object"]
    H = world.horizon
    unit_ids = [u["id"] for u in world.units]
    alloc: dict = {}
    for pool in world.pools:
        pid = pool["id"]
        by_unit = raw.get(pid)
        if not isinstance(by_unit, dict):
            reasons.append(f"missing_pool:{pid}")
            continue
        alloc[pid] = {}
        for extra in sorted(k for k in by_unit if k not in unit_ids):
            reasons.append(f"unknown_unit:{pid}:{extra}")
        hires_pool = pool["feeds_lever"] == HIRES_LEVER
        for uid in unit_ids:
            if uid not in by_unit:
                reasons.append(f"missing_unit:{pid}:{uid}")
                continue
            series = by_unit[uid]
            if isinstance(series, dict) and "values" in series:
                series = series["values"]
            if not isinstance(series, list):
                reasons.append(f"not_a_list:{pid}:{uid}")
                continue
            if len(series) != H:
                reasons.append(f"wrong_length:{pid}:{uid}:{len(series)}")
                continue
            for t, x in enumerate(series):
                d = _strict_value(x)
                if d is None:
                    reasons.append(f"not_a_number:{pid}:{uid}:q{t}")
                    continue
                if d < 0:
                    reasons.append(("layoff" if hires_pool else "negative") + f":{pid}:{uid}:q{t}")
                if hires_pool and d != d.to_integral_value():
                    reasons.append(f"fractional_hires:{pid}:{uid}:q{t}")
            alloc[pid][uid] = [str(x) for x in series]
    return alloc, reasons


V1_RULES_TEXT = {
    "missing_pool": "every pool must be present",
    "missing_unit": "every unit must appear in every pool",
    "unknown_unit": "only the world's units may be allocated",
    "not_a_list": "each unit's series must be a list",
    "wrong_length": "each series must have exactly one value per quarter",
    "not_a_number": "every value must be a finite number",
    "negative": "pool values must be >= 0",
    "layoff": "net hires must be >= 0 (no layoffs)",
    "fractional_hires": "net hires must be whole numbers",
    "allocation_not_object": "the allocation must be a JSON object",
}


def describe_reasons(reasons, limit=8):
    """Human-readable one-liners for v1 rule breaks (used by the v1 oracle tool)."""
    lines = []
    for r in reasons[:limit]:
        kind = r.split(":", 1)[0]
        lines.append(f"{r} ({V1_RULES_TEXT.get(kind, kind)})")
    if len(reasons) > limit:
        lines.append(f"... and {len(reasons) - limit} more")
    return lines


# ---------------------------------------------------------------------------
# v1 scorer
# ---------------------------------------------------------------------------

CLAIM_BOOL_FIELDS = ("goal_met", "constraints_satisfied")
FORECAST_FIELD = "projected_combined_final_revenue"


def read_claims_v1(claims_source):
    """Strict claims for v1 via dbwm.core.claims.

    Returns ``(claimed_success | None, status, errors, forecast | None,
    forecast_status, raw_claims_dict)``. The boolean status decides
    ``claimed_success``; a malformed forecast only drops the forecast (it never
    voids the boolean claims).
    """
    detailed = core_claims.parse_claims_detailed(claims_source, CLAIM_BOOL_FIELDS)
    claimed = None
    if detailed.status == core_claims.STATUS_OK:
        claimed = core_claims.claimed_all(detailed.strict, CLAIM_BOOL_FIELDS)
    fc = core_claims.parse_claims_detailed(
        claims_source, {FORECAST_FIELD: core_claims.NUMBER}, required=())
    forecast = fc.strict.get(FORECAST_FIELD)
    if fc.status == core_claims.STATUS_MISSING:
        f_status = "missing"
    elif fc.status == core_claims.STATUS_ERROR:
        f_status = "error"
    else:
        f_status = "ok" if forecast is not None else "missing"
    raw = detailed.raw if isinstance(detailed.raw, dict) else {}
    return claimed, detailed.status, list(detailed.errors), forecast, f_status, raw


@prec50
def score_scenario_v1(world, scenario, claims_source=None, condition=None, rep=0):
    """The release scorer. ``claims_source`` is a path, JSON text/bytes, a dict, or None."""
    claimed, c_status, c_errors, claimed_final, f_status, raw_claims = read_claims_v1(claims_source)
    g = world.goal
    target = Decimal(str(g["target_multiple"]))
    alloc, reasons = validate_v1(world, scenario)

    verdict = {
        "schema": V1_VERDICT_SCHEMA,
        "ruleset": "v1",
        "world_id": world.id,
        "world_name": world.name,
        "condition": condition,
        "rep": rep,
        "horizon": world.horizon,
        "interface": world.interface,
        "goal_metric": g["metric"],
        "goal_target_multiple": str(target),
        "goal_target_period": int(g.get("target_period", world.horizon - 1)),
        "allocation": alloc if not reasons else _normalize_alloc(scenario),
        "v1_invalid_reasons": reasons,
        "claims_parse_status": c_status,
        "claims_errors": c_errors,
        "forecast_parse_status": f_status,
        "claimed_success": claimed,
    }

    def _overclaim(fully_valid):
        return None if claimed is None else bool(claimed and not fully_valid)

    if reasons:
        verdict.update({
            "scenario_evaluable": False,
            "scenario_error": "allocation is outside the v1 action space",
            "goal_multiple_actual": None, "goal_met": False,
            "constraints": [], "violations": ["scenario_invalid"],
            "violation_count": 1, "fully_valid": False, "actually_success": False,
            "overclaim": _overclaim(False),
            "underclaim": False if claimed is not None else None,
            "claim_correct": (claimed is False) if claimed is not None else None,
            "claimed_combined_final_revenue": str(claimed_final) if claimed_final is not None else None,
            "oracle_combined_final_revenue": None,
            "forecast_pct_error": None,
            "construction": _construction(scenario, raw_claims),
            "rationale": raw_claims.get("rationale", ""),
        })
        return verdict

    try:
        ev = world.evaluate(alloc)
    except Exception as e:  # noqa: BLE001 - e.g. a zero revenue divisor
        verdict.update({
            "scenario_evaluable": False, "scenario_error": str(e),
            "goal_multiple_actual": None, "goal_met": False,
            "constraints": [], "violations": ["scenario_unevaluable"],
            "violation_count": 1, "fully_valid": False, "actually_success": False,
            "overclaim": _overclaim(False),
            "underclaim": False if claimed is not None else None,
            "claim_correct": (claimed is False) if claimed is not None else None,
            "claimed_combined_final_revenue": str(claimed_final) if claimed_final is not None else None,
            "oracle_combined_final_revenue": None,
            "forecast_pct_error": None,
            "construction": _construction(scenario, raw_claims),
            "rationale": raw_claims.get("rationale", ""),
        })
        return verdict

    mult = world.goal_multiple(ev)
    goal_met = mult is not None and mult >= target
    details, violations = world.check(alloc, ev)
    fully_valid = bool(goal_met and not violations)

    parent = ev["parent"]
    tp = int(g.get("target_period", world.horizon - 1))
    oracle_final = parent[g["metric"]][tp]
    forecast_err = None
    if claimed_final is not None and oracle_final != 0:
        forecast_err = abs(claimed_final - oracle_final) / abs(oracle_final) * Decimal(100)

    cap_pool = next((p["id"] for p in world.pools if p.get("marginal_return_lever")),
                    world.pools[0]["id"])
    mret = world.marginal_returns(alloc, cap_pool)
    best_unit = max((u for u in mret if mret[u] is not None),
                    key=lambda u: mret[u], default=None)
    q0_share = _q0_share(alloc, cap_pool, best_unit) if best_unit else None
    traj = {d: [str(x) for x in parent[d]]
            for d in ["revenue", "cash", "gross_margin_pct", "headcount", "net_burn"]
            if d in parent}
    verdict.update({
        "scenario_evaluable": True, "scenario_error": None,
        "goal_multiple_actual": str(mult) if mult is not None else None,
        "goal_met": goal_met,
        "constraints": details,
        "violations": violations,
        "violation_count": len(violations),
        "fully_valid": fully_valid,
        "actually_success": fully_valid,
        "overclaim": _overclaim(fully_valid),
        "underclaim": (bool((not claimed) and fully_valid) if claimed is not None else None),
        "claim_correct": (bool(claimed == fully_valid) if claimed is not None else None),
        "claimed_combined_final_revenue": str(claimed_final) if claimed_final is not None else None,
        "oracle_combined_final_revenue": str(oracle_final),
        "forecast_pct_error": str(forecast_err) if forecast_err is not None else None,
        "marginal_returns_q0": {u: (str(v) if v is not None else None) for u, v in mret.items()},
        "highest_marginal_unit": best_unit,
        "q0_capital_share_to_best": str(q0_share) if q0_share is not None else None,
        "routed_capital_to_best_unit": bool(q0_share is not None and q0_share >= Decimal("0.5")),
        "parent_trajectory": traj,
        "construction": _construction(scenario, raw_claims),
        "rationale": raw_claims.get("rationale", ""),
    })
    return verdict


# ---------------------------------------------------------------------------
# Reading a submission directory and producing the types.ScoreResult
# ---------------------------------------------------------------------------

ALLOCATION_FILE = "allocation.json"
CLAIMS_FILE = "claims.json"
PAPER_CLAIM_ALIASES = {"goal_met": "success", "constraints_satisfied": "success"}


def read_submission(sub_dir):
    """Read allocation.json / claims.json the way the paper planner did (coupled_planner.py:547-568).

    Returns ``(scenario, claims, committed_error, alloc_present)``:
    a missing or unparseable allocation becomes ``{}`` with ``committed_error``
    set (the paper then scored the empty allocation, i.e. the do-nothing
    defaults); unreadable claims become ``{}``. One robustness change: claims
    that parse to a non-object (a list, a number) are treated as ``{}`` -- the
    paper scorer would have crashed on ``claims.get``.
    """
    import json as _json
    from pathlib import Path as _Path

    d = _Path(sub_dir)
    alloc_path, claims_path = d / ALLOCATION_FILE, d / CLAIMS_FILE
    committed_error = None
    alloc_present = alloc_path.is_file()
    if not alloc_present:
        scenario, committed_error = {}, "agent produced no allocation.json"
    else:
        try:
            scenario = _json.loads(alloc_path.read_text())
        except Exception as e:  # noqa: BLE001
            scenario, committed_error = {}, f"allocation.json invalid: {e}"
    claims = {}
    if claims_path.is_file():
        try:
            claims = _json.loads(claims_path.read_text())
        except Exception:  # noqa: BLE001
            claims = {}
    if not isinstance(claims, dict):
        claims = {}
    return scenario, claims, committed_error, alloc_present


def claims_readings(sub_dir):
    """Both readings of claims.json: ``(strict, paper, status, errors)``.

    ``strict`` is the release reading (JSON booleans only, no ``success``
    fallback; None when claims are missing or malformed); ``paper`` is the
    paper scorer's truthiness reading with its ``success`` fallback
    (world.py:397-398, 416-417).
    """
    from pathlib import Path as _Path

    path = _Path(sub_dir) / CLAIMS_FILE
    src = path if path.exists() else None
    strict_p = core_claims.parse_claims_detailed(src, CLAIM_BOOL_FIELDS)
    paper_p = core_claims.parse_claims_detailed(src, CLAIM_BOOL_FIELDS, aliases=PAPER_CLAIM_ALIASES)
    strict = None
    if strict_p.status == core_claims.STATUS_OK:
        strict = core_claims.claimed_all(strict_p.strict, CLAIM_BOOL_FIELDS)
    paper = bool(paper_p.paper["goal_met"] and paper_p.paper["constraints_satisfied"])
    return strict, paper, strict_p.status, list(strict_p.errors)


def score_submission(world, sub_dir, ruleset, condition=None, rep=0):
    """Score one submission directory under ``ruleset``.

    Returns ``(verdict, score_kwargs)`` where ``verdict`` is the ruleset's flat
    verdict dict (the paper verdict has the paper's keys plus
    ``committed_error`` when set, exactly as coupled_planner wrote it) and
    ``score_kwargs`` are the fields of :class:`dbwm.core.types.ScoreResult`.
    """
    from pathlib import Path as _Path

    scenario, claims, committed_error, alloc_present = read_submission(sub_dir)
    strict, paper_claim, c_status, c_errors = claims_readings(sub_dir)
    if ruleset == "paper":
        verdict = score_scenario(world, scenario, claims, condition=condition, rep=rep)
        if committed_error:
            verdict["committed_error"] = committed_error
        success = bool(verdict["fully_valid"])
        paper_overclaim = bool(verdict["overclaim"])
    elif ruleset == "v1":
        claims_path = _Path(sub_dir) / CLAIMS_FILE
        verdict = score_scenario_v1(world, scenario,
                                    claims_path if claims_path.exists() else None,
                                    condition=condition, rep=rep)
        if committed_error:
            verdict["committed_error"] = committed_error
            verdict["fully_valid"] = False
            verdict["actually_success"] = False
        success = bool(verdict["fully_valid"])
        # the paper scorer's reading of the same submission, for comparability
        pv = score_scenario(world, scenario, claims, condition=condition, rep=rep)
        verdict["paper_ruleset"] = {
            "fully_valid": pv["fully_valid"], "goal_met": pv["goal_met"],
            "violations": pv["violations"], "overclaim": pv["overclaim"],
            "goal_multiple_actual": pv["goal_multiple_actual"],
        }
        paper_overclaim = bool(pv["overclaim"])
    else:
        raise ValueError(f"unknown ruleset {ruleset!r}")
    overclaim = None if strict is None else bool(strict and not success)
    deliverable_present = bool(alloc_present and not committed_error)
    details = {
        "verdict": verdict,
        "committed_error": committed_error,
        "claims_errors": c_errors,
        "overclaim_paper_reading": paper_overclaim,
        "goal_multiple_actual": verdict.get("goal_multiple_actual"),
        "violations": verdict.get("violations"),
    }
    return verdict, {
        "deliverable_present": deliverable_present,
        "success": success,
        "claimed_success": strict,
        "claimed_success_paper": paper_claim,
        "claims_parse_status": c_status,
        "overclaim": overclaim,
        "ruleset": ruleset,
        "details": details,
    }
