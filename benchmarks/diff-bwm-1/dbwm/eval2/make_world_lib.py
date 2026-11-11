"""World-generation library: careful-optimum search, naive heuristics, hardness floor.

Ported from the paper harness ``bwm_coupled/make_world.py`` as a library only.
The paper file also had a ``--write`` CLI that produced a *different*
reference.json schema than ``make_worlds.py`` and would have overwritten the
shipped orion world; that CLI is gone. :mod:`dbwm.eval2.make_worlds`
is the one generator.

What the certificate means (and does not)
  1. 729 structured capital templates (3x3x3 shapes x 3^3 intensities) are
     scored; each template's headcount pool is DERIVED as the minimal whole,
     non-negative net-hire schedule that keeps every unit at capacity
     (:func:`derive_headcount`; the clamp at ``hire < 0 -> 0`` is
     make_world.py:166-167).
  2. The best constraint-feasible template is the "careful optimum" M*. It is a
     feasibility witness from a coarse search, not an optimum: fully valid
     plans above it exist.
  3. The four named naive heuristics are evaluated at 100% of the cap.
  4. The target sits 60% of the way from max(min-feasible, best naive) to M*.

Because hires are clamped at >= 0, hardness is certified only in the action
space without layoffs. The paper scorer accepted layoffs, and with
layoffs a flat equal split at 85-98% of the cap plus capacity-exact hires
passes in every world. The ``v1`` ruleset forbids layoffs, negative capital
and fractional hires, so its action space is exactly the certificate's; the
extra naive heuristic :func:`flat_split_candidates` (flat equal split at x% of
the cap with the certificate's clamped hires) is checked to fail under v1 for
every x in 1..100 (``make_worlds.certify_v1``).
"""

from __future__ import annotations

import itertools
import math
from decimal import Decimal

from .engine import evaluate, prec50
from .score import score_scenario, score_scenario_v1

H = 6

# ---------------------------------------------------------------------------
# Headcount derived from capacity (couples the two pools)
# ---------------------------------------------------------------------------

def _customers_for(world, uid, cap_slice):
    """Customers series for a unit given its marketing slice (customers are
    independent of hires, so we read them with default hires)."""
    per = evaluate(world.models[uid], {"marketing_spend": [str(x) for x in cap_slice]})
    return [p["customers"] for p in per]


@prec50
def derive_headcount(world, capital_alloc):
    """Minimal net-hire schedule that satisfies each unit's capacity constraint,
    given the capital allocation. Net hires are >= 0 (headcount is monotonic).
    Returns (headcount_alloc, feasible_within_pool: bool, peak_total)."""
    H_ = world.horizon
    per_unit_hires = {}
    for u in world.units:
        uid = u["id"]
        cph = Decimal(str(u["customers_per_head"]))
        custs = _customers_for(world, uid, capital_alloc.get(uid, [u["overrides"]["marketing_spend"]] * H_))
        h0 = Decimal(world.models[uid]["nodes"]["headcount"]["initial"])
        hires, hc_prev = [], h0
        for t in range(H_):
            need = custs[t] / cph
            req_round = Decimal(math.ceil(need))           # heads must be whole
            hire = req_round - hc_prev
            if hire < 0:
                hire = Decimal(0)
            hires.append(int(hire))
            hc_prev = hc_prev + hire
        per_unit_hires[uid] = hires
    cap = [Decimal(str(c)) for c in world.pools_by_id()["eng_headcount"]["cap"]]
    peak = 0
    feasible = True
    for t in range(H_):
        tot = sum(per_unit_hires[u["id"]][t] for u in world.units)
        peak = max(peak, tot)
        if tot > int(cap[t]):
            feasible = False
    headcount_alloc = {u["id"]: [str(x) for x in per_unit_hires[u["id"]]]
                       for u in world.units}
    return headcount_alloc, feasible, peak


@prec50
def derive_headcount_exact(world, capital_alloc):
    """Capacity-EXACT net hires, layoffs allowed (package-only; the paper's generator never explored layoffs).

    Each quarter the unit's headcount is set to exactly ceil(customers/cph),
    hiring or laying off as needed. This is the action the paper scorer
    accepted but the hardness certificate never explored; it exists
    to document that gap, never to generate worlds.
    """
    H_ = world.horizon
    out = {}
    for u in world.units:
        uid = u["id"]
        cph = Decimal(str(u["customers_per_head"]))
        custs = _customers_for(world, uid, capital_alloc.get(uid, [u["overrides"]["marketing_spend"]] * H_))
        hc_prev = Decimal(world.models[uid]["nodes"]["headcount"]["initial"])
        hires = []
        for t in range(H_):
            req = Decimal(math.ceil(custs[t] / cph))
            hires.append(int(req - hc_prev))
            hc_prev = req
        out[uid] = [str(x) for x in hires]
    return out


