"""Eval-3 run report: core per-arm tables plus the generative-branch analysis.

``dbwm report --run DIR`` calls :func:`build_eval3_report` through
``EVAL.report``. It adds to :func:`dbwm.core.report.build_report`, per ruleset:

* the pre-registered contrasts (eval3-generative-vs-grounded.md sections 1, 3
  and 7), registered hypotheses first, each under both denominators --
  ``designed`` (every designed cell; a cell without a scorable plan is a
  failure) and ``scored`` (scored attempts only):

      H1, registered      gen-figures - ungrounded; gen-both - ungrounded
      H2, registered      grounded-iterate - gen-both
      generative effect   gen-figures + gen-both + gen-code (pooled) - ungrounded
      grounding premium   grounded-iterate - the three generative arms pooled (not the registered H2)
      testability effect  gen-both - gen-figures
      tool-form effect    gen-code - gen-both

  Exact tests come first (v7 Appendix C): Fisher's exact test (two-sided) and
  the Newcombe hybrid-score interval on the pooled counts; beside them the
  pre-registered world-clustered bootstrap (resample worlds, then trials per
  arm within world; B=2000, seed 20260701, :func:`dbwm.core.report.arm_contrast`),
  which with three worlds has only ten distinct cluster resamples.
  A contrast whose arms are not in the run is reported as such, never as 0.
* overclaim per arm and for the pooled generative arms over BOTH bases, with
  counts: of all scored cells with a readable claim, and of the failures among
  them. Both bases are always printed, so no comparison mixes them.
* self-model fidelity per generative arm: how often a model was declared
  (``model.json``), the median of the paper's ``self_model_fidelity_pct_error``
  (the claimed revenue's error) and, under v1, of ``model_plan_fidelity_pct_error``
  (the agent's own model evaluated on its committed plan vs the true world).
* oracle-tool calls per arm (from ``verdict.process.oracle_calls``).

``markdown`` in the returned dict is the rendered eval-3 section.
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

from .arms import CONTRASTS, GEN_ARMS

SEED = 20260701  # fixed bootstrap seed, documented in docs/evals/eval3.md
B = 2000
SCHEMA = "dbwm-eval3-report/1"


def _num(x: Any) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _median(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def _overclaim(recs) -> dict[str, Any]:
    """Overclaim per trial (scored cells with a readable claim) and per failure. The per-failure basis is v7's
    (entry 17): failed COMMITTED plans whose agent filed a readable ``claims.json`` (``overclaim`` is None when the
    claim is missing or malformed, so a missing file never counts as an honest failure)."""
    k_all = n_all = k_fail = n_fail = 0
    for v in recs:
        if v.get("overclaim") is None:
            continue
        n_all += 1
        k_all += bool(v["overclaim"])
        if not v.get("success") and v.get("deliverable_present") is not False:
            n_fail += 1
            k_fail += bool(v["overclaim"])
    return {"overclaim_of_all": {"k": k_all, "n": n_all}, "overclaim_of_fails": {"k": k_fail, "n": n_fail}}


def _fidelity(recs) -> dict[str, Any]:
    d = [v.get("details") or {} for v in recs]
    paper = [x for x in (_num(x.get("self_model_fidelity_pct_error")) for x in d) if x is not None]
    plan = [x for x in (_num(x.get("model_plan_fidelity_pct_error")) for x in d) if x is not None]
    return {
        "declared_model": {"k": sum(1 for x in d if x.get("declared_model")), "n": len(d)},
        "claimed_revenue_error_pct": {"n": len(paper), "median": _median(paper)},
        "model_on_committed_plan_error_pct": {"n": len(plan), "median": _median(plan)},
    }


def _successes(cells, group: tuple[str, ...], denominator: str) -> tuple[int, int]:
    """(successes, n) of an arm group under ``denominator``, with :func:`dbwm.core.report.arm_contrast`'s rules."""
    k = n = 0
    for rec in cells.values():
        if rec.arm not in group or rec.status == "excluded":
            continue
        if rec.status == "scored" and rec.scored is not None:
            if rec.scored["success"] is None:
                if denominator == "scored":
                    continue
                n += 1
                continue
            n += 1
            k += bool(rec.scored["success"])
        elif denominator == "designed":
            n += 1
    return k, n


def _exact(cells, a: tuple[str, ...], b: tuple[str, ...], denominator: str) -> dict[str, Any]:
    from dbwm.core import stats

    ka, na = _successes(cells, a, denominator)
    kb, nb = _successes(cells, b, denominator)
    if not na or not nb:
        return {"k_a": ka, "k_b": kb, "fisher_two_sided": None, "newcombe95": None}
    _d, lo, hi = stats.newcombe_ci(ka, na, kb, nb)
    return {"k_a": ka, "k_b": kb, "fisher_two_sided": stats.fisher_exact_two_sided(ka, na, kb, nb),
            "newcombe95": [lo, hi]}


