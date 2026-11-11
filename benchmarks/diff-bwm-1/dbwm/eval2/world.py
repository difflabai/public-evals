"""Coupled-world engine: a portfolio of unit operating models sharing pools.

Ported from the paper harness ``bwm_coupled/world.py`` (class World,
build_brief, selftest). The numerical behaviour is unchanged line for line;
what changed is plumbing:

* Base models are loaded from ``private/eval2/base_models/<id>.operating-model.json``
  (the paper imported ``discover_models`` from eval 1's ``bwm_theory/oracle.py``
  through a ``sys.path`` hack and rglobbed ``examples/data/bwm-scenario-matrix``;
  world.py:68-69, 86).
* Malformed worlds raise :class:`WorldSpecError` instead of ``SystemExit``
  (the messages are verbatim).
* Every numeric method runs in the paper's decimal context (see engine.prec50).
* The scorer (``score_scenario``) lives in :mod:`dbwm.eval2.score`; the CLI is
  gone (``dbwm`` subcommands and :mod:`dbwm.eval2.make_worlds` replace it).

THE TASK (from the paper module docstring, abridged)
A *world* couples several unit operating-models through shared per-quarter
resource pools no single unit owns (``growth_capital`` feeds each unit's
``marketing_spend``; ``eng_headcount`` feeds each unit's
``hires_per_quarter``). The agent must discover a per-quarter allocation of
every pool that hits a parent-level goal (combined revenue multiple) while the
combined cash floor, combined margin floor, per-unit capacity and pool caps
all hold. Allocation == program, evaluating the world == running it, goal and
limits == test suite, pass/fail == verdict.

INTERFACE INVARIANTS (enforced at load)
  - every pool's fed lever is a leaf in every unit
  - the fed lever carries the same unit string across the boundary
  - allocation/cap series lengths equal the horizon
  - the parent rollup is derived from unit trajectories, never entered
"""

from __future__ import annotations

import copy
import functools
import json
from decimal import Decimal
from pathlib import Path

from dbwm.core import paths as _paths

from .engine import evaluate, prec50

# Parent-level series rolled up from the units. cash/revenue/gross_profit/net_burn
# are additive; gross_margin is a ratio of summed parts.
ADDITIVE_DRIVERS = ["revenue", "gross_profit", "cogs", "net_burn", "cash",
                    "total_opex", "payroll_cost", "marketing_spend", "headcount",
                    "customers", "new_customers"]


class WorldSpecError(ValueError):
    """A world spec violates an interface invariant (the paper raised SystemExit)."""


def base_models_dir() -> Path:
    return _paths.private("eval2", "base_models")


@functools.lru_cache(maxsize=None)
def _base_model_text(path_str: str) -> str:
    return Path(path_str).read_text()


def load_base_model(base_id: str) -> dict:
    """Return a fresh copy of the base operating model ``base_id``.

    The paper resolved ids through eval 1's model registry; the only base model
    evals 2/3 use is ``sprout-crm`` and it is vendored on its own.
    """
    path = base_models_dir() / f"{base_id}.operating-model.json"
    if not path.exists():
        raise WorldSpecError(f"unknown base_model {base_id!r}")
    return json.loads(_base_model_text(str(path)))


# ---------------------------------------------------------------------------
# Unit-model resolution: a unit = a base operating-model + overrides
# ---------------------------------------------------------------------------

