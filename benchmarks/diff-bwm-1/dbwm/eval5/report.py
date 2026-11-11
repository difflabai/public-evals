"""Eval-5 report for a new run (dbwm verdicts).

``report(run_dir)`` (EVAL.report) builds on dbwm.core.report (per-arm success
under both denominators, Wilson CIs, overclaim, exclusions, conformance) and
adds what eval 5 pre-registered and what the audit asked for:

* pooled oracle item-pass per arm (the pre-registered PRIMARY measure);
* the three scenario-clustered contrasts of the paper's analysis (any
  checklist - baseline, executable - prose, executable - verdict), on
  all-items success, under both denominators, seed 20260912;
* calibration that is not entailed by accuracy: P(believes success | >= 1
  failed item) per arm, with counts (the paper's coarse overclaim is
  mechanically 0 whenever every item passes; ledger E5-D13);
* key figures inside the anchor bands (unit-normalized in v1).
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from dbwm.core import report as core_report
from dbwm.core import stats as core_stats

CONTRAST_SEED = 20260912
CONTRASTS = (
    ("any checklist - baseline", ["checklist-prose", "checklist-verdict", "checklist-executable"], ["baseline"]),
    ("executability (executable - prose)", ["checklist-executable"], ["checklist-prose"]),
    ("runnable check (executable - verdict)", ["checklist-executable"], ["checklist-verdict"]),
)
ARM_ORDER = ("baseline", "checklist-prose", "checklist-verdict", "checklist-executable")


def _pct(x):
    return "—" if x is None else f"{x:.0%}"


def _rate(k, n):
    p, lo, hi = core_stats.wilson(k, n)
    return {"k": k, "n": n, "rate": p, "ci95": [lo, hi]}


def _fmt(r):
    if not r["n"]:
        return "—"
    return f"{r['k']}/{r['n']} = {_pct(r['rate'])} [{_pct(r['ci95'][0])},{_pct(r['ci95'][1])}]"


def eval_metrics(scored_verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-arm eval-5 metrics over scored, non-excluded verdicts (one ruleset)."""
    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for v in scored_verdicts:
        by_arm[v["cell"]["arm"]].append(v)
    out = {}
    for arm in sorted(by_arm, key=lambda a: (ARM_ORDER.index(a) if a in ARM_ORDER else 99, a)):
        vs = by_arm[arm]
        kp = sum(((v.get("details") or {}).get("item_pass") or [0, 0])[0] for v in vs)
        npp = sum(((v.get("details") or {}).get("item_pass") or [0, 0])[1] for v in vs)
        failed = [v for v in vs if v.get("success") is False]
        believes_failed = [v for v in failed if v.get("claimed_success") is True]
        figs = [x for v in vs for x in ((v.get("details") or {}).get("key_figures_in_anchor_band") or {}).values()]
        per_check = defaultdict(int)
        for v in vs:
            for cid in (v.get("details") or {}).get("checker_failed_ids") or []:
                per_check[cid] += 1
        out[arm] = {
            "n": len(vs),
            "item_pass": _rate(kp, npp),
            "all_items": _rate(sum(1 for v in vs if v.get("success") is True), len(vs)),
            "p_believes_given_fail": _rate(len(believes_failed), len(failed)),
            "overclaim_all": _rate(sum(1 for v in vs if v.get("overclaim") is True), len(vs)),
            "key_figures_in_band": _rate(sum(1 for x in figs if x is True), sum(1 for x in figs if x is not None)),
            "per_check_fail": dict(sorted(per_check.items())),
        }
    return out


def report(run_dir) -> dict[str, Any]:
    """EVAL.report: core summary + eval-5 metrics and contrasts; writes eval5_report.{json,md}."""
    run_dir = Path(run_dir)
    summary = core_report.build_report(run_dir)
    verdicts, _errors = core_report.load_verdicts(run_dir)
    manifest = core_report.load_manifest(run_dir) or {}
    designed = manifest.get("cells") if isinstance(manifest.get("cells"), list) else None
    invalidated = core_report.load_invalidated(run_dir)
    sections = []
    for rs in sorted({v["ruleset"] for v in verdicts}):
        vs = [v for v in verdicts if v["ruleset"] == rs]
        cells = core_report.select_cells(vs, designed, invalidated)
        scored = [r.scored for r in cells.values() if r.status == "scored" and r.scored is not None]
        contrasts = []
        for label, a, b in CONTRASTS:
            for den in ("designed", "scored"):
                c = core_report.arm_contrast(vs, a, b, ruleset=rs, designed_cells=designed, denominator=den,
                                             B=2000, seed=CONTRAST_SEED, invalidated=invalidated)
                c["label"] = label
                contrasts.append(c)
        sections.append({"ruleset": rs, "arms": eval_metrics(scored), "contrasts": contrasts})
    out = {"schema": "dbwm.eval5.report/1", "core": summary, "eval5": sections}
    (run_dir / "eval5_report.json").write_text(json.dumps(out, indent=2, default=str) + "\n")
    (run_dir / "eval5_report.md").write_text(render_markdown(out))
    return out


def render_markdown(rep: dict[str, Any]) -> str:
    lines = ["# Eval 5 (consulting oracle) report", ""]
    core = rep.get("core") or {}
    conf = core.get("conformance") or {}
    lines.append(f"Run: {core.get('run_id')}  regime_hash: {core.get('regime_hash')}  conformance: {conf}")
    lines.append("")
    for sec in rep["eval5"]:
        lines.append(f"## Ruleset {sec['ruleset']}")
        lines.append("")
        lines.append("| arm | n | item-pass (primary) | all-items | P(believes \\| >=1 fail) | overclaim (all) | key figs in band |")
        lines.append("|---|---|---|---|---|---|---|")
        for arm, m in sec["arms"].items():
            lines.append(f"| {arm} | {m['n']} | {_fmt(m['item_pass'])} | {_fmt(m['all_items'])} | "
                         f"{_fmt(m['p_believes_given_fail'])} | {_fmt(m['overclaim_all'])} | {_fmt(m['key_figures_in_band'])} |")
        lines.append("")
        lines.append(f"Scenario-clustered contrasts on all-items success (B=2000, seed {CONTRAST_SEED}):")
        lines.append("")
        for c in sec["contrasts"]:
            d, (lo, hi) = c["diff"], c["ci95"]
            lines.append(f"- {c['label']} [{c['denominator']}]: "
                         + ("n/a" if d is None else f"{d:+.0%} 95% CI [{_pct(lo)}, {_pct(hi)}]"
                            f" (n {c['n_a']} vs {c['n_b']}, {c['clusters']} scenarios)"))
        lines.append("")
    lines.append("Checklist-vs-baseline is an answer-disclosure contrast: CHECKLIST.md states an in-band value for "
                 "every banded check (docs/evals/eval5.md).")
    return "\n".join(lines) + "\n"

