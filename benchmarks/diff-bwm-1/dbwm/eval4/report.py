"""Eval-4 report for a NEW run (``dbwm report --run DIR`` calls ``EVAL.report``).

The generic per-arm tables (deterministic success under both denominators,
overclaim, exclusions, rails, conformance) come from ``dbwm.core.report``. This
adds what is specific to eval 4:

* the PRIMARY measure, judged item-pass on the dataset's 12 rubric checks, from
  ``dbwm eval4-judge`` output (``attempt-k/judge/panel.json``): 3-pass panel
  majority (``panel_majority``, min_valid 2; ties are judge_error, never a fail),
  intention-to-treat -- a scored attempt with no workbook fails every item, and
  a DESIGNED cell with no scored attempt also fails every item in the second
  denominator; Wilson intervals plus a two-sample trial-clustered bootstrap for
  the pre-registered contrasts (H1 executable - prose, H3 executable - verdict,
  H4 prose - baseline);
* calibration against the JUDGE (coarse and per-item, excluding A-07), with the
  checker-referenced figure kept beside it and labelled self-consistency;
* the deterministic checker (secondary), process (qa_check oracle calls, turns,
  cost) and the recalc status of every scored workbook.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any, Iterable, Sequence

from dbwm.core import report as core_report
from dbwm.core.types import RegimeConfig

CONDITION_ORDER = ["baseline", "checklist-prose", "checklist-verdict", "checklist-executable"]
BOOT_SEED = 12345
BOOT_B = 20000

CONTRASTS = (("H1", "checklist-executable", "checklist-prose"),
             ("H3", "checklist-executable", "checklist-verdict"),
             ("H4", "checklist-prose", "baseline"),
             ("-", "checklist-verdict", "checklist-prose"))


def _naive_sum(xs: Iterable[float]) -> float:
    """Left-to-right float addition (``sum()`` before Python 3.12)."""
    s = 0
    for x in xs:
        s = s + x
    return s


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float | None, float | None, float | None]:
    if n == 0:
        return (None, None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - h), min(1.0, c + h))


def _rate(k: int, n: int) -> dict[str, Any]:
    p, lo, hi = wilson(k, n)
    return {"k": k, "n": n, "p": p, "lo": lo, "hi": hi}


def trial_cluster_bootstrap_diff(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]],
                                 B: int = BOOT_B, seed: int = BOOT_SEED,
                                 alpha: float = 0.05) -> dict[str, Any]:
    """Two-sample, trial-clustered percentile bootstrap of pooled item-pass(a) - (b).

    Each arm's TRIALS are resampled with replacement, independently, keeping
    each trial's items together (items within a trial are correlated: one
    workbook, one session), and the pooled item-pass difference is recomputed.
    ``a``/``b`` are lists of per-trial item vectors (1/0; None items dropped).
    Local ``random.Random(seed)``; no hash-order dependence."""
    A = [[float(x) for x in t if x is not None] for t in a]
    Bv = [[float(x) for x in t if x is not None] for t in b]
    A = [t for t in A if t]
    Bv = [t for t in Bv if t]
    if not A or not Bv:
        return {"point": None, "lo": None, "hi": None, "B": B, "seed": seed, "n_a": len(A), "n_b": len(Bv)}

    def pooled(ts: Sequence[Sequence[float]]) -> float:
        s = n = 0.0
        for t in ts:
            s += _naive_sum(t)
            n += len(t)
        return s / n

    point = pooled(A) - pooled(Bv)
    r = random.Random(seed)
    diffs = []
    na, nb = len(A), len(Bv)
    for _ in range(B):
        ra = [A[r.randrange(na)] for _ in range(na)]
        rb = [Bv[r.randrange(nb)] for _ in range(nb)]
        diffs.append(pooled(ra) - pooled(rb))
    diffs.sort()
    lo = diffs[int(math.floor((alpha / 2) * B))]
    hi = diffs[min(B - 1, int(math.ceil((1 - alpha / 2) * B)) - 1)]
    return {"point": point, "lo": lo, "hi": hi, "B": B, "seed": seed, "n_a": na, "n_b": nb}


def _read(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _designed(run_dir: Path, eval_def) -> list | None:
    m = core_report.load_manifest(run_dir) or {}
    reg = m.get("regime")
    if not reg:
        return None
    try:
        return eval_def.cells(RegimeConfig.from_json(reg))
    except Exception:  # noqa: BLE001
        return None


def run_report(run_dir: Path, eval_def) -> dict[str, Any]:
    run_dir = Path(run_dir)
    manifest = core_report.load_manifest(run_dir) or {}
    ruleset = (manifest.get("regime") or {}).get("ruleset")
    verdicts, load_errors = core_report.load_verdicts(run_dir)
    if ruleset:
        verdicts = [v for v in verdicts if v["ruleset"] == ruleset]
    designed = _designed(run_dir, eval_def)
    cells = core_report.select_cells(verdicts, designed, core_report.load_invalidated(run_dir))
    judge_summary = _read(run_dir / "eval4_judge_summary.json")
    universe: list[str] = []
    per_arm: dict[str, dict[str, Any]] = {}
    vectors_itt: dict[str, list[list[float | None]]] = {a: [] for a in CONDITION_ORDER}
    vectors_designed: dict[str, list[list[float | None]]] = {a: [] for a in CONDITION_ORDER}
    unjudged = []
    for cid in sorted(cells):
        rec = cells[cid]
        arm = rec.arm
        row = per_arm.setdefault(arm, {
            "designed_or_seen": 0, "scored": 0, "excluded": 0, "missing_or_unscored": 0,
            "judged": 0, "no_workbook": 0, "checker_all12": 0, "checker_items": [0, 0],
            "coarse_overclaim_vs_judge": [0, 0], "coarse_overclaim_vs_checker": [0, 0],
            "per_item_overclaim_vs_judge_excl_A07": [0, 0], "qa_check_calls": [], "turns": [], "cost_usd": [],
            "recalc_status": {}, "claims_parse_status": {}})
        row["designed_or_seen"] += 1
        if rec.status == "excluded":
            row["excluded"] += 1
            continue
        if rec.status != "scored":
            row["missing_or_unscored"] += 1
            vectors_designed[arm].append(None)  # filled with zeros once the universe is known
            continue
        v = rec.scored
        row["scored"] += 1
        d = v.get("details") or {}
        ck = d.get("checker") or {}
        if v.get("success") is True:
            row["checker_all12"] += 1
        if ck:
            row["checker_items"][0] += ck.get("passed", 0)
            row["checker_items"][1] += ck.get("total", 0)
        if v.get("claimed_success") is not None and v.get("success") is not None:
            row["coarse_overclaim_vs_checker"][1] += 1
            row["coarse_overclaim_vs_checker"][0] += 1 if v.get("overclaim") else 0
        rs = ((d.get("recalc") or {}).get("recalc_status")) or "no_workbook"
        row["recalc_status"][rs] = row["recalc_status"].get(rs, 0) + 1
        cps = v.get("claims_parse_status") or "none"
        row["claims_parse_status"][cps] = row["claims_parse_status"].get(cps, 0) + 1
        pr = v.get("process") or {}
        for k_src, k_dst in (("oracle_calls", "qa_check_calls"), ("turns", "turns"), ("cost_usd", "cost_usd")):
            if isinstance(pr.get(k_src), (int, float)):
                row[k_dst].append(pr[k_src])
        panel = _read(run_dir / "cells" / cid / f"attempt-{v['attempt']}" / "judge" / "panel.json")
        if not isinstance(panel, dict):
            unjudged.append(cid)  # listed, excluded from both judged denominators
            continue
        row["judged"] += 1
        items = panel.get("items") or {}
        universe = sorted(set(universe) | set(items))
        vec = [items.get(k) for k in sorted(items)]
        vec_f = [None if x is None else (1.0 if x else 0.0) for x in vec]
        vectors_itt[arm].append(vec_f)
        vectors_designed[arm].append(vec_f)
        if panel.get("no_workbook"):
            row["no_workbook"] += 1
        co = panel.get("coarse_overclaim_vs_judge")
        row["coarse_overclaim_vs_judge"][1] += 1
        row["coarse_overclaim_vs_judge"][0] += 1 if co else 0
        pi = panel.get("per_item_overclaim_vs_judge_excl_A07") or {}
        row["per_item_overclaim_vs_judge_excl_A07"][0] += pi.get("overclaimed", 0)
        row["per_item_overclaim_vs_judge_excl_A07"][1] += pi.get("claimed_true", 0)

    n_items = len(universe) or 12

    def fill(vs: list) -> list[list[float | None]]:
        return [[0.0] * n_items if x is None else x for x in vs]

    judged: dict[str, Any] = {}
    for arm in CONDITION_ORDER:
        if arm not in per_arm:
            continue
        itt = vectors_itt[arm]
        k = sum(1 for t in itt for x in t if x == 1.0)
        n = sum(1 for t in itt for x in t if x is not None)
        dz = fill(vectors_designed[arm])
        kd = sum(1 for t in dz for x in t if x == 1.0)
        nd = sum(1 for t in dz for x in t if x is not None)
        judged[arm] = {"scored_itt": _rate(k, n), "designed_missing_fail": _rate(kd, nd),
                       "judge_error_items": sum(1 for t in itt for x in t if x is None)}
    contrasts = {}
    for label, a, b in CONTRASTS:
        if a in judged and b in judged and judged[a]["scored_itt"]["n"] and judged[b]["scored_itt"]["n"]:
            bs = trial_cluster_bootstrap_diff(vectors_itt[a], vectors_itt[b])
            contrasts[f"{a} - {b}"] = {"hypothesis": label, **bs}
    for row in per_arm.values():
        for k in ("qa_check_calls", "turns", "cost_usd"):
            xs = row[k]
            row[k] = {"mean": (sum(xs) / len(xs)) if xs else None, "n": len(xs)}
    out = {
        "schema": "dbwm.eval4.run-report/1",
        "ruleset": ruleset,
        "series_note": ("New runs are a new series (fixed sandboxes, sealed harness-side qa_check, isolated judge); "
                        "the instrument changes are listed in change-ledger/eval4.md."),
        "isolation": (manifest.get("regime") or {}).get("isolation"),
        "judge": ({k: judge_summary.get(k) for k in ("model", "passes", "style", "judge_adapter", "min_valid")}
                  if isinstance(judge_summary, dict) else None),
        "primary_judged_item_pass": judged if judge_summary else "not judged yet: run `dbwm eval4-judge --run DIR`",
        "contrasts": contrasts,
        "unjudged_scored_cells": unjudged,
        "per_arm": per_arm,
        "load_errors": load_errors,
    }
    out["markdown"] = render_markdown(out)
    return out


def _fmt(r: dict) -> str:
    if not r or not r.get("n"):
        return "—"
    return f"{r['k']}/{r['n']} = {100 * r['p']:.0f}% [{100 * r['lo']:.0f},{100 * r['hi']:.0f}]"


def _frac(x: list) -> str:
    return f"{x[0]}/{x[1]}" if x and x[1] else "—"


def render_markdown(rep: dict[str, Any]) -> str:
    L = ["## Eval 4 (AA-Briefcase-Lite w1_t2)", "", rep["series_note"], ""]
    pj = rep["primary_judged_item_pass"]
    if isinstance(pj, str):
        L += [f"Primary (judged item-pass): {pj}", ""]
    else:
        j = rep.get("judge") or {}
        L += [f"Primary: judged item-pass, {j.get('passes')}-pass panel majority (judge {j.get('model')}, "
              f"adapter {j.get('judge_adapter')}, prompt style {j.get('style')}).", "",
              "| arm | scored cells (ITT) | designed cells, missing = fail | judge_error items |",
              "|---|---|---|---|"]
        for arm in CONDITION_ORDER:
            if arm in pj:
                r = pj[arm]
                L.append(f"| {arm} | {_fmt(r['scored_itt'])} | {_fmt(r['designed_missing_fail'])} "
                         f"| {r['judge_error_items']} |")
        L.append("")
        if rep["contrasts"]:
            L += ["| contrast | diff (points) | 95% trial-clustered bootstrap |", "|---|---|---|"]
            for k, c in rep["contrasts"].items():
                if c.get("point") is not None:
                    L.append(f"| {c['hypothesis']} {k} | {100 * c['point']:+.1f} "
                             f"| [{100 * c['lo']:+.1f}, {100 * c['hi']:+.1f}] |")
            L.append("")
    L += ["| arm | scored | excluded | missing/unscored | checker all-12 | checker items | "
          "coarse overclaim vs judge | per-item overclaim vs judge (excl. A-07) | "
          "overclaim vs checker (self-consistency) | qa_check calls | turns | cost USD |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for arm in CONDITION_ORDER:
        r = rep["per_arm"].get(arm)
        if not r:
            continue

        def mean(x):
            return "—" if x["mean"] is None else f"{x['mean']:.1f}"
        L.append(f"| {arm} | {r['scored']} | {r['excluded']} | {r['missing_or_unscored']} "
                 f"| {r['checker_all12']}/{r['scored']} | {_frac(r['checker_items'])} "
                 f"| {_frac(r['coarse_overclaim_vs_judge'])} | {_frac(r['per_item_overclaim_vs_judge_excl_A07'])} "
                 f"| {_frac(r['coarse_overclaim_vs_checker'])} | {mean(r['qa_check_calls'])} "
                 f"| {mean(r['turns'])} | {mean(r['cost_usd'])} |")
    L.append("")
    return "\n".join(L) + "\n"
