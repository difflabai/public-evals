"""The faithful self-model: the true per-unit funnel written in the eval-3 DSL.

``FAITHFUL_FORMULAS`` is ``model_tool_selftest.FORMULAS`` (model_tool_selftest.py:18-30):
the eleven formulas of the sprout-crm funnel the worlds are built on. With
each unit's public economics and starting values (all printed in the prompt
and in brief.json) as parameters, this model reproduces the true world to
within 1e-9 relative (the paper's selftest tolerance; in practice ~1e-48), so
the self-model fidelity of a correct model is 0 and every gap measured in the
generative arms is the agent's modelling error, not the tool's.

The formulas are not an answer key: they are the structure a generative agent
is asked to discover and they ship in the paper's own selftest.
They are used by the stub agent, the self-test and the golden tests only.
"""

from __future__ import annotations

from typing import Any, Mapping

FAITHFUL_FORMULAS = {
    "leads": "marketing / cost_per_lead",
    "new_customers": "leads * conversion",
    "customers": "prior(customers, customers_init) * (1 - churn) + new_customers",
    "revenue": "customers * acv",
    "cogs": "revenue * (1 - gm_rate)",
    "gross_profit": "revenue - cogs",
    "headcount": "prior(headcount, headcount_init) + hires",
    "payroll": "headcount * salary",
    "total_opex": "payroll + marketing + fixed_opex",
    "net_burn": "total_opex - gross_profit",
    "cash": "prior(cash, cash_init) - net_burn",
}


def unit_params(unit: Mapping[str, Any]) -> dict[str, str]:
    """A brief unit's public economics as the faithful model's parameters."""
    e, s = unit["economics"], unit["starting"]
    return {"acv": e["acv"], "churn": e["churn_rate"], "conversion": e["conversion_rate"],
            "cost_per_lead": e["cost_per_lead"], "gm_rate": e["gm_rate"],
            "fixed_opex": e["fixed_opex"], "salary": e["salary_per_head"],
            "customers_init": s["customers"], "headcount_init": s["headcount"],
            "cash_init": s["cash"]}


def faithful_model(brief: Mapping[str, Any], growth: Mapping[str, list], hires: Mapping[str, list]) -> dict:
    """The faithful model of ``brief``'s portfolio with plan ``growth``/``hires`` (per unit id)."""
    return {
        "horizon": int(brief["horizon"]),
        "formulas": dict(FAITHFUL_FORMULAS),
        "units": {u["id"]: {"params": unit_params(u),
                            "plan": {"marketing": list(growth[u["id"]]), "hires": list(hires[u["id"]])}}
                  for u in brief["units"]},
    }


def plan_from_allocation(alloc: Mapping[str, Any]) -> tuple[dict, dict]:
    """(growth, hires) per unit from an allocation.json object (wrapped or bare)."""
    a = alloc.get("allocation", alloc)
    return dict(a["growth_capital"]), dict(a["eng_headcount"])


__all__ = ["FAITHFUL_FORMULAS", "unit_params", "faithful_model", "plan_from_allocation"]