def _resolve_unit_model(unit, horizon):
    """Build the resolved operating-model for a unit: load its base model and
    apply leaf-default + roll-forward-initial overrides. The agent never edits
    these; they are the unit's FIXED economics. Only the pool-fed levers move."""
    try:
        model = load_base_model(unit["base_model"])
    except WorldSpecError:
        raise WorldSpecError(f"unit {unit['id']}: unknown base_model {unit['base_model']!r}")
    if model["horizon"] != horizon:
        raise WorldSpecError(
            f"unit {unit['id']}: base horizon {model['horizon']} != world {horizon}")
    model["id"] = unit["id"]
    model["name"] = unit.get("name", unit["id"])
    ov = unit.get("overrides", {})
    for nid, val in ov.items():
        if nid.endswith("_initial"):
            target = nid[: -len("_initial")]
            if target not in model["nodes"] or model["nodes"][target]["kind"] != "roll_forward":
                raise WorldSpecError(f"unit {unit['id']}: {target} is not a roll_forward")
            model["nodes"][target]["initial"] = str(val)
        else:
            if nid not in model["nodes"] or model["nodes"][nid]["kind"] != "leaf":
                raise WorldSpecError(f"unit {unit['id']}: override {nid} is not a leaf")
            model["nodes"][nid]["default"] = {"values": [str(val)] * horizon}
    return model


# ---------------------------------------------------------------------------
# World loading + interface invariants
# ---------------------------------------------------------------------------

