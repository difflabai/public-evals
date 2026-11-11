"""The exact per-unit projection engine shared by evals 2 and 3.

This is a verbatim copy of ``evaluate`` (+ ``_cur_refs``) from the paper
harness (``bwm_coupled/_model_engine.py``, itself a copy of eval 1's
``bwm_theory/oracle.evaluate``; the two bodies are identical). It is the
three-statement roll-forward engine that the paper cross-checked against the
live operating-model product to <1e-12. Nothing here knows about worlds,
answer keys or scoring: it is generic arithmetic over a node graph.

One deliberate difference from the paper file: the paper set the *global*
decimal context (``getcontext().prec = 50``) at import time. A global setting
only holds in the thread that imported the module; the per-trial oracle server
answers calls on worker threads, whose decimal context starts from
``decimal.DefaultContext`` (prec 28), so the same call would have returned
different digits there. Every public numeric entry point in ``dbwm.eval2``
therefore runs inside :func:`prec50`, a local context identical to the one the
paper ran under (prec 50, otherwise the stdlib defaults), and no global state
is touched. ``tests/eval2/test_eval2_engine_world.py`` checks thread independence.
"""

from __future__ import annotations

import functools
from decimal import Context, Decimal, localcontext

# The decimal context the paper scorer ran under: prec 50, every other field at
# the stdlib default (ROUND_HALF_EVEN, Emin/Emax +-999999, traps on
# InvalidOperation/DivisionByZero/Overflow).
PAPER_CONTEXT = Context(prec=50)


def prec50(fn):
    """Run ``fn`` inside a fresh copy of PAPER_CONTEXT (thread-safe, re-entrant)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with localcontext(PAPER_CONTEXT):
            return fn(*args, **kwargs)

    return wrapper


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
