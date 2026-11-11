"""Deterministic stub agent for the D3 self-supply arm (no LLM).

It reads ONLY what is staged (public/history.csv, public/task.json, TASK.md) and
writes the two deliverables, so a stub run exercises staging, the brief, the
per-attempt scorer and the gate report end to end. The figures are crude on
purpose (a weak agent): fixed structural guesses, "not identifiable" (0) for the
spillover, and naive last-four-quarters forecasts with the brief's pinned gm /
fixed, each multiplied by a small cell-hash jitter so reps differ but mostly agree.
The plan is the budget-scaled reference allocation at price 1.0.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Callable

from dbwm.core.types import Cell

from .brief import FIGURE_NAMES

_GM_RE = re.compile(r"^\s*gm\s*=\s*(\{.*\})\s*$", re.M)
_FIXED_RE = re.compile(r"^\s*fixed\s*=\s*(\{.*?\})", re.M)
_PROBE_RE = re.compile(r'^probe segment\s*=\s*"([^"]+)"', re.M)
_HORIZON_RE = re.compile(r"ALL (\d+) held-out quarters")


def _jitter(cell: Cell, label: str, width: float) -> float:
    """1 + u * width, u in [-1, 1), from sha256(cell_id | label)."""
    h = int.from_bytes(hashlib.sha256(f"{cell.cell_id}|{label}".encode()).digest()[:8], "big")
    return 1.0 + (h / 2 ** 64 * 2.0 - 1.0) * width


def _write(sandbox: Path, name: str, obj: Any) -> None:
    (sandbox / name).write_text(json.dumps(obj, indent=2) + "\n")


def stub_agent(cell: Cell, sandbox: Path, call_oracle: Callable[[str, dict], Any] | None = None) -> None:
    sandbox = Path(sandbox)
    task = json.loads((sandbox / "public" / "task.json").read_text())
    brief = (sandbox / "TASK.md").read_text()
    gm = json.loads(_GM_RE.search(brief).group(1))
    fixed = json.loads(_FIXED_RE.search(brief).group(1))
    probe = _PROBE_RE.search(brief).group(1)
    K = int(_HORIZON_RE.search(brief).group(1))
    rows = list(csv.DictReader((sandbox / "public" / "history.csv").open(newline="")))
    by_seg: dict[str, list[dict]] = {}
    for r in rows:
        by_seg.setdefault(r["segment"], []).append(r)
    last = {s: rs[-4:] for s, rs in by_seg.items()}
    rev_q = {s: math.fsum(float(r["revenue"]) for r in rs) / len(rs) for s, rs in last.items()}
    mkt_q = {s: math.fsum(float(r["marketing"]) for r in rs) / len(rs) for s, rs in last.items()}
    revenue = K * math.fsum(rev_q.values())
    profit = K * math.fsum(rev_q[s] * gm[s] - mkt_q[s] - fixed[s] for s in rev_q)
    end_customers = float(by_seg[probe][-1]["customers"])
    figures = {
        "price_elasticity_probe_segment": -1.0 * _jitter(cell, "elast", 0.005),
        "marketing_exponent_probe_segment": 0.5 * _jitter(cell, "mktexp", 0.005),
        "cross_segment_spillover_pct": 0.0,
        "pinned_plan_total_profit_usd": profit * _jitter(cell, "profit", 0.003),
        "pinned_plan_total_revenue_usd": revenue * _jitter(cell, "revenue", 0.003),
        "pinned_plan_end_customers_probe_segment": end_customers * _jitter(cell, "customers", 0.003),
    }
    claims = dict(figures)
    claims["confidence"] = {n: 0.3 for n in FIGURE_NAMES}
    claims["first_action"] = "Fit a log-log regression of the probe segment's signups on price and marketing."
    claims["rationale"] = ("stub: fixed structural guesses; spillover not identifiable from this history (0); "
                           "forecasts from the last four quarters with the pinned gm and fixed")
    _write(sandbox, "claims.json", claims)
    cap = float(task["budget_constraint"]["cap"])
    ref = {s: float(task["reference_marketing"][s]) for s in task["segments"]}
    tot = math.fsum(ref.values())
    _write(sandbox, "plan.json", {"plan": {s: {"marketing": ref[s] / tot * cap, "price": 1.0}
                                           for s in task["segments"]}})