class World:
    def __init__(self, spec):
        self.spec = spec
        self.id = spec["id"]
        self.name = spec.get("name", spec["id"])
        self.horizon = int(spec["horizon"])
        self.units = spec["units"]
        self.pools = spec["pools"]
        self.goal = spec["goal"]
        self.parent_constraints = spec.get("parent_constraints", [])
        self.unit_constraints = spec.get("unit_constraints", [])
        self.interface = spec.get("interface", "consistent")  # or "broken"
        self.models = {u["id"]: _resolve_unit_model(u, self.horizon) for u in self.units}
        self._check_invariants()

    def pools_by_id(self):
        return {p["id"]: p for p in self.pools}

    # -- the type system: reject malformed worlds at load --------------------
    def _check_invariants(self):
        H = self.horizon
        unit_ids = [u["id"] for u in self.units]
        if len(set(unit_ids)) != len(unit_ids):
            raise WorldSpecError("duplicate unit ids")
        for pool in self.pools:
            lever = pool["feeds_lever"]
            unit_str = None
            for uid, m in self.models.items():
                node = m["nodes"].get(lever)
                if node is None or node["kind"] != "leaf":
                    raise WorldSpecError(
                        f"interface invariant: pool {pool['id']} feeds {lever!r} "
                        f"which is not a leaf of unit {uid}")
                u = node.get("unit", "")
                if unit_str is None:
                    unit_str = u
                elif u != unit_str:
                    raise WorldSpecError(
                        f"interface invariant: lever {lever!r} has unit {u!r} in "
                        f"{uid} but {unit_str!r} elsewhere — currencies must match")
            if len(pool["cap"]) != H:
                raise WorldSpecError(f"pool {pool['id']} cap length != horizon {H}")
        # capacity params present for every unit referenced by a capacity constraint
        for c in self.unit_constraints:
            if c["kind"] == "unit_headcount_capacity":
                for u in self.units:
                    if "customers_per_head" not in u:
                        raise WorldSpecError(
                            f"unit {u['id']} needs customers_per_head for capacity")

    # -- evaluation ----------------------------------------------------------
    @prec50
    def evaluate(self, allocation):
        """Return {"units": {uid: periods}, "parent": {driver: [Decimal]}}.

        Each unit is projected by the EXACT per-unit engine with its pool slices
        bound to the fed levers; the parent is DERIVED by summation, never entered.

        Paper defect E23-D4, kept here for the ``paper`` ruleset: a unit missing
        from a pool keeps the lever's resolved default (40k-52k marketing, 1
        hire per quarter) and :meth:`check` does not count that default against
        the pool cap. The ``v1`` ruleset rejects incomplete allocations before
        they get here (``score.validate_v1``).
        """
        H = self.horizon
        unit_periods = {}
        for u in self.units:
            uid = u["id"]
            bindings = {}
            for pool in self.pools:
                slice_ = allocation.get(pool["id"], {}).get(uid)
                if slice_ is None:
                    # an unallocated unit keeps the lever's resolved default
                    continue
                if len(slice_) != H:
                    raise ValueError(
                        f"allocation[{pool['id']}][{uid}] has {len(slice_)} values, "
                        f"horizon is {H}")
                bindings[pool["feeds_lever"]] = [str(x) for x in slice_]
            unit_periods[uid] = evaluate(self.models[uid], bindings)

        parent = {d: [Decimal(0)] * H for d in ADDITIVE_DRIVERS}
        for uid, periods in unit_periods.items():
            scale = self._interface_scale(uid)
            for t in range(H):
                for d in ADDITIVE_DRIVERS:
                    if d in periods[t]:
                        parent[d][t] += periods[t][d] * scale.get(d, Decimal(1))
        parent["gross_margin_pct"] = [
            (parent["gross_profit"][t] / parent["revenue"][t])
            if parent["revenue"][t] != 0 else Decimal(0) for t in range(H)]
        return {"units": unit_periods, "parent": parent}

    def _interface_scale(self, uid):
        """consistent -> identity. broken -> a deterministic, NON-monotone unit
        mismatch on cross-boundary values for one unit (e.g. its revenue read in
        thousands while its costs read in ones): each unit stays internally fine,
        but the rollup is internally INCONSISTENT — the lesion the design calls
        for. Symmetric/varied so it does not push outcomes one direction."""
        if self.interface != "broken":
            return {}
        broken_unit = self.spec.get("broken_unit", self.units[0]["id"])
        if uid != broken_unit:
            return {}
        # revenue scaled up, gross_profit scaled down -> margin + multiple both
        # corrupted, in opposite directions (not monotone).
        return {"revenue": Decimal("1.5"), "gross_profit": Decimal("0.7")}

    # -- the test suite ------------------------------------------------------
    @prec50
    def check(self, allocation, evaluated=None):
        ev = evaluated or self.evaluate(allocation)
        parent, units = ev["parent"], ev["units"]
        H = self.horizon
        details = []

        # (a) pool caps: sum of slices <= cap each quarter
        for pool in self.pools:
            cap = [Decimal(str(c)) for c in pool["cap"]]
            worst_q, worst_over = None, Decimal(0)
            spent = [Decimal(0)] * H
            for u in self.units:
                sl = allocation.get(pool["id"], {}).get(u["id"])
                if sl is None:
                    continue
                for t in range(H):
                    spent[t] += Decimal(str(sl[t]))
            ok = True
            for t in range(H):
                over = spent[t] - cap[t]
                if over > 0:
                    ok = False
                    if over > worst_over:
                        worst_over, worst_q = over, t
            details.append({
                "id": f"pool_cap:{pool['id']}", "kind": "pool_cap",
                "pool": pool["id"], "ok": ok,
                "worst_quarter": worst_q, "worst_overage": str(worst_over),
                "spent": [str(x) for x in spent], "cap": [str(c) for c in cap],
                "description": f"sum of {pool['id']} allocation must be <= cap each quarter",
            })

        # (b) parent series constraints (cash floor, margin floor, ...)
        for c in self.parent_constraints:
            if c["kind"] == "parent_series_min":
                ser = parent[c["metric"]]
                lim = Decimal(str(c["limit"]))
                idx = min(range(H), key=lambda t: ser[t])
                actual = ser[idx]
                details.append({
                    "id": c["id"], "kind": c["kind"], "metric": c["metric"],
                    "limit": str(lim), "actual": str(actual), "worst_quarter": idx,
                    "ok": actual >= lim, "description": c.get("description", ""),
                })
            elif c["kind"] == "parent_margin_min":
                ser = parent["gross_margin_pct"]
                lim = Decimal(str(c["limit"]))
                idx = min(range(H), key=lambda t: ser[t])
                actual = ser[idx]
                details.append({
                    "id": c["id"], "kind": c["kind"], "metric": "gross_margin_pct",
                    "limit": str(lim), "actual": str(actual), "worst_quarter": idx,
                    "ok": actual >= lim, "description": c.get("description", ""),
                })
            else:
                raise ValueError(f"unknown parent constraint kind {c['kind']!r}")

        # (c) unit capacity: headcount must cover customers/customers_per_head
        for c in self.unit_constraints:
            if c["kind"] != "unit_headcount_capacity":
                raise ValueError(f"unknown unit constraint kind {c['kind']!r}")
            for u in self.units:
                cph = Decimal(str(u["customers_per_head"]))
                periods = units[u["id"]]
                worst_q, worst_short = None, Decimal(0)
                ok = True
                for t in range(H):
                    need = periods[t]["customers"] / cph
                    have = periods[t]["headcount"]
                    short = need - have
                    if short > 0:
                        ok = False
                        if short > worst_short:
                            worst_short, worst_q = short, t
                details.append({
                    "id": f"capacity:{u['id']}", "kind": "unit_headcount_capacity",
                    "unit": u["id"], "ok": ok, "worst_quarter": worst_q,
                    "worst_short_heads": str(worst_short),
                    "description": (f"{u['id']} headcount must be >= customers/"
                                    f"{u['customers_per_head']} every quarter"),
                })

        violations = [d["id"] for d in details if not d["ok"]]
        return details, violations

    @prec50
    def goal_multiple(self, evaluated):
        g = self.goal
        rev = evaluated["parent"][g["metric"]]
        b = int(g.get("baseline_period", 0))
        t = int(g.get("target_period", self.horizon - 1))
        if rev[b] == 0:
            return None
        return rev[t] / rev[b]

    # -- marginal returns: the coupling-exploitation oracle (judge-free) ------
    @prec50
    def marginal_returns(self, allocation, pool_id, eps="1000"):
        """For the given pool, finite-difference each unit's marginal effect on the
        combined revenue multiple of bumping its quarter-0 allocation by eps. This
        is a DECIDABLE function of the exact engine, so 'did the agent route the
        pool toward the higher marginal-return unit' needs no LLM judge."""
        eps = Decimal(str(eps))
        base_mult = self.goal_multiple(self.evaluate(allocation))
        out = {}
        for u in self.units:
            uid = u["id"]
            bumped = copy.deepcopy(allocation)
            sl = bumped.setdefault(pool_id, {}).get(uid)
            if sl is None:
                pool = next(p for p in self.pools if p["id"] == pool_id)
                lever = pool["feeds_lever"]
                base_default = self.models[uid]["nodes"][lever]["default"]["values"]
                sl = [str(v) for v in base_default]
            sl = [str(Decimal(str(v))) for v in sl]
            sl[0] = str(Decimal(sl[0]) + eps)
            bumped[pool_id][uid] = sl
            m = self.goal_multiple(self.evaluate(bumped))
            out[uid] = (m - base_mult) if (m is not None and base_mult is not None) else None
        return out