# ---------------------------------------------------------------------------
# Allocation templates + search
# ---------------------------------------------------------------------------

def _profile(shape, intensity, H_):
    """A per-quarter weight vector for one unit, scaled by intensity in [0,1]."""
    if shape == "zero":
        base = [0.0] * H_
    elif shape == "flat":
        base = [1.0] * H_
    elif shape == "front":      # heavy q1-q2, taper (avoid q0 to not inflate base)
        base = [0.3, 1.0, 1.0, 0.7, 0.4, 0.2]
    elif shape == "early":      # includes q0
        base = [1.0, 1.0, 0.8, 0.5, 0.3, 0.2]
    elif shape == "back":
        base = [0.2, 0.4, 0.6, 0.8, 1.0, 1.0]
    else:
        raise ValueError(shape)
    return [b * intensity for b in base[:H_]]


def _scale_to_cap(weights_by_unit, cap):
    """Turn raw per-unit weight vectors into dollar allocations that never exceed
    the per-quarter pool cap (scale down a quarter proportionally if the raw sum
    exceeds the cap)."""
    units = list(weights_by_unit)
    H_ = len(cap)
    alloc = {u: [0.0] * H_ for u in units}
    UNIT_MAX = 140000.0  # a single unit can absorb at most this much marketing/q
    for t in range(H_):
        raw = {u: weights_by_unit[u][t] * UNIT_MAX for u in units}
        tot = sum(raw.values())
        capt = float(cap[t])
        if tot > capt and tot > 0:
            f = capt / tot
            raw = {u: raw[u] * f for u in units}
        for u in units:
            alloc[u][t] = round(raw[u], 2)
    return {u: [str(v) for v in alloc[u]] for u in units}


def evaluate_candidate(world, capital_alloc):
    """Attach the derived headcount, score (paper ruleset), return (passes, multiple, verdict, scenario)."""
    headcount_alloc, _, _ = derive_headcount(world, capital_alloc)
    scenario = {"allocation": {"growth_capital": capital_alloc,
                               "eng_headcount": headcount_alloc}}
    v = score_scenario(world, scenario)
    mult = Decimal(v["goal_multiple_actual"]) if v["goal_multiple_actual"] else Decimal(-1)
    return v["fully_valid"] or (not v["violations"]), mult, v, scenario


def evaluate_candidate_v1(world, capital_alloc):
    """As :func:`evaluate_candidate` but scored by the v1 ruleset."""
    headcount_alloc, _, _ = derive_headcount(world, capital_alloc)
    scenario = {"allocation": {"growth_capital": capital_alloc,
                               "eng_headcount": headcount_alloc}}
    v = score_scenario_v1(world, scenario)
    mult = Decimal(v["goal_multiple_actual"]) if v["goal_multiple_actual"] else Decimal(-1)
    return v["fully_valid"] or (not v["violations"]), mult, v, scenario


def search_templates():
    """The 729 (shapes, intensities) templates in the paper's enumeration order."""
    return list(itertools.product(
        itertools.product(["front", "early", "flat"], ["zero", "back", "flat"], ["front", "flat", "zero"]),
        itertools.product([0.5, 0.75, 1.0], repeat=3)))


