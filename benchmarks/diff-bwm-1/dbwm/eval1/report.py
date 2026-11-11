"""Eval-1 report over a dbwm run: the paper's aggregation and decision rule, with both denominators.

``build_eval1_report(run_dir)`` is what ``dbwm report --run DIR`` embeds as
``eval_report`` for eval 1 (the generic per-arm tables come from
dbwm.core.report). Per ruleset found in the run it:

  * selects one verdict per designed cell with the core rules (first scored
    attempt; attempts reset/invalidated in attempts.jsonl ignored; audit-flagged
    cells excluded and listed by reason);
  * feeds the scored, non-excluded cells to the paper's report code
    (paper_report.py), so a new run is summarized by exactly the paper's
    aggregation, bootstrap and pre-registered decision rule
    (PROVED/PARTIAL/DISPROVED);
  * adds what the paper's report could not show: the designed-cell
    denominator per arm (a missing, unscored or excluded cell counts as a
    failure and as no overclaim), next to the paper's scored-only rate;
  * writes ``eval1-report.<ruleset>.html`` into the run directory, rendered
    by the paper's HTML code with neutral captions.

Mechanism counters come from the oracle server log (verdict
process.oracle_calls), not from substring matches on tool inputs as in the
paper (planner.py:230-245).
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from dbwm.core import report as core_report
from dbwm.core.verdict import atomic_write_text

from . import paper_report as PR

REPORT_SCHEMA = "dbwm.eval1.report/1"

_LIVE_ADAPTERS = {"claude_code", "command", "openai_compat"}


def _row_key(company: str, tier: str, arm: str, rep: int) -> str:
    """The paper's verdict-file order (report.py sorted ``<company>__<tier>__<arm>__r<rep>.json``)."""
    return f"{company}__{tier}__{arm}__r{rep}.json"


def _data_source(manifest: dict | None) -> str:
    adapter = (manifest or {}).get("adapter")
    if adapter == "stub":
        return "stub-planner"
    if adapter == "replay":
        return "replay"
    if adapter in _LIVE_ADAPTERS:
        return "live-agent"
    return "unknown"


def rows_for_ruleset(verdicts: list[dict], designed, invalidated) -> tuple[list[dict], dict, list[dict]]:
    """(paper-shaped rows sorted in paper order, per-arm designed accounting, exclusions)."""
    cells = core_report.select_cells(verdicts, designed, invalidated)
    rows: list[tuple[str, dict]] = []
    per_arm: dict[str, dict[str, int]] = defaultdict(lambda: {
        "designed": 0, "scored": 0, "excluded": 0, "unscored_or_missing": 0, "valid": 0, "overclaim": 0,
        "overclaim_unknown": 0})
    exclusions = []
    for rec in cells.values():
        a = per_arm[rec.arm]
        a["designed"] += 1
        if rec.status == "excluded":
            a["excluded"] += 1
            exclusions.append({"cell_id": rec.cell_id, "reasons": rec.exclusion})
            continue
        if rec.status != "scored":
            a["unscored_or_missing"] += 1
            exclusions.append({"cell_id": rec.cell_id, "reasons": rec.exclusion})
            continue
        v = rec.scored
        d = dict(v.get("details") or {})
        company, _, tier = rec.instance.rpartition("__")
        row = {k: d.get(k) for k in d if k != "diagnostics"}
        row.update({"condition": rec.arm, "company": d.get("company") or company,
                    "tier": d.get("tier") or tier, "rep": rec.rep})
        proc = v.get("process") or {}
        oc = proc.get("oracle_calls")
        row["bwm_query_calls"] = int(oc or 0)
        row["queried_model"] = bool(oc)
        row["agent_turns"] = proc.get("turns")
        row["agent_cost_usd"] = proc.get("cost_usd")
        # Achievement fields as the verdict states them (paper rows carry them flat).
        row["fully_valid"] = v.get("success") is True
        row["overclaim"] = v.get("overclaim") is True
        a["scored"] += 1
        a["valid"] += row["fully_valid"]
        a["overclaim"] += row["overclaim"]
        a["overclaim_unknown"] += v.get("overclaim") is None
        rows.append((_row_key(row["company"], row["tier"], rec.arm, rec.rep), row))
    rows.sort(key=lambda kv: kv[0])
    return [r for _, r in rows], dict(per_arm), exclusions


def _denominators(per_arm: dict) -> dict:
    out = {}
    for arm in [a for a in PR.CONDITION_ORDER if a in per_arm] + sorted(set(per_arm) - set(PR.CONDITION_ORDER)):
        a = per_arm[arm]
        n_s, n_d = a["scored"], a["designed"]
        out[arm] = dict(a, **{
            "valid_rate_scored": PR.fraction(a["valid"], n_s),
            "valid_rate_designed": PR.fraction(a["valid"], n_d),
            "overclaim_rate_scored": PR.fraction(a["overclaim"], n_s),
            "overclaim_rate_designed": PR.fraction(a["overclaim"], n_d),
        })
    return out


def build_eval1_report(run_dir: Path, *, write_html: bool = True) -> dict[str, Any]:
    run_dir = Path(run_dir)
    verdicts, errors = core_report.load_verdicts(run_dir)
    verdicts = [v for v in verdicts if v.get("eval") == "eval1"]
    manifest = core_report.load_manifest(run_dir)
    designed = (manifest or {}).get("cells")
    invalidated = core_report.load_invalidated(run_dir)
    regime = (manifest or {}).get("regime") or {}
    out: dict[str, Any] = {"schema": REPORT_SCHEMA, "load_errors": errors, "rulesets": {}}
    for ruleset in sorted({v["ruleset"] for v in verdicts}):
        vs = [v for v in verdicts if v["ruleset"] == ruleset]
        rows, per_arm, exclusions = rows_for_ruleset(vs, designed, invalidated)
        section: dict[str, Any] = {
            "designed_denominators": _denominators(per_arm),
            "excluded_or_missing": exclusions,
        }
        if rows:
            run_meta = {
                "data_source": _data_source(manifest),
                "planner_mode": (manifest or {}).get("adapter"),
                "query_backend": "local",
                "companies": " ".join(sorted({r["company"] for r in rows})),
                "tiers": " ".join(t for t in PR.TIER_ORDER if t in {r["tier"] for r in rows}),
                "conditions": " ".join(c for c in PR.CONDITION_ORDER if c in {r["condition"] for r in rows}),
                "reps": regime.get("reps"),
                "agent_model": regime.get("model"),
                "verdict_count": len(rows),
                "ruleset": ruleset,
                "isolation": (manifest or {}).get("isolation"),
                "conforming": (manifest or {}).get("conforming"),
            }
            title = (f"diff-bwm-1 eval 1 run {(manifest or {}).get('run_id', run_dir.name)} "
                     f"({ruleset} ruleset, {len(rows)} scored cells)")
            rendered = PR.render_rows(rows, title=title, run_meta=run_meta, captions="neutral")
            section["decision_rule_verdict"] = rendered["verdict"]
            section["paper_summary"] = rendered["summary"]
            section["summary_md"] = rendered["summary_md"]
            if write_html:
                name = f"eval1-report.{ruleset}.html"
                atomic_write_text(run_dir / name, rendered["html"])
                section["html"] = name
        else:
            section["decision_rule_verdict"] = None
            section["note"] = "no scored, non-excluded cells"
        out["rulesets"][ruleset] = section
    return out