# ---------------------------------------------------------------------------
# Loading worlds shipped with the package
# ---------------------------------------------------------------------------

def world_spec_path(world_id: str) -> Path:
    """Scorer/oracle-only true spec. Never staged into a sandbox."""
    return _paths.private("eval2", "worlds", world_id, "world.json")


def reference_path(world_id: str) -> Path:
    """Answer key (careful_optimum, naive baselines, hardness floor). Scorer-only."""
    return _paths.private("eval2", "worlds", world_id, "reference.json")


def brief_path(world_id: str) -> Path:
    """Agent-facing brief (public)."""
    return _paths.data("eval2", "worlds", world_id, "brief.json")


def load_world_spec(world_id: str) -> dict:
    path = world_spec_path(world_id)
    if not path.exists():
        raise FileNotFoundError(f"eval2 world {world_id!r} not found (no world.json under private/eval2/worlds)")
    return json.loads(path.read_text())


@functools.lru_cache(maxsize=64)
def _cached_world(world_id: str, interface: str, private_root: str) -> World:
    spec = load_world_spec(world_id)
    if interface != spec.get("interface", "consistent"):
        spec = copy.deepcopy(spec)
        spec["interface"] = interface
    return World(spec)


def load_world(world_id: str, interface: str | None = None) -> World:
    """World object for a shipped world id (cached; World methods do not mutate it).

    ``interface="broken"`` gives the broken-interface lesion world the paper's
    ``world_query --mode broken`` built (world_query.py:96-99).
    """
    spec_iface = interface or "consistent"
    return _cached_world(world_id, spec_iface, str(_paths.private_root()))