def search_optimum(world, ruleset="paper"):
    """Enumerate structured capital templates; return the feasible allocation with
    the highest multiple (the careful optimum) and the full ranked log.

    ``ruleset="v1"`` scores each candidate with the v1 scorer (used to
    re-certify hardness; the candidates are identical because derived hires
    are whole and non-negative and capital is non-negative).
    """
    score_fn = evaluate_candidate if ruleset == "paper" else evaluate_candidate_v1
    uids = [u["id"] for u in world.units]
    comp, cow, sec = uids  # alpha (compounder), beta (cow), gamma (secondary)
    cap = world.pools_by_id()["growth_capital"]["cap"]
    shapes = {
        comp: ["front", "early", "flat"],
        cow: ["zero", "back", "flat"],
        sec: ["front", "flat", "zero"],
    }
    intens = [0.5, 0.75, 1.0]
    best = None
    feasible_mults = []
    all_results = []
    for sc, ss, sg in itertools.product(shapes[comp], shapes[cow], shapes[sec]):
        for ic, ico, ig in itertools.product(intens, repeat=3):
            wbu = {
                comp: _profile(sc, ic, H),
                cow: _profile(ss, ico, H),
                sec: _profile(sg, ig, H),
            }
            cap_alloc = _scale_to_cap(wbu, cap)
            # honor the headcount pool: skip candidates that overrun it
            _, hc_ok, peak = derive_headcount(world, cap_alloc)
            passes_cons, mult, v, scenario = score_fn(world, cap_alloc)
            no_viol = not v["violations"]
            all_results.append({
                "shapes": [sc, ss, sg], "intens": [ic, ico, ig],
                "multiple": str(mult), "violations": v["violations"],
                "hc_peak": peak,
            })
            if no_viol:
                feasible_mults.append(mult)
                if best is None or mult > best[0]:
                    best = (mult, cap_alloc, scenario, v)
    return best, feasible_mults, all_results


# ---------------------------------------------------------------------------
# Naive baselines (the unaided heuristics we want to FAIL)
# ---------------------------------------------------------------------------

def naive_capital_allocations(world):
    """The four named naive heuristics' capital allocations (make_world.py:278-298)."""
    uids = [u["id"] for u in world.units]
    comp, cow, sec = uids
    cap = world.pools_by_id()["growth_capital"]["cap"]
    out = {}
    # do-nothing: default marketing for each unit
    out["do_nothing"] = {u["id"]: [u["overrides"]["marketing_spend"]] * H for u in world.units}
    # equal split of the full pool every quarter
    out["equal_split"] = {u: [str(round(float(cap[t]) / 3, 2)) for t in range(H)] for u in uids}
    # all capital to the highest-ACV unit (cash-cow) — the seductive wrong move
    out["all_to_high_acv"] = {comp: ["0"] * H, sec: ["0"] * H, cow: [cap[t] for t in range(H)]}
    # all capital to the compounder, FULL pool, ignoring the cash floor / caps interplay
    out["all_to_compounder_maxed"] = {cow: ["0"] * H, sec: ["0"] * H, comp: [cap[t] for t in range(H)]}
    return out


def naive_baselines(world):
    """make_world.naive_baselines verbatim (paper ruleset)."""
    scored = {}
    for name, cap_alloc in naive_capital_allocations(world).items():
        passes, mult, v, scenario = evaluate_candidate(world, cap_alloc)
        scored[name] = {
            "multiple": str(mult), "fully_valid": v["fully_valid"],
            "violations": v["violations"], "scenario": scenario,
        }
    return scored


@prec50
def flat_split_capital(world, pct):
    """Flat equal split of ``pct``% of the growth cap across the units, every quarter."""
    cap = world.pools_by_id()["growth_capital"]["cap"]
    n = len(world.units)
    return {u["id"]: [str((Decimal(str(cap[t])) * Decimal(pct) / Decimal(100) / Decimal(n))
                          .quantize(Decimal("0.01"))) for t in range(world.horizon)]
            for u in world.units}


@prec50
def flat_split_candidates(world, pcts=range(1, 101)):
    """The E23-D5 naive family: flat split at x% of cap, scored both ways.

    For each pct returns ``{"pct", "v1": {fully_valid, multiple, violations}
    (clamped, certificate-space hires), "paper_layoffs": {...} (capacity-exact
    hires with layoffs, scored by the paper ruleset)}``. Under v1 none may pass
    (asserted by make_worlds.certify_v1); under the paper scorer with layoffs
    some pct passes in every shipped world, which is the documented gap.
    """
    rows = []
    for pct in pcts:
        capital = flat_split_capital(world, pct)
        hc, _, _ = derive_headcount(world, capital)
        v1 = score_scenario_v1(world, {"allocation": {"growth_capital": capital, "eng_headcount": hc}})
        hc_x = derive_headcount_exact(world, capital)
        pv = score_scenario(world, {"allocation": {"growth_capital": capital, "eng_headcount": hc_x}})
        rows.append({
            "pct": pct,
            "v1": {"fully_valid": v1["fully_valid"], "multiple": v1["goal_multiple_actual"],
                   "violations": v1["violations"]},
            "paper_layoffs": {"fully_valid": pv["fully_valid"], "multiple": pv["goal_multiple_actual"],
                              "violations": pv["violations"]},
        })
    return rows
