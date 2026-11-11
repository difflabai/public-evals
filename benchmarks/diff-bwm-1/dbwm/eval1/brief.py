"""Task briefs: what an eval-1 agent is told about its company.

``build_brief`` is oracle.py build_brief (L418-460) and ``brief_markdown`` /
``generate_tasks`` are tasks.py, ported verbatim. A brief holds the goal, the
tier's constraints, the levers with their defaults, the company note and the
base (do-nothing) trajectory. Reference answer plans are never included.
The 60 shipped files in data/eval1/tasks/ regenerate byte-identically
(tests/eval1/test_eval1_engine.py, test_briefs_regenerate_and_sha_prefixes).
"""

from __future__ import annotations

import json
from pathlib import Path

from . import _paths
from .engine import discover_models, evaluate, load_model_and_tiers, prec50
from .score import TIERS, _series_str

# The five companies the published eval-1 matrix used (both HTML reports'
# Methods line). The other five ship as optional instances.
USED_COMPANIES = (
    "atlas-advisory",
    "gigbridge-marketplace",
    "northwind-analytics",
    "sprout-crm",
    "voltcore-devices",
)


@prec50
def build_brief(model, tiers_doc, tier_name):
    """A human/agent-readable task brief (verbatim port)."""
    goal = tiers_doc["goal"]
    tier = tiers_doc["tiers"][tier_name]
    base = evaluate(model)
    horizon = model["horizon"]

    def fmt(driver):
        s = _series_str(base, driver)
        return s if s else ["n/a"] * horizon

    levers = []
    for lid in tiers_doc["levers"]:
        n = model["nodes"].get(lid, {})
        levers.append({
            "id": lid,
            "label": n.get("label", lid),
            "unit": n.get("unit", ""),
            "default": n.get("default", {}).get("values") if n.get("kind") == "leaf" else None,
        })

    return {
        "model_id": model["id"],
        "name": model["name"],
        "category": tiers_doc.get("category", ""),
        "period_grain": model["period_grain"],
        "horizon": horizon,
        "tier": tier_name,
        "tier_note": tier.get("description", ""),
        "notes": tiers_doc.get("notes", ""),
        "goal": goal,
        "constraints": tier["constraints"],
        "levers": levers,
        "base_trajectory": {
            "revenue": fmt("revenue"),
            "net_burn": fmt("net_burn"),
            "cash": fmt("cash"),
            "gross_margin_pct": fmt("gross_margin_pct"),
            "headcount": fmt("headcount"),
        },
    }


def brief_markdown(b):
    """tasks.py brief_markdown, verbatim."""
    g = b["goal"]
    lines = [
        f"# Operating-plan task — {b['name']} ({b['tier']} tier)",
        "",
        f"- **Company:** {b['name']}  (`{b['model_id']}`, {b['category']})",
        f"- **Horizon:** {b['horizon']} {b['period_grain']} periods",
        f"- **Tier:** {b['tier']} — _{b['tier_note']}_",
        f"- **Note:** {b['notes']}",
        "",
        "## Goal",
        f"Grow **{g['metric']}** to at least **{g['target_multiple']}x** its "
        f"period-{g['baseline_period']} value by period {g['target_period']}, "
        f"while satisfying every constraint below.",
        "",
        "## Constraints (all must hold)",
        "| id | kind | driver | limit |",
        "|----|------|--------|-------|",
    ]
    for c in b["constraints"]:
        lines.append(f"| {c['id']} | {c['kind']} | {c['driver']} | {c['limit']} |")
    lines += ["", "## Levers (one value per quarter)",
              "| id | label | unit | default |", "|----|-------|------|---------|"]
    for lv in b["levers"]:
        lines.append(f"| {lv['id']} | {lv['label']} | {lv['unit']} | {lv['default']} |")
    base = b["base_trajectory"]
    lines += ["", "## Base (do-nothing) trajectory",
              "| driver | " + " | ".join(f"Q{i}" for i in range(b["horizon"])) + " |",
              "|--------|" + "|".join(["---"] * b["horizon"]) + "|"]
    for d in ("revenue", "net_burn", "cash", "gross_margin_pct", "headcount"):
        lines.append(f"| {d} | " + " | ".join(base[d]) + " |")
    lines += ["", "## Deliverables",
              "Write `plan.json` (lever bindings) and `claims.json` "
              "(goal_met, constraints_satisfied, projected_final_revenue, rationale)."]
    return "\n".join(lines) + "\n"


def generate_tasks(out_dir: Path, model_ids=None) -> int:
    """tasks.py main(): write <id>__<tier>.json + .md for every company and tier."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ids = list(model_ids) if model_ids else sorted(discover_models())
    n = 0
    for mid in ids:
        model, tiers_doc = load_model_and_tiers(mid)
        for tier in TIERS:
            brief = build_brief(model, tiers_doc, tier)
            stem = f"{mid}__{tier}"
            (out / f"{stem}.json").write_text(json.dumps(brief, indent=2) + "\n", encoding="utf-8")
            (out / f"{stem}.md").write_text(brief_markdown(brief), encoding="utf-8")
            n += 1
    return n


def tasks_dir() -> Path:
    return _paths.data("tasks")


def load_brief(instance: str) -> dict:
    """The shipped (agent-visible) brief for an instance id ``<company>__<tier>``."""
    p = tasks_dir() / f"{instance}.json"
    if not p.is_file():
        raise KeyError(f"no eval-1 brief for instance {instance!r} at data/eval1/tasks/")
    return json.loads(p.read_text(encoding="utf-8"))