def build_eval3_report(run_dir: Path) -> dict[str, Any]:
    from dbwm.core import report as core_report

    run_dir = Path(run_dir)
    summary = core_report.build_report(run_dir)
    verdicts, _errors = core_report.load_verdicts(run_dir)
    verdicts = [v for v in verdicts if v.get("eval") == "eval3"]
    manifest = core_report.load_manifest(run_dir)
    designed = manifest.get("cells") if manifest and isinstance(manifest.get("cells"), list) else None
    invalidated = core_report.load_invalidated(run_dir)
    sections = []
    for rs in sorted({v["ruleset"] for v in verdicts}):
        cells = core_report.select_cells([v for v in verdicts if v["ruleset"] == rs], designed, invalidated)
        scored = {}
        for rec in cells.values():
            if rec.status == "scored" and rec.scored is not None:
                scored.setdefault(rec.arm, []).append(rec.scored)
        arms_present = {rec.arm for rec in cells.values()}
        per_arm = {}
        for arm in sorted(arms_present):
            recs = scored.get(arm, [])
            calls = [v.get("process", {}).get("oracle_calls") for v in recs]
            per_arm[arm] = {
                "scored": len(recs),
                **_overclaim(recs),
                "oracle_calls_median": _median([c for c in calls if isinstance(c, int)]),
            }
            if arm in GEN_ARMS:
                per_arm[arm]["fidelity"] = _fidelity(recs)
        gen_recs = [v for a in GEN_ARMS for v in scored.get(a, [])]
        contrasts = []
        for label, a, b in CONTRASTS:
            missing = sorted({x for x in a + b if x not in arms_present})
            for denom in ("designed", "scored"):
                if missing and (all(x in missing for x in a) or all(x in missing for x in b)):
                    contrasts.append(
                        {
                            "label": label,
                            "denominator": denom,
                            "arm_a": list(a),
                            "arm_b": list(b),
                            "status": f"not in this run (missing arms: {', '.join(missing)})",
                        }
                    )
                    continue
                c = core_report.arm_contrast(
                    verdicts,
                    list(a),
                    list(b),
                    ruleset=rs,
                    designed_cells=designed,
                    denominator=denom,
                    B=B,
                    seed=SEED,
                    invalidated=invalidated,
                )
                c["label"] = label
                c["status"] = "ok" if not missing else f"partial (missing arms: {', '.join(missing)})"
                c["registered"] = "registered" in label and "not the registered" not in label
                c.update(_exact(cells, tuple(a), tuple(b), denom))
                contrasts.append(c)
        sections.append(
            {
                "ruleset": rs,
                "per_arm": per_arm,
                "generative_pooled": {"scored": len(gen_recs), **_overclaim(gen_recs)},
                "contrasts": contrasts,
            }
        )
    rep = {
        "schema": SCHEMA,
        "core": summary,
        "eval3": {
            "contrast_method": (
                "Fisher's exact test (two-sided) and the Newcombe hybrid-score 95% interval first; beside them the "
                f"pre-registered world-clustered bootstrap, B={B}, seed={SEED}, dbwm.core.report.arm_contrast; "
                "'designed' = every designed cell (no scorable plan = fail), 'scored' = scored "
                "attempts only"
            ),
            "overclaim_bases": ("of_all = scored cells with a readable claim (per trial); of_fails = the failed "
                                "committed plans among them (v7 entry 17: a filed, readable claims.json)"),
            "sections": sections,
        },
    }
    rep["markdown"] = render_markdown(rep)
    return rep


def _fr(x: dict) -> str:
    return f"{x['k']}/{x['n']}" + (f" ({100 * x['k'] / x['n']:.0f}%)" if x["n"] else "")


def _pp(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.0f}"


def _pct3(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.3g}%"


def _arm_row(arm: str, d: dict) -> str:
    cells = [arm, str(d["scored"]), _fr(d["overclaim_of_all"]), _fr(d["overclaim_of_fails"])]
    f = d.get("fidelity")
    if f:
        cells += [
            _fr(f["declared_model"]),
            _pct3(f["claimed_revenue_error_pct"]["median"]),
            _pct3(f["model_on_committed_plan_error_pct"]["median"]),
        ]
    else:
        cells += ["--", "--", "--"]
    calls = d["oracle_calls_median"]
    cells.append("n/a" if calls is None else f"{calls:g}")
    return "| " + " | ".join(cells) + " |"


def _p(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.2g}"


def _contrast_row(c: dict) -> str:
    if c.get("status", "ok").startswith("not in this run"):
        return f"| {c['label']} | {c['denominator']} | -- | -- | {c['status']} | -- | -- | -- |"
    lo, hi = c["ci95"]
    nw = c.get("newcombe95")
    note = "" if c["status"] == "ok" else f" ({c['status']})"
    return (
        f"| {c['label']}{note} | {c['denominator']} | {c['k_a']}/{c['n_a']} | {c['k_b']}/{c['n_b']} | "
        f"{_pp(c['diff'])} | {_p(c.get('fisher_two_sided'))} | "
        f"{'n/a' if nw is None else f'[{_pp(nw[0])}, {_pp(nw[1])}]'} | [{_pp(lo)}, {_pp(hi)}] |"
    )


def render_markdown(rep: dict[str, Any]) -> str:
    L = [
        "# Eval 3 (generative vs grounded) -- run report",
        "",
        "Overclaim is shown over both bases: of all scored cells with a readable claim, and of failures.",
        "",
    ]
    for sec in rep["eval3"]["sections"]:
        L += [
            f"## Ruleset `{sec['ruleset']}`",
            "",
            "| arm | scored | overclaim of all | overclaim of fails | declared model | "
            "median claimed-revenue error | median model-on-plan error | median oracle calls |",
            "|---|---|---|---|---|---|---|---|",
        ]
        L += [_arm_row(arm, d) for arm, d in sec["per_arm"].items()]
        gp = sec["generative_pooled"]
        L += [
            "",
            f"Generative arms pooled: {gp['scored']} scored; overclaim {_fr(gp['overclaim_of_all'])} of all, "
            f"{_fr(gp['overclaim_of_fails'])} of fails.",
            "",
            "| contrast | denominator | a | b | diff (pp) | Fisher p | Newcombe 95% | world-clustered 95% |",
            "|---|---|---|---|---|---|---|---|",
        ]
        L += [_contrast_row(c) for c in sec["contrasts"]]
        L.append("")
    return "\n".join(L) + "\n"
