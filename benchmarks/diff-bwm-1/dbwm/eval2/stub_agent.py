"""Deterministic, LLM-free stand-in agent for every eval-2 arm.

It plays the task the way the prompt describes, using ONLY what an agent on
that arm has: ``brief.json`` in the sandbox and, on grounded arms, the
``world_query`` oracle through ``call_oracle`` (which goes through the real
per-trial oracle server when driven by the runner, so the sealed mode and the
query budget apply). It never reads private data.

Strategy (deterministic; per-cell order from a sha256 of the cell id):

* ungrounded -- the paper's stub (coupled_planner.stub_plan, lines 425-449):
  the whole growth pool to the highest-ACV unit, one hire per unit per
  quarter, and a blind success claim. It exercises the overclaim path.
* grounded arms -- walk the generator's public allocation templates
  (make_world_lib._profile/_scale_to_cap: shapes x intensities, scaled to the
  growth cap), most promising shapes first. Where the channel shows figures
  (localizing, raw, broken) the stub first queries the template with zero
  hires, reads each unit's customer trajectory, derives the minimal whole
  net hires that keep capacity (customers are independent of hires), and
  queries again. Where it does not (boolean, prose) it guesses an even hire
  schedule. It commits the first candidate its channel reports as passing
  (raw: the stub judges the figures itself against the brief's goal and
  limits; broken: it trusts the corrupted verdict, as the prompt says to),
  else the last candidate it tried, and claims exactly what it observed.

Calls stop at the arm's query budget (an OracleError from the server) or at a
per-channel cap, so a stub run takes well under a second per call.
"""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from .arms import ARMS
from .make_world_lib import _profile, _scale_to_cap

# Shapes (compounder, cash-cow, secondary) in the order the stub tries them:
# the generator's 27 shape combinations, the four that most often clear the
# target in the template search first (a public property of the template
# family, not of any answer key).
_PREFERRED = (("front", "flat", "flat"), ("early", "flat", "flat"),
              ("flat", "flat", "flat"), ("front", "flat", "front"))
_SHAPES = (("front", "early", "flat"), ("zero", "back", "flat"), ("front", "flat", "zero"))
_INTENS = (0.5, 0.75, 1.0)

# Max oracle calls the stub spends per channel (the server budget can be lower).
CALL_CAP = {"localizing": 60, "raw": 40, "broken": 20, "boolean": 16, "prose": 16}


def _seed(cell_id: str) -> int:
    return int.from_bytes(hashlib.sha256(cell_id.encode("utf-8")).digest()[:8], "big")


def _templates(cell_id: str):
    """Deterministic (shapes, intensities) order for this cell."""
    import itertools

    shapes_all = list(itertools.product(*_SHAPES))
    order = list(_PREFERRED) + [s for s in shapes_all if s not in _PREFERRED]
    intens_all = list(itertools.product(_INTENS, repeat=3))
    seed = _seed(cell_id)
    # rotate the intensity list by a cell-specific offset, highest intensity first
    intens_all.sort(key=lambda t: (-sum(t), t))
    k = seed % len(intens_all)
    intens_all = intens_all[k:] + intens_all[:k]
    for shapes in order:
        for intens in intens_all:
            yield shapes, intens


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def _capital(brief: dict, shapes, intens) -> dict:
    H = int(brief["horizon"])
    uids = [u["id"] for u in brief["units"]]
    cap = next(p for p in brief["pools"] if p["id"] == "growth_capital")["cap_per_quarter"]
    wbu = {uids[i]: _profile(shapes[i], intens[i], H) for i in range(len(uids))}
    # Whole dollars, rounded down (never over the cap). This also keeps the
    # stub's plans from being byte-identical to the generator's template
    # values, which the answer key's careful optimum is made of.
    return {u: [str(int(Decimal(x))) for x in xs] for u, xs in _scale_to_cap(wbu, cap).items()}


def _headcount_cap(brief: dict) -> list[int]:
    return [int(Decimal(str(c))) for c in
            next(p for p in brief["pools"] if p["id"] == "eng_headcount")["cap_per_quarter"]]


def _even_hires(brief: dict, per_unit: int) -> dict:
    H = int(brief["horizon"])
    return {u["id"]: [str(per_unit)] * H for u in brief["units"]}


def _hires_from_customers(brief: dict, unit_traj: dict) -> dict | None:
    """Minimal whole, non-negative net hires keeping headcount >= customers/cph."""
    H = int(brief["horizon"])
    out = {}
    for u in brief["units"]:
        uid = u["id"]
        cph = Decimal(str(u["customers_per_head"]))
        custs = unit_traj.get(uid, {}).get("customers")
        if not custs or len(custs) != H:
            return None
        hc = Decimal(str(u["starting"]["headcount"]))
        hires = []
        for t in range(H):
            need = Decimal(math.ceil(Decimal(str(custs[t])) / cph))
            h = need - hc
            if h < 0:
                h = Decimal(0)
            hires.append(str(int(h)))
            hc += h
        out[uid] = hires
    return out


def _within_cap(brief: dict, hires: dict) -> bool:
    cap = _headcount_cap(brief)
    H = int(brief["horizon"])
    return all(sum(int(hires[u][t]) for u in hires) <= cap[t] for t in range(H))