def load_world_file(path) -> World:
    return World(json.loads(Path(path).read_text()))


def load_reference(world_id: str) -> dict:
    return json.loads(reference_path(world_id).read_text())


def load_brief(world_id: str) -> dict:
    return json.loads(brief_path(world_id).read_text())


# ---------------------------------------------------------------------------
# Agent brief — what the agent is told (no answer-key leakage)
# ---------------------------------------------------------------------------

@prec50
def build_brief(world):
    H = world.horizon
    units = []
    for u in world.units:
        m = world.models[u["id"]]
        base = evaluate(m)  # do-nothing trajectory with this unit's economics
        econ = {}
        for k in ("acv", "churn_rate", "conversion_rate", "cost_per_lead",
                  "gm_rate", "salary_per_head", "fixed_opex"):
            if k in m["nodes"] and m["nodes"][k]["kind"] == "leaf":
                econ[k] = m["nodes"][k]["default"]["values"][0]
        units.append({
            "id": u["id"],
            "name": u.get("name", u["id"]),
            "customers_per_head": u.get("customers_per_head"),
            "economics": econ,
            "starting": {
                "cash": str(m["nodes"]["cash"]["initial"]),
                "customers": str(m["nodes"]["customers"]["initial"]),
                "headcount": str(m["nodes"]["headcount"]["initial"]),
            },
            "base_trajectory": {
                d: [str(p[d]) for p in base]
                for d in ["revenue", "customers", "cash", "net_burn",
                          "gross_margin_pct", "headcount"] if d in base[0]
            },
        })
    pools = [{
        "id": p["id"], "label": p.get("label", p["id"]),
        "unit": p.get("unit", ""), "feeds_lever": p["feeds_lever"],
        "cap_per_quarter": [str(c) for c in p["cap"]],
    } for p in world.pools]
    return {
        "world_id": world.id, "name": world.name, "horizon": H,
        "period_grain": "quarterly",
        "goal": dict(world.goal),
        "parent_constraints": world.parent_constraints,
        "unit_constraints": [
            {"id": f"capacity:{u['id']}", "kind": "unit_headcount_capacity",
             "unit": u["id"],
             "rule": f"headcount >= customers/{u['customers_per_head']} every quarter"}
            for u in world.units],
        "pools": pools,
        "units": units,
        "notes": world.spec.get("notes", ""),
    }


# ---------------------------------------------------------------------------
# Selftest — prove the coupling layer is exact + invariants fire
# ---------------------------------------------------------------------------

def _selftest_spec():
    return {
        "schema": "bwm-coupled-world/1", "id": "selftest-world",
        "name": "selftest", "horizon": 6,
        "units": [
            {"id": "a", "base_model": "sprout-crm", "customers_per_head": "40",
             "overrides": {"churn_rate": "0.03", "cash_initial": "400000"}},
            {"id": "b", "base_model": "sprout-crm", "customers_per_head": "40",
             "overrides": {"churn_rate": "0.10", "cash_initial": "400000"}},
        ],
        "pools": [
            {"id": "growth_capital", "feeds_lever": "marketing_spend",
             "unit": "usd/quarter", "marginal_return_lever": True,
             "cap": ["120000"] * 6},
            {"id": "eng_headcount", "feeds_lever": "hires_per_quarter",
             "unit": "people/quarter", "cap": ["6"] * 6},
        ],
        "goal": {"metric": "revenue", "target_multiple": "1.5",
                 "baseline_period": 0, "target_period": 5},
        "parent_constraints": [
            {"id": "cash_floor", "kind": "parent_series_min", "metric": "cash",
             "limit": "0"},
            {"id": "margin_floor", "kind": "parent_margin_min", "limit": "0.5"},
        ],
        "unit_constraints": [{"kind": "unit_headcount_capacity"}],
    }


