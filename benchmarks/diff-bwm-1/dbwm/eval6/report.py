"""Eval-6 D1 run report (EVAL.report): the generic core summary plus the eval's own analysis.

Added on top of dbwm.core.report (per-arm success under both denominators,
Wilson CIs, overclaim, exclusions, conformance):

* exclusions by reason, counted over the DESIGNED cells before anything is
  filtered (the paper's harness counted oracle_leaks after filtering; ledger
  );
* per-arm realized-profit metrics (true profit, gap to CRN M*, no-plan,
  marketing bounded at the history's maximum, budget projection, out-of-band
  prices, projection gap = projected / true - 1; the v1 brief pins the profit
  accounting);
* the headline: aided-minus-unaided realized-profit uplift per world with a
  world-clustered bootstrap, under BOTH no-plan policies (reference = the
  paper's, exclude), always shown side by side;
* moat buckets from the normative certify scan, a description only.

Writes eval6_report.json and eval6_report.md into the run directory.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from dbwm.core import report as core_report

from . import _paths
from . import contrast as C

ARMS = ("unaided", "aided")


def _stats(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0, "mean": None, "median": None}
    return {"n": len(xs), "mean": statistics.mean(xs), "median": statistics.median(xs)}


def trials_from_cells(cells) -> tuple[list[dict], Counter]:
    """(trials for contrast.py, exclusion counts by reason over every designed cell)."""
    trials, reasons = [], Counter()
    for rec in cells.values():
        base = {"world": rec.instance, "arm": rec.arm, "rep": rec.rep, "cell_id": rec.cell_id}
        if rec.status == "scored" and rec.scored is not None:
            d = rec.scored.get("details") or {}
            trials.append({**base, "true_profit": d.get("true_profit"), "no_plan": bool(d.get("no_plan")),
                           "details": d})
            if d.get("no_plan"):
                reasons["no_plan (scored; policy-dependent)"] += 1
        else:
            for r in rec.exclusion or [rec.status]:
                reasons[r] += 1
            trials.append({**base, "true_profit": None, "no_plan": False, "excluded": True,
                           "exclusion_reason": ";".join(rec.exclusion) or rec.status})
    return trials, reasons


def arm_metrics(trials: list[dict]) -> dict:
    out = {}
    for arm in ARMS:
        ts = [t for t in trials if t["arm"] == arm and not t.get("excluded")]
        ds = [t["details"] for t in ts]
        out[arm] = {
            "n_scored": len(ts),
            "true_profit": _stats([d.get("true_profit") for d in ds]),
            "gap_to_mstar_pct": _stats([d.get("gap_to_mstar_pct") for d in ds if not d.get("no_plan")]),
            "projection_gap_pct": _stats([d.get("projection_gap_pct") for d in ds if not d.get("no_plan")]),
            "no_plan": sum(1 for d in ds if d.get("no_plan")),
            "marketing_bounded": sum(1 for d in ds if d.get("marketing_bounded")),
            "budget_projected": sum(1 for d in ds if d.get("budget_projected")),
            "raw_price_out_of_band": sum(1 for d in ds if d.get("raw_price_out_of_band")),
            "plan_fallback": sum(1 for d in ds if d.get("plan_fallback")),
            "claims_not_ok": sum(1 for d in ds if d.get("claims_parse_status") != "ok"),
            "reached_tau": sum(1 for d in ds if d.get("reached_tau")),
        }
    return out


def report(run_dir) -> dict[str, Any]:
    run_dir = Path(run_dir)
    summary = core_report.build_report(run_dir)
    verdicts, _errors = core_report.load_verdicts(run_dir)
    manifest = core_report.load_manifest(run_dir) or {}
    designed = manifest.get("cells") if isinstance(manifest.get("cells"), list) else None
    invalidated = core_report.load_invalidated(run_dir)
    moats = C.moats_from_certify_scan(_paths.certify_scan()) if _paths.certify_scan().is_file() else {}
    sections = []
    for rs in sorted({v["ruleset"] for v in verdicts}):
        vs = [v for v in verdicts if v["ruleset"] == rs]
        cells = core_report.select_cells(vs, designed, invalidated)
        trials, reasons = trials_from_cells(cells)
        sec: dict[str, Any] = {"ruleset": rs, "n_designed_cells": len(cells),
                               "exclusions_by_reason": dict(sorted(reasons.items())),
                               "arms": arm_metrics(trials)}
        if all(any(t["arm"] == a and not t.get("excluded") for t in trials) for a in ARMS):
            con = C.contrast_both_policies([{k: v for k, v in t.items() if k != "details"} for t in trials])
            sec["uplift"] = con
            if moats:
                sec["moat_buckets"] = C.moat_buckets(con["reference"]["per_world"], moats)
        sections.append(sec)
    out = {"schema": "dbwm.eval6.report/1", "core": summary, "eval6": sections,
           "notes": ["success = true profit reaches the step-0 target tau (derived; the paper reported none). "
                     "The eval-6 headline is the realized-profit uplift.",
                     "projection gap = projected / true profit - 1. Under the paper brief it is a profit-definition "
                     "gap (margins and fixed costs undisclosed), not calibration; the v1 brief pins both.",
                     "v1 caps each segment's marketing at the history's maximum before scoring (marketing bounded); "
                     "gap to M* and tau stay against the unbounded grid optimum, and where tau lies above the bounded "
                     "optimum (w7) v1 reports success as undefined.",
                     "Both no-plan policies are shown: 'reference' scores a trial that committed nothing "
                     "usable as the do-nothing plan (the paper's undisclosed choice); 'exclude' drops it "
                     "and any world left without both arms."]}
    (run_dir / "eval6_report.json").write_text(json.dumps(out, indent=2, default=str) + "\n")
    (run_dir / "eval6_report.md").write_text(render_markdown(out))
    return out


def _f(x, d=1, pct=False):
    if x is None:
        return "-"
    return f"{x:+.{d}f}%" if pct else f"{x:,.{d}f}"


def render_markdown(rep: dict[str, Any], include_core: bool = True) -> str:
    """eval6_report.md (standalone: with the generic summary) or, with ``include_core=False``,
    the eval section ``dbwm report`` appends to report.md after its own generic tables."""
    L = ["# Eval 6 (epistemic access) D1 report", ""]
    if include_core and rep.get("core") is not None:
        L += [core_report.render_markdown(rep["core"], "Generic summary"), ""]
    for sec in rep["eval6"]:
        L += [f"## Ruleset {sec['ruleset']}", "",
              f"Designed cells: {sec['n_designed_cells']}. Exclusions by reason (counted before filtering): "
              + (", ".join(f"{k}: {v}" for k, v in sec["exclusions_by_reason"].items()) or "none"), "",
              "| arm | scored | mean true profit | mean gap to M* | mean projection gap | no-plan | marketing bounded "
              "| budget projected | price out of band | reached tau |", "|---|---|---|---|---|---|---|---|---|---|"]
        for arm, m in sec["arms"].items():
            L.append(f"| {arm} | {m['n_scored']} | {_f(m['true_profit']['mean'], 0)} | "
                     f"{_f(m['gap_to_mstar_pct']['mean'], 1, True)} | {_f(m['projection_gap_pct']['mean'], 1, True)} | "
                     f"{m['no_plan']} | {m['marketing_bounded']} | {m['budget_projected']} | "
                     f"{m['raw_price_out_of_band']} | {m['reached_tau']} |")
        L.append("")
        if "uplift" in sec:
            L += ["| no-plan policy | worlds | mean uplift | median | 95% CI (world-clustered) | dropped worlds |",
                  "|---|---|---|---|---|---|"]
            for pol, c in sec["uplift"].items():
                ci = c.get("ci95") or [None, None]
                L.append(f"| {pol} | {c.get('n_worlds', 0)} | {_f(c.get('uplift_mean'), 1, True)} | "
                         f"{_f(c.get('uplift_median'), 1, True)} | [{_f(ci[0], 1, True)}, {_f(ci[1], 1, True)}] | "
                         f"{', '.join(c.get('dropped_worlds') or []) or '-'} |")
            L.append("")
        if "moat_buckets" in sec:
            mb = sec["moat_buckets"]
            L.append("Moat buckets (gamma=0-belief decision gap from certify_scan.json; a description only): "
                     + "; ".join(f"{k} n={v['n']} mean uplift {_f(v['mean_uplift'], 1, True)}"
                                 for k, v in mb.items() if k != "thresholds"))
            L.append("")
    L += [f"- {n}" for n in rep["notes"]]
    return "\n".join(L) + "\n"