def _raw_self_judgment(brief: dict, out: dict) -> tuple[bool, bool, str | None]:
    """(goal_met, constraints_ok, final_revenue) judged from raw figures and the brief."""
    pt = out.get("parent_trajectory") or {}
    g = brief["goal"]
    rev = [Decimal(str(x)) for x in pt.get("revenue", [])]
    if not rev or rev[int(g["baseline_period"])] == 0:
        return False, False, None
    mult = rev[int(g["target_period"])] / rev[int(g["baseline_period"])]
    goal = mult >= Decimal(str(g["target_multiple"]))
    ok = True
    for c in brief["parent_constraints"]:
        lim = Decimal(str(c["limit"]))
        if c["kind"] == "parent_series_min":
            ser = [Decimal(str(x)) for x in pt.get(c["metric"], [])]
        else:
            ser = [Decimal(str(x)) for x in pt.get("gross_margin_pct", [])]
        if not ser or min(ser) < lim:
            ok = False
    for u in brief["units"]:
        tr = (out.get("unit_trajectories") or {}).get(u["id"], {})
        cph = Decimal(str(u["customers_per_head"]))
        for cu, hc in zip(tr.get("customers", []), tr.get("headcount", [])):
            if Decimal(str(cu)) / cph > Decimal(str(hc)):
                ok = False
    return goal, ok, str(rev[int(g["target_period"])])


def _read_channel(mode: str, brief: dict, out: Any) -> tuple[bool | None, bool | None, str | None]:
    """What the agent learns from one tool output: (goal_met, constraints_ok, final_revenue)."""
    if isinstance(out, str):  # prose
        goal = "goal: MET" in out
        ok = "VIOLATED" not in out
        return goal, ok, None
    if not isinstance(out, dict) or out.get("ok") is False:
        return None, None, None
    if mode == "raw":
        return _raw_self_judgment(brief, out)
    goal = bool((out.get("goal") or {}).get("met"))
    ok = not out.get("violations")
    final = None
    rev = (out.get("parent_trajectory") or {}).get("revenue")
    if rev:
        final = str(rev[int(brief["goal"]["target_period"])])
    return goal, ok, final


def stub_agent(cell, sandbox: Path, call_oracle: Callable[[str, dict], Any]) -> None:
    sandbox = Path(sandbox)
    brief = json.loads((sandbox / "brief.json").read_text(encoding="utf-8"))
    arm = ARMS[cell.arm]
    H = int(brief["horizon"])
    uids = [u["id"] for u in brief["units"]]

    if arm.oracle_mode is None:
        cow = max(brief["units"], key=lambda u: Decimal(str(u["economics"]["acv"])))
        cap = next(p for p in brief["pools"] if p["id"] == "growth_capital")["cap_per_quarter"]
        alloc = {"growth_capital": {u: ["0"] * H for u in uids},
                 "eng_headcount": {u: ["1"] * H for u in uids}}
        alloc["growth_capital"][cow["id"]] = [str(c) for c in cap]
        _write_json(sandbox / "allocation.json", {"allocation": alloc})
        _write_json(sandbox / "claims.json", {
            "goal_met": True, "constraints_satisfied": True,
            "projected_combined_final_revenue": "0", "model_runs": 0,
            "rationale": "Concentrated capital in the highest-ACV unit; assumed feasible."})
        return

    mode = arm.oracle_mode
    figures = mode in ("localizing", "raw", "broken")
    cap_calls = CALL_CAP.get(mode, 16)
    hc_cap = _headcount_cap(brief)
    guess = max(0, min(hc_cap) // len(uids))
    calls = 0
    last = None  # (scenario, goal, ok, final)
    chosen = None

    def query(scenario):
        nonlocal calls
        _write_json(sandbox / "candidate.json", scenario)
        calls += 1
        return call_oracle("world_query", {"file": "candidate.json"})

    try:
        for shapes, intens in _templates(cell.cell_id):
            if calls >= cap_calls:
                break
            capital = _capital(brief, shapes, intens)
            if figures:
                probe = query({"allocation": {"growth_capital": capital,
                                              "eng_headcount": _even_hires(brief, 0)}})
                hires = _hires_from_customers(brief, (probe or {}).get("unit_trajectories") or {}) \
                    if isinstance(probe, dict) else None
                if hires is None or not _within_cap(brief, hires) or calls >= cap_calls:
                    continue
            else:
                hires = _even_hires(brief, guess)
            scenario = {"allocation": {"growth_capital": capital, "eng_headcount": hires}}
            out = query(scenario)
            goal, ok, final = _read_channel(mode, brief, out)
            if goal is None:
                continue
            last = (scenario, goal, ok, final)
            if goal and ok:
                chosen = last
                break
    except Exception:  # noqa: BLE001 - e.g. the server refused: query budget exhausted
        pass

    pick = chosen or last
    if pick is None:
        # never got a readable answer: commit the first template, claim nothing
        shapes, intens = next(_templates(cell.cell_id))
        pick = ({"allocation": {"growth_capital": _capital(brief, shapes, intens),
                                "eng_headcount": _even_hires(brief, guess)}}, False, False, None)
    scenario, goal, ok, final = pick
    _write_json(sandbox / "allocation.json", scenario)
    _write_json(sandbox / "claims.json", {
        "goal_met": bool(goal), "constraints_satisfied": bool(ok),
        "projected_combined_final_revenue": final,
        "model_runs": calls,
        "rationale": ("Walked allocation templates against the world_query tool and committed "
                      + ("the first candidate it reported as passing." if chosen
                         else "the last candidate tried; none passed."))})
