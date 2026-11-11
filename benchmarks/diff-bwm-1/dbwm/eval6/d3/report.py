"""Gate report for a run of the D3 self-supply arm (``dbwm report --run`` on a ``dbwm.eval6.d3`` run).

Unit: the first scored, audit-clean attempt per cell (core ``select_cells``). Per world
and figure the reported values are graded exactly as the pilot's scorer graded them
(``score.gate``: agree, consensus-wrong real / at null, retention), against the
references recorded in each verdict (engine-consistent under ``v1``, the pilot's under
``paper``). Attrition, explicit: a rail (USD, clock, turns) or a missing figure is a
missing report, never a zero; the report prints n per world and figure and labels the
gate agree-k, refusing "agree-5", whenever a world has fewer than 5 reports (v7 E.6).
Labelled everywhere: pilot, gate only; self-supply only; not pooled with D1.

Writes eval6_d3_report.json and eval6_d3_report.md into the run directory.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from dbwm.core import report as core_report

from . import score as S

LABEL = "pilot, gate only; self-supply only; not pooled with D1"
RAIL_OUTCOMES = ("cost_stop", "clock_stop", "budget_exhausted")


def _sessions_by_world(cells) -> tuple[dict[str, list[dict]], dict[str, dict], dict[str, Counter]]:
    """(world -> reported figures per session, world -> references, world -> attrition counts)."""
    runs: dict[str, list[dict]] = {}
    refs: dict[str, dict] = {}
    attrition: dict[str, Counter] = {}
    for rec in sorted(cells.values(), key=lambda r: (r.instance, r.rep)):
        c = attrition.setdefault(rec.instance, Counter())
        c["designed"] += 1
        if rec.status != "scored" or rec.scored is None:
            c["no_scored_attempt" if rec.status != "excluded" else "audit_excluded"] += 1
            continue
        d = rec.scored.get("details") or {}
        if rec.scored.get("outcome") in RAIL_OUTCOMES:
            c[f"rail:{rec.scored['outcome']}"] += 1
        figs = d.get("figures") or {}
        if not any(isinstance(v, (int, float)) and not isinstance(v, bool) for v in figs.values()):
            c["no_figures"] += 1
            continue
        c["reported"] += 1
        runs.setdefault(rec.instance, []).append({"figures": figs, "cell_id": rec.cell_id,
                                                  "missing_figures": d.get("missing_figures") or []})
        if rec.instance not in refs and isinstance(d.get("references"), dict):
            refs[rec.instance] = d["references"]
    return runs, refs, attrition


def report(run_dir) -> dict[str, Any]:
    run_dir = Path(run_dir)
    verdicts, _errors = core_report.load_verdicts(run_dir)
    manifest = core_report.load_manifest(run_dir) or {}
    designed = manifest.get("cells") if isinstance(manifest.get("cells"), list) else None
    invalidated = core_report.load_invalidated(run_dir)
    sections = []
    for rs in sorted({v["ruleset"] for v in verdicts}):
        vs = [v for v in verdicts if v["ruleset"] == rs]
        cells = core_report.select_cells(vs, designed, invalidated)
        runs, refs, attrition = _sessions_by_world(cells)
        worlds = {w: r for w, r in runs.items() if w in refs}
        sec: dict[str, Any] = {"ruleset": rs, "n_designed_cells": len(cells),
                               "attrition": {w: dict(sorted(c.items())) for w, c in sorted(attrition.items())}}
        if worlds:
            g = S.gate(worlds, refs)
            sec["gate"] = {k: g[k] for k in ("n_worlds", "n_sessions", "cells", "agree", "rate", "wilson_ci95",
                                             "world_clustered_ci95", "kill1_threshold", "kill1_fires", "label",
                                             "consensus_wrong_real", "consensus_wrong_at_null",
                                             "consensus_wrong_real_cells", "consensus_wrong_at_null_cells",
                                             "retained")}
            sec["per_world"] = {w: {"n_runs": pw["n_runs"], "n_reports": pw["n_reports"], "label": pw["label"],
                                    "agree": pw["agree"],
                                    "figures": {n: (None if f is None else
                                                    {k: f[k] for k in ("n", "agree5", "wrong", "at_null", "spread",
                                                                       "median", "truth", "dev")})
                                                for n, f in pw["figures"].items()}}
                                for w, pw in g["per_world"].items()}
            sec["by_figure"] = S.by_figure(g)
        sections.append(sec)
    out = {"schema": "dbwm.eval6.d3.report/1", "label": LABEL, "eval6_d3": sections,
           "notes": ["Unit: the first scored, audit-clean attempt per cell. A rail hit or a missing figure is a "
                     "missing report, never a zero; n is printed per world and figure.",
                     "The gate label is agree-5 only when every world has 5 reports; otherwise agree-k.",
                     "Under v1 the references are engine-consistent (v7 Appendix E.7); under paper they are the "
                     "pilot's (defective) references, kept as the pilot scored.",
                     "KILL-1 (agree >= 70% of cells) is the pilot's pre-committed gate; the interval of record "
                     "is the world-clustered bootstrap (the Wilson interval ignores clustering)."]}
    (run_dir / "eval6_d3_report.json").write_text(json.dumps(out, indent=2, default=str) + "\n")
    (run_dir / "eval6_d3_report.md").write_text(render_markdown(out))
    return out


def _pct(x) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def render_markdown(rep: dict[str, Any]) -> str:
    L = ["# Eval 6 D3 dispersion gate report", "", f"*{rep['label']}*", ""]
    for sec in rep["eval6_d3"]:
        L += [f"## Ruleset {sec['ruleset']}", "", f"Designed cells: {sec['n_designed_cells']}.", ""]
        L += ["| world | designed | reported | rails | no figures | no scored attempt / excluded |",
              "|---|---|---|---|---|---|"]
        for w, c in sec["attrition"].items():
            rails = sum(v for k, v in c.items() if k.startswith("rail:"))
            L.append(f"| {w} | {c.get('designed', 0)} | {c.get('reported', 0)} | {rails} | {c.get('no_figures', 0)} | "
                     f"{c.get('no_scored_attempt', 0) + c.get('audit_excluded', 0)} |")
        L.append("")
        g = sec.get("gate")
        if not g:
            L += ["No world has a reported figure: no gate.", ""]
            continue
        ci = g["world_clustered_ci95"]
        L += [f"**Gate ({g['label']}):** {g['agree']}/{g['cells']} world x figure cells agree = {_pct(g['rate'])}, "
              f"world-clustered 95% CI [{_pct(ci[0])}, {_pct(ci[1])}] (Wilson, unclustered, for the record: "
              f"[{_pct(g['wilson_ci95'][0])}, {_pct(g['wilson_ci95'][1])}]); KILL-1 at "
              f"{g['kill1_threshold']:.0%} {'FIRES' if g['kill1_fires'] else 'does not fire'}. "
              f"Consensus-wrong: real {g['consensus_wrong_real']}, at null {g['consensus_wrong_at_null']} "
              f"(reported separately, never pooled). Retained worlds (agree on <= 2 of 6): "
              f"{', '.join(g['retained']) or 'none'}.", ""]
        L += ["| world | n (reports) | label | agree of 6 | " + " | ".join(S.FIGURE_NAMES) + " |",
              "|---|---|---|---|" + "---|" * len(S.FIGURE_NAMES)]
        for w, pw in sec["per_world"].items():
            cells = []
            for n in S.FIGURE_NAMES:
                f = pw["figures"].get(n)
                cells.append("n<2" if f is None else
                             f"n={f['n']} {'AGREE' if f['agree5'] else 'disperse'}"
                             + (" wrong" if f["agree5"] and f["wrong"] else "")
                             + (" (at null)" if f["agree5"] and f["wrong"] and f["at_null"] else ""))
            L.append(f"| {w} | {pw['n_reports']} | {pw['label']} | {pw['agree']} | " + " | ".join(cells) + " |")
        L.append("")
    L += [f"- {n}" for n in rep["notes"]]
    return "\n".join(L) + "\n"
