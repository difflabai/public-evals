"""Eval-2 run report: the core per-arm tables plus the paper's three clustered contrasts.

``dbwm report --run DIR`` calls :func:`build_eval2_report` through
``EVAL.report``. It adds to :func:`dbwm.core.report.build_report`:

* the paper's contrasts (confirm_report.py:9-17), each as a world-clustered
  bootstrap (resample worlds, then trials per arm within world; B=2000,
  seed 20260911, :func:`dbwm.core.stats.cluster_bootstrap_diff` -- sorted and
  hash-independent, NOT the paper's legacy routine) under both denominators:
  ``designed`` (metric B: every designed cell counts, an attempt that ended
  without a scorable plan is a failure) and ``scored`` (scored attempts only);
      ceiling       grounded-iterate - ungrounded
      any-grounded  the five grounded arms pooled - ungrounded
      mechanism     raw-figures - (grounded-boolean + prose-feedback)
* an achievement table by world x arm, and overclaim counts per arm: per trial
  (scored cells with a readable claim) and per failure (failed committed plans
  that filed a readable ``claims.json``, v7 Appendix A entry 17).
* beside each bootstrap, Fisher's exact test (two-sided) and the Newcombe
  hybrid-score 95% interval on the same cells: with three to five worlds the
  bootstrap degenerates when an arm sits at 0/k (v7 Appendix C).

Differences from the paper's aggregation, by design: metric B (designed cells)
is primary; metric A (the paper's default, attrition excluded) is the
``scored`` denominator only in the sense that unscored attempts are dropped --
a scored attempt without a plan is still a failure here, as it was in metric B.
The mechanism contrast is no longer confounded by the boolean channel's leaked
figures (N01: v1 serves the whitelist) or by self-computation (no arm has
Bash), so the paper's "mechanism among non-self-computing trials" row is gone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .arms import FIGURES_NO_VERDICT, GROUNDED_CANONICAL, VERDICT_NO_FIGURES

SEED = 20260911
B = 2000
CONTRASTS = (
    ("ceiling (grounded-iterate - ungrounded)", ("grounded-iterate",), ("ungrounded",)),
    ("any-grounded - ungrounded", GROUNDED_CANONICAL, ("ungrounded",)),
    ("mechanism (figures - verdict)", FIGURES_NO_VERDICT, VERDICT_NO_FIGURES),
)


def _by_world(cells) -> dict[str, dict[str, str]]:
    table: dict[str, dict[str, list[int]]] = {}
    for rec in cells.values():
        k = table.setdefault(rec.instance, {}).setdefault(rec.arm, [0, 0])
        if rec.status == "excluded":
            continue
        k[1] += 1
        if rec.status == "scored" and rec.scored is not None and rec.scored.get("success"):
            k[0] += 1
    return {w: {a: f"{k}/{n}" for a, (k, n) in sorted(arms.items())} for w, arms in sorted(table.items())}


def _overclaim(cells) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rec in cells.values():
        if rec.status != "scored" or rec.scored is None:
            continue
        o = out.setdefault(rec.arm, {"overclaim_all": [0, 0], "overclaim_of_fails": [0, 0]})
        v = rec.scored
        if v.get("overclaim") is not None:  # None = no readable claims.json (v7 entry 17: never an honest failure)
            o["overclaim_all"][1] += 1
            o["overclaim_all"][0] += int(bool(v["overclaim"]))
            if not v.get("success") and v.get("deliverable_present") is not False:  # failed committed plans
                o["overclaim_of_fails"][1] += 1
                o["overclaim_of_fails"][0] += int(bool(v["overclaim"]))
    return {a: {k: f"{x[0]}/{x[1]}" for k, x in d.items()} for a, d in sorted(out.items())}


def _exact(cells, a, b, denominator: str) -> dict[str, Any]:
    """Fisher's exact test and the Newcombe interval on the cells the bootstrap resamples (arm_contrast's rules)."""
    from dbwm.core import stats

    def count(group):
        k = n = 0
        for rec in cells.values():
            if rec.arm not in group or rec.status == "excluded":
                continue
            if rec.status == "scored" and rec.scored is not None:
                if rec.scored["success"] is None and denominator == "scored":
                    continue
                n += 1
                k += bool(rec.scored["success"])
            elif denominator == "designed":
                n += 1
        return k, n

    (ka, na), (kb, nb) = count(tuple(a)), count(tuple(b))
    if not na or not nb:
        return {"k_a": ka, "k_b": kb, "fisher_two_sided": None, "newcombe95": None}
    _d, lo, hi = stats.newcombe_ci(ka, na, kb, nb)
    return {"k_a": ka, "k_b": kb, "fisher_two_sided": stats.fisher_exact_two_sided(ka, na, kb, nb),
            "newcombe95": [lo, hi]}


def build_eval2_report(run_dir: Path) -> dict[str, Any]:
    from dbwm.core import report as core_report

    run_dir = Path(run_dir)
    summary = core_report.build_report(run_dir)
    verdicts, _errors = core_report.load_verdicts(run_dir)
    verdicts = [v for v in verdicts if v.get("eval") == "eval2"]
    manifest = core_report.load_manifest(run_dir)
    designed = manifest.get("cells") if manifest and isinstance(manifest.get("cells"), list) else None
    invalidated = core_report.load_invalidated(run_dir)
    sections = []
    for rs in sorted({v["ruleset"] for v in verdicts}):
        cells = core_report.select_cells([v for v in verdicts if v["ruleset"] == rs], designed, invalidated)
        contrasts = []
        for label, a, b in CONTRASTS:
            for denom in ("designed", "scored"):
                c = core_report.arm_contrast(verdicts, list(a), list(b), ruleset=rs,
                                             designed_cells=designed, denominator=denom,
                                             B=B, seed=SEED, invalidated=invalidated)
                c["label"] = label
                c.update(_exact(cells, a, b, denom))
                contrasts.append(c)
        sections.append({
            "ruleset": rs,
            "achievement_by_world": _by_world(cells),
            "overclaim_by_arm": _overclaim(cells),
            "contrasts": contrasts,
        })
    return {
        "schema": "dbwm-eval2-report/1",
        "core": summary,
        "eval2": {
            "contrast_method": (f"world-clustered bootstrap, B={B}, seed={SEED}, dbwm.core.stats "
                                "(hash-independent), beside Fisher's exact test and the Newcombe interval; "
                                "'designed' = metric B, 'scored' = scored attempts only"),
            "sections": sections,
        },
    }


def render_markdown(rep: dict[str, Any]) -> str:
    lines = ["# Eval 2 (coupled portfolio) -- run report", ""]
    for sec in rep["eval2"]["sections"]:
        lines.append(f"## Ruleset `{sec['ruleset']}`")
        lines.append("")
        lines.append("| contrast | denominator | a | b | diff | Fisher p | Newcombe 95% | world-clustered 95% |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for c in sec["contrasts"]:
            d = c["diff"]
            lo, hi = c["ci95"]
            fmt = (lambda x: "n/a" if x is None else f"{x * 100:+.0f}")
            nw = c.get("newcombe95")
            p = c.get("fisher_two_sided")
            lines.append(f"| {c['label']} | {c['denominator']} | {c['k_a']}/{c['n_a']} | {c['k_b']}/{c['n_b']} | "
                         f"{fmt(d)} | {'n/a' if p is None else f'{p:.2g}'} | "
                         f"{'n/a' if nw is None else f'[{fmt(nw[0])}, {fmt(nw[1])}]'} | [{fmt(lo)}, {fmt(hi)}] |")
        lines.append("")
        lines.append("Achievement by world x arm: " + ", ".join(
            f"{w}: " + "; ".join(f"{a} {r}" for a, r in arms.items())
            for w, arms in sec["achievement_by_world"].items()))
        lines.append("")
    return "\n".join(lines) + "\n"