@prec50
def selftest_checks():
    """The paper's five engine checks (world.py:558-658) as (name, ok, detail) tuples.

    1 additivity, 2 margin identity, 3 pool-cap detection, 4 invariant
    rejection, 5 broken-interface isolation. A missing base model is a failed
    check, never a skip.
    """
    out = []
    try:
        spec = _selftest_spec()
        world = World(spec)
    except Exception as e:  # noqa: BLE001
        return [("world.selftest.load", False, f"cannot build the selftest world: {e}")]
    alloc = {
        "growth_capital": {"a": ["60000"] * 6, "b": ["50000"] * 6},
        "eng_headcount": {"a": ["2"] * 6, "b": ["1"] * 6},
    }
    ev = world.evaluate(alloc)

    # 1) additivity: the parent rollup equals the sum of the exact per-unit engine
    exp_rev = [Decimal(0)] * 6
    for uid, mk in (("a", {"marketing_spend": ["60000"] * 6, "hires_per_quarter": ["2"] * 6}),
                    ("b", {"marketing_spend": ["50000"] * 6, "hires_per_quarter": ["1"] * 6})):
        per = evaluate(world.models[uid], mk)
        for t in range(6):
            exp_rev[t] += per[t]["revenue"]
    bad = [t for t in range(6) if ev["parent"]["revenue"][t] != exp_rev[t]]
    out.append(("world.selftest.additivity", not bad,
                "parent revenue rollup == sum of exact per-unit engine (6 quarters)"
                if not bad else f"rollup mismatch at quarters {bad}"))

    # 2) margin identity: parent margin == sum(gp)/sum(rev)
    bad = [t for t in range(6)
           if ev["parent"]["gross_margin_pct"][t]
           != ev["parent"]["gross_profit"][t] / ev["parent"]["revenue"][t]]
    out.append(("world.selftest.margin_identity", not bad,
                "parent margin == sum(gross_profit)/sum(revenue) every quarter"
                if not bad else f"margin identity broken at {bad}"))

    # 3) pool-cap detection: over-allocate one quarter, expect a violation
    over = copy.deepcopy(alloc)
    over["growth_capital"]["a"][2] = "200000"  # 200k+50k=250k > 120k cap
    _, viol = world.check(over)
    out.append(("world.selftest.pool_cap", "pool_cap:growth_capital" in viol,
                "pool-cap over-allocation detected" if "pool_cap:growth_capital" in viol
                else "pool-cap over-allocation NOT detected"))

    # 4) interface invariant: a pool feeding a non-leaf must be rejected at load
    bad_spec = copy.deepcopy(spec)
    bad_spec["pools"][0]["feeds_lever"] = "revenue"  # computed, not a leaf
    try:
        World(bad_spec)
        out.append(("world.selftest.invariant", False,
                    "invariant did NOT reject a pool feeding a computed node"))
    except WorldSpecError:
        out.append(("world.selftest.invariant", True,
                    "interface invariant rejects a pool feeding a non-leaf"))

    # 5) broken interface corrupts the rollup but each unit stays internally fine
    bspec = copy.deepcopy(spec)
    bspec["interface"] = "broken"
    bspec["broken_unit"] = "a"
    bw = World(bspec)
    bev = bw.evaluate(alloc)
    if bev["parent"]["revenue"][5] == ev["parent"]["revenue"][5]:
        out.append(("world.selftest.broken_interface", False,
                    "broken interface did not change the rollup"))
    else:
        per_a = bev["units"]["a"]
        recompute = per_a[5]["customers"] * Decimal(
            bw.models["a"]["nodes"]["acv"]["default"]["values"][5])
        ok = per_a[5]["revenue"] == recompute
        out.append(("world.selftest.broken_interface", ok,
                    "broken interface corrupts ONLY the rollup; units stay internally consistent"
                    if ok else "broken interface corrupted a unit's INTERNAL identity"))
    return out
