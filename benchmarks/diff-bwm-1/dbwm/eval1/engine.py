"""Eval-1 world engine: the deterministic company evaluator and its checks.

Ported verbatim from the paper harness ``oracle.py`` (evaluate L86-161,
check_constraints L172-200, goal_multiple L203-210). The Python evaluator is
NORMATIVE for diff-bwm-1: it defines the worlds. The paper described it as a
re-implementation of a private engine that was "cross-checked" against a live
service; no artifact of that cross-check exists, and the live backend is not
part of this package (docs/instrument-changes/eval1.md, E1-G03, E1-T04).

Decimal context. The paper set ``getcontext().prec = 50`` at import, which only
affects the importing thread. diff-bwm-1 serves this engine from a threaded
oracle server, where a fresh thread would silently get the default 28-digit
context and different figures. Every public entry point here therefore runs
under an explicit, fixed context equal to the paper's main-thread context
(prec 50, ROUND_HALF_EVEN, default traps), independent of the caller's thread.
"""

from __future__ import annotations

import functools
import json
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from pathlib import Path

from . import _paths

PREC = 50


def paper_context() -> Context:
    """The decimal context the paper's single-threaded harness computed under."""
    return Context(
        prec=PREC,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )


def prec50(fn):
    """Run ``fn`` under the paper's decimal context (see module docstring)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with localcontext(paper_context()):
            return fn(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Evaluator - mirrors the original operating-model semantics:
#   - scenario bindings rebind leaves only; series length must equal horizon
#   - within-period topological order over current-period refs
#   - `prior` (self) at t=0 -> roll-forward initial
#   - `prior_ref` at t=0 -> target's initial if roll-forward else 0
# (verbatim from the paper harness)
# ---------------------------------------------------------------------------

def _cur_refs(f):
    op = f["op"]
    if op == "ref":
        return [f["id"]]
    if op in ("add", "sub", "mul", "div"):
        return _cur_refs(f["lhs"]) + _cur_refs(f["rhs"])
    return []


@prec50
def evaluate(model, bindings=None):
    """Project every node across the horizon. Returns a list of {driver: Decimal}."""
    horizon = model["horizon"]
    nodes = model["nodes"]
    bindings = bindings or {}
    leaves = {}
    for nid, n in nodes.items():
        if n["kind"] == "leaf":
            series_vals = bindings.get(nid, n["default"]["values"])
            if len(series_vals) != horizon:
                raise ValueError(
                    f"binding length mismatch for {nid}: got {len(series_vals)}, "
                    f"need {horizon}")
            leaves[nid] = [Decimal(str(v)) for v in series_vals]
    for nid in bindings:
        if nid not in leaves:
            raise ValueError(f"binding {nid!r} is not a leaf driver")

    deps = {nid: [d for d in _cur_refs(n["formula"]) if d not in leaves]
            for nid, n in nodes.items() if n["kind"] != "leaf"}
    order, resolved = [], set(leaves)
    pending = dict(deps)
    while pending:
        ready = [nid for nid, ds in pending.items()
                 if all(d in resolved for d in ds)]
        if not ready:
            raise ValueError(f"cycle among {sorted(pending)}")
        for nid in sorted(ready):
            order.append(nid)
            resolved.add(nid)
            del pending[nid]

    periods = []
    for t in range(horizon):
        vals = {nid: leaves[nid][t] for nid in leaves}
        prior_vals = periods[-1] if periods else None

        def pval(nid):
            if prior_vals is not None:
                n = nodes.get(nid)
                init = (Decimal(n["initial"])
                        if n and n["kind"] == "roll_forward" else Decimal(0))
                return prior_vals.get(nid, init)
            n = nodes.get(nid)
            if n and n["kind"] == "roll_forward":
                return Decimal(n["initial"])
            return Decimal(0)

        def ev(f, self_id):
            op = f["op"]
            if op == "const":
                return Decimal(f["value"])
            if op == "ref":
                return vals[f["id"]]
            if op == "prior":
                return pval(self_id)
            if op == "prior_ref":
                return pval(f["id"])
            a = ev(f["lhs"], self_id)
            b = ev(f["rhs"], self_id)
            if op == "add":
                return a + b
            if op == "sub":
                return a - b
            if op == "mul":
                return a * b
            if op == "div":
                if b == 0:
                    raise ZeroDivisionError(self_id)
                return a / b
            raise ValueError(f"unknown op {op}")

        for nid in order:
            vals[nid] = ev(nodes[nid]["formula"], nid)
        periods.append(vals)
    return periods


def series(periods, driver):
    return [p[driver] for p in periods]


# ---------------------------------------------------------------------------
# Constraint + goal checking (the "test suite"), verbatim
# ---------------------------------------------------------------------------

@prec50
def check_constraints(model, periods, bindings, tier):
    """Return a per-constraint detail list."""
    details = []
    for c in tier["constraints"]:
        limit = Decimal(c["limit"])
        kind = c["kind"]
        ok = True
        actual = None
        if kind == "series_max":
            actual = max(series(periods, c["driver"]))
            ok = actual <= limit
        elif kind == "series_min":
            actual = min(series(periods, c["driver"]))
            ok = actual >= limit
        elif kind == "binding_max":
            vals = bindings.get(c["driver"])
            if vals is None:
                vals = model["nodes"][c["driver"]]["default"]["values"]
            actual = max(Decimal(str(v)) for v in vals)
            ok = actual <= limit
        else:
            raise ValueError(f"unknown constraint kind {kind!r}")
        details.append({
            "id": c["id"], "kind": kind, "driver": c["driver"],
            "limit": str(limit), "actual": str(actual), "ok": ok,
            "description": c.get("description", ""),
        })
    return details


@prec50
def goal_multiple(periods, goal):
    metric = goal["metric"]
    b = int(goal.get("baseline_period", 0))
    t = int(goal.get("target_period", len(periods) - 1))
    base = periods[b][metric]
    if base == 0:
        return None
    return periods[t][metric] / base


# ---------------------------------------------------------------------------
# World discovery (package-relative; replaces the paper's repo-layout rglob)
# ---------------------------------------------------------------------------

_CACHE: dict[tuple[str, str], tuple[dict, dict]] = {}


def worlds_dir() -> Path:
    return _paths.private("worlds")


def discover_models() -> dict[str, dict]:
    """model_id -> {"model": path, "tiers": path, "category": str}."""
    out = {}
    root = worlds_dir()
    if not root.is_dir():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        mfile = d / "operating-model.json"
        tiers = d / "eval-tiers.json"
        if not (mfile.exists() and tiers.exists()):
            continue
        mid = json.loads(mfile.read_text())["id"]
        cat = json.loads(tiers.read_text()).get("category", "")
        out[mid] = {"model": mfile, "tiers": tiers, "category": cat}
    return out


def load_model_and_tiers(model_id: str) -> tuple[dict, dict]:
    """Load (operating model, eval-tiers doc) for a company. Cached per root.

    Returned dicts are shared; callers must not mutate them.
    """
    root = str(worlds_dir())
    key = (root, model_id)
    if key not in _CACHE:
        d = worlds_dir() / model_id
        mfile, tfile = d / "operating-model.json", d / "eval-tiers.json"
        if not (mfile.exists() and tfile.exists()):
            known = ", ".join(sorted(discover_models()))
            raise KeyError(f"unknown eval-1 company {model_id!r}; known: {known}")
        model = json.loads(mfile.read_text())
        tiers_doc = json.loads(tfile.read_text())
        if model.get("id") != model_id:
            raise ValueError(f"{mfile}: id {model.get('id')!r} != directory {model_id!r}")
        _CACHE[key] = (model, tiers_doc)
    return _CACHE[key]


def load_calibration() -> list[dict]:
    """The scorer-only calibration report (naive + careful reference plans)."""
    return json.loads(_paths.private("calibration", "report.json").read_text())
