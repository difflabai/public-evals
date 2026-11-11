"""Generator for the eval-1 company matrix and its calibration answer key (SCORER-SIDE ONLY).

Port of the paper harness's matrix generator. It authors the 10 synthetic
companies (operating model + eval tiers + base scenario) and the calibration
report holding each company's naive and careful reference plans. Its source
EMBEDS those reference plans, i.e. the answer key, so it lives in the package
(never staged into a sandbox) and its outputs go to private/eval1/.

Tier semantics are machine-calibrated. Each company defines two reference
plans (naive: brute-force growth; careful: staged ramp plus pricing/efficiency
mix). ``calibrate`` evaluates base/naive/careful with the eval-1 engine, derives
the soft/hard/hostile limits from those trajectories, and asserts:
  - base    passes every tier's constraints but misses the growth goal
  - naive   reaches the goal, passes soft, violates hard AND hostile
  - careful reaches the goal and passes every tier including hostile
            (hostile is feasible BY CONSTRUCTION)

Changes from the paper copy (docs/instrument-changes/eval1.md E1-G01):
  - repo-layout paths and the private live-engine anchor selftest are removed
    (the paper's main() crashed without that private file); the engine module
    is the single evaluator (the generator's own copy differed only in error
    message text);
  - ``emit`` writes the flat package layout <dir>/worlds/<model_id>/ instead of
    the original nested registry layout; file CONTENTS are byte-identical to
    the shipped worlds and calibration report (tests/eval1/test_eval1_engine.py,
    test_generator_regenerates_worlds_and_calibration);
  - everything runs under the engine's fixed 50-digit decimal context.

Usage (writes into a directory you choose):
    python -m dbwm.eval1.gen.generate_matrix --out <dir>
"""

import argparse
import json
import math
import sys
from decimal import Decimal, localcontext
from pathlib import Path

from dbwm.eval1.engine import evaluate, paper_context, series

STAMP = "2026-06-11T00:00:00Z"
HORIZON = 6
VERSION = "matrix-v1"


# ---------------------------------------------------------------------------
# Formula DSL (the operating-model JSON formula format)
# ---------------------------------------------------------------------------

def C(v):
    return {"op": "const", "value": str(v)}


def R(i):
    return {"op": "ref", "id": i}


PRIOR = {"op": "prior"}


def PR(i):
    return {"op": "prior_ref", "id": i}


def _bin(op, lhs, rhs):
    return {"op": op, "lhs": lhs, "rhs": rhs}


def ADD(a, b, *rest):
    f = _bin("add", a, b)
    for r in rest:
        f = _bin("add", f, r)
    return f


def SUB(a, b):
    return _bin("sub", a, b)


def MUL(a, b, *rest):
    f = _bin("mul", a, b)
    for r in rest:
        f = _bin("mul", f, r)
    return f


def DIV(a, b):
    return _bin("div", a, b)


def leaf(id_, label, unit, values):
    if not isinstance(values, list):
        values = [values] * HORIZON
    return {
        "kind": "leaf", "id": id_, "label": label, "unit": unit,
        "default": {"values": [str(v) for v in values]},
    }


def comp(id_, label, unit, formula):
    return {"kind": "computed", "id": id_, "label": label, "unit": unit,
            "formula": formula}


def roll(id_, label, unit, initial, formula):
    return {"kind": "roll_forward", "id": id_, "label": label, "unit": unit,
            "initial": str(initial), "formula": formula}


def out(id_, label, category):
    return {"id": id_, "label": label, "category": category}


# ---------------------------------------------------------------------------
# Shared structural blocks
# ---------------------------------------------------------------------------

def ramp_block(nodes, *, hires_id="hires_per_quarter",
               pool_id, pool_label, pool_initial, attrition=None):
    """hires -> 2-quarter ramp -> productive pool.

    hires_lag2[t] = hires[t-2], so Q1 hires only enter the pool in Q3 —
    the multi-step-coherence trap from the eval plan (H4). Headcount and
    payroll, by contrast, grow the quarter someone is hired (see
    staff_block, which declares the hires leaf), so naive plans pay for
    capacity they can't use yet.
    """
    nodes += [
        comp("hires_lag1", "Hires (1q ago)", "people/quarter", PR(hires_id)),
        comp("hires_lag2", "Hires (2q ago)", "people/quarter", PR("hires_lag1")),
    ]
    grow = ADD(MUL(PRIOR, SUB(C(1), C(attrition))) if attrition else PRIOR,
               R("hires_lag2"))
    nodes.append(roll(pool_id, pool_label, "people", pool_initial, grow))


def staff_block(nodes, *, headcount_initial, salary, base_hires,
                hires_id="hires_per_quarter"):
    nodes += [
        leaf(hires_id, "Net hires per quarter", "people/quarter", base_hires),
        roll("headcount", "Headcount (ending)", "people", headcount_initial,
             ADD(PRIOR, R(hires_id))),
        leaf("salary_per_head", "Fully-loaded comp per head", "usd/quarter", salary),
        comp("payroll_cost", "Payroll", "usd/quarter",
             MUL(R("headcount"), R("salary_per_head"))),
    ]


def cash_block(nodes, *, initial_cash, opex_terms, capex_term=None):
    """gross_profit/margin, total_opex, net_burn (positive = burning), cash."""
    nodes += [
        comp("gross_profit", "Gross profit", "usd/quarter",
             SUB(R("revenue"), R("cogs"))),
        comp("gross_margin_pct", "Gross margin", "ratio",
             DIV(R("gross_profit"), R("revenue"))),
        comp("total_opex", "Total OpEx", "usd/quarter",
             ADD(*[R(t) for t in opex_terms]) if len(opex_terms) > 1
             else R(opex_terms[0])),
        comp("net_burn", "Net burn (positive = burning)", "usd/quarter",
             ADD(SUB(R("total_opex"), R("gross_profit")),
                 R(capex_term)) if capex_term
             else SUB(R("total_opex"), R("gross_profit"))),
        roll("cash", "Cash (ending)", "usd", initial_cash,
             SUB(PRIOR, R("net_burn"))),
    ]


STANDARD_OUTPUTS = [
    out("revenue", "Revenue", "revenue"),
    out("cogs", "Cost of revenue", "cost_of_revenue"),
    out("gross_margin_pct", "Gross margin", "other"),
    out("payroll_cost", "Payroll", "operating_expense"),
    out("total_opex", "Total OpEx", "operating_expense"),
    out("net_burn", "Net burn", "other"),
    out("cash", "Cash (ending)", "other"),
    out("headcount", "Headcount", "other"),
]


def model_shell(id_, name, company_id, topology, nodes, outputs,
                cost_behavior=None, wc_tags=None, capex_tags=None):
    return {
        "id": id_,
        "name": name,
        "company_id": company_id,
        "version": VERSION,
        "period_grain": "quarterly",
        "horizon": HORIZON,
        "topology": topology,
        "nodes": {n["id"]: n for n in nodes},
        "cost_behavior": cost_behavior or {},
        "working_capital_tags": wc_tags or {},
        "capex_asset_tags": capex_tags or {},
        "outputs": outputs,
        "created_at": STAMP,
        "updated_at": STAMP,
    }


def ramp_series(start, factor):
    """Geometric ramp as decimal strings: start, start*f, start*f^2, ..."""
    vals, v = [], Decimal(str(start))
    for _ in range(HORIZON):
        vals.append(str(v.quantize(Decimal("0.01"))))
        v *= Decimal(str(factor))
    return vals


def flat(v):
    return [str(v)] * HORIZON


# ---------------------------------------------------------------------------
# Company definitions. Each returns:
#   dict(category, model, levers, naive(bindings), careful(bindings), notes)
# ---------------------------------------------------------------------------

def saas_funnel(*, id_, name, company_id, customers0, acv, churn, marketing,
                cpl, conversion, gm, headcount0, salary, fixed, cash0,
                base_hires, naive, careful, notes):
    nodes = [
        leaf("marketing_spend", "Marketing spend", "usd/quarter", marketing),
        leaf("cost_per_lead", "Cost per lead", "usd", cpl),
        leaf("conversion_rate", "Lead -> customer conversion", "ratio", conversion),
        leaf("acv", "Avg contract value", "usd/quarter", acv),
        leaf("churn_rate", "Quarterly churn", "ratio", churn),
        leaf("gm_rate", "Gross margin rate", "ratio", gm),
        leaf("fixed_opex", "Fixed OpEx", "usd/quarter", fixed),
        comp("traffic", "Leads", "leads/quarter",
             DIV(R("marketing_spend"), R("cost_per_lead"))),
        comp("new_customers", "New customers", "customers/quarter",
             MUL(R("traffic"), R("conversion_rate"))),
        roll("customers", "Customer base (ending)", "customers", customers0,
             ADD(MUL(PRIOR, SUB(C(1), R("churn_rate"))), R("new_customers"))),
        comp("revenue", "Revenue", "usd/quarter",
             MUL(R("customers"), R("acv"))),
        comp("cogs", "Cost of revenue", "usd/quarter",
             MUL(R("revenue"), SUB(C(1), R("gm_rate")))),
    ]
    staff_block(nodes, headcount_initial=headcount0, salary=salary,
                base_hires=base_hires)
    # hires feed CS/onboarding capacity after the 2-quarter ramp
    ramp_block(nodes, pool_id="ramped_staff",
               pool_label="Ramped staff (productive)",
               pool_initial=headcount0)
    cash_block(nodes, initial_cash=cash0,
               opex_terms=["payroll_cost", "marketing_spend", "fixed_opex"])
    topology = {
        "kind": "funnel",
        "traffic_driver": "traffic", "conversion_driver": "conversion_rate",
        "acv_driver": "acv", "churn_driver": "churn_rate",
        "new_customers_node": "new_customers",
        "ending_base_node": "customers", "revenue_node": "revenue",
    }
    outputs = ([out("marketing_spend", "Marketing spend", "operating_expense"),
                out("traffic", "Leads", "other"),
                out("new_customers", "New customers", "other"),
                out("customers", "Customers (ending)", "other"),
                out("ramped_staff", "Ramped staff", "other")]
               + STANDARD_OUTPUTS)
    model = model_shell(
        id_, name, company_id, topology, nodes, outputs,
        cost_behavior={"cogs": "variable", "marketing_spend": "variable",
                       "payroll_cost": "variable", "fixed_opex": "fixed"})
    return {
        "category": "saas", "model": model,
        "levers": ["marketing_spend", "hires_per_quarter", "acv"],
        "naive": naive, "careful": careful, "notes": notes,
    }


def build_sprout():
    return saas_funnel(
        id_="sprout-crm", name="Sprout CRM — Seed SaaS", company_id="sprout",
        customers0=120, acv=600, churn="0.06", marketing=45000, cpl=25,
        conversion="0.04", gm="0.82", headcount0=8, salary=21000,
        fixed=30000, cash0=1500000, base_hires=1,
        naive={
            "marketing_spend": flat(180000),
            "hires_per_quarter": flat(4),
        },
        careful={
            "marketing_spend": ["65000", "80000", "90000", "90000", "90000", "90000"],
            "acv": ramp_series(620, 1.05),
            "hires_per_quarter": ["2", "2", "1", "1", "1", "1"],
        },
        notes="Seed-stage SaaS. Naive quadruples marketing and over-hires; "
              "careful blends a staged marketing ramp with ~5%/q price rises.",
    )


def build_globalgrid():
    return saas_funnel(
        id_="globalgrid-cloud", name="GlobalGrid Cloud — Public-scale SaaS",
        company_id="globalgrid",
        customers0=40000, acv=5000, churn="0.02", marketing=30000000, cpl=800,
        conversion="0.05", gm="0.78", headcount0=3800, salary=35000,
        fixed=15000000, cash0=900000000, base_hires=40,
        naive={
            "marketing_spend": flat(90000000),
            "hires_per_quarter": flat(300),
        },
        careful={
            "marketing_spend": ["42000000", "48000000", "52000000",
                                "52000000", "52000000", "52000000"],
            "acv": ramp_series(5100, 1.03),
            "hires_per_quarter": ["100", "100", "80", "80", "60", "60"],
        },
        notes="Public-scale SaaS. Efficiency, not survival: constraints bind "
              "on burn discipline at large absolute numbers.",
    )


def build_northwind():
    """Series B SaaS, AE-capacity revenue engine (capacity topology)."""
    nodes = [
        leaf("deals_per_ae", "Deals per ramped AE per quarter", "deals", 3),
        leaf("acv", "Avg contract value", "usd/quarter", 9000),
        leaf("churn_rate", "Quarterly churn", "ratio", "0.03"),
        leaf("gm_rate", "Gross margin rate", "ratio", "0.80"),
        leaf("marketing_spend", "Marketing spend", "usd/quarter", 350000),
        leaf("cost_per_mql_deal", "Marketing cost per sourced deal", "usd", 9000),
        leaf("fixed_opex", "Fixed OpEx", "usd/quarter", 250000),
    ]
    ramp_block(nodes, pool_id="ramped_aes",
               pool_label="Ramped AEs", pool_initial=10)
    nodes += [
        comp("ae_sourced_deals", "AE-sourced deals", "deals/quarter",
             MUL(R("ramped_aes"), R("deals_per_ae"))),
        comp("mkt_sourced_deals", "Marketing-sourced deals", "deals/quarter",
             DIV(R("marketing_spend"), R("cost_per_mql_deal"))),
        comp("new_customers", "New customers", "customers/quarter",
             ADD(R("ae_sourced_deals"), R("mkt_sourced_deals"))),
        roll("customers", "Customer base (ending)", "customers", 333,
             ADD(MUL(PRIOR, SUB(C(1), R("churn_rate"))), R("new_customers"))),
        comp("revenue", "Revenue", "usd/quarter", MUL(R("customers"), R("acv"))),
        comp("cogs", "Cost of revenue", "usd/quarter",
             MUL(R("revenue"), SUB(C(1), R("gm_rate")))),
    ]
    staff_block(nodes, headcount_initial=78, salary=30000, base_hires=2)
    cash_block(nodes, initial_cash=18000000,
               opex_terms=["payroll_cost", "marketing_spend", "fixed_opex"])
    topology = {"kind": "capacity", "capacity_driver": "ramped_aes",
                "utilization_driver": "deals_per_ae", "price_driver": "acv",
                "revenue_node": "revenue"}
    outputs = ([out("ramped_aes", "Ramped AEs", "other"),
                out("new_customers", "New customers", "other"),
                out("customers", "Customers (ending)", "other"),
                out("marketing_spend", "Marketing spend", "operating_expense")]
               + STANDARD_OUTPUTS)
    model = model_shell(
        "northwind-analytics", "Northwind Analytics — Series B SaaS",
        "northwind", topology, nodes, outputs,
        cost_behavior={"cogs": "variable", "marketing_spend": "variable",
                       "payroll_cost": "variable", "fixed_opex": "fixed"})
    return {
        "category": "saas", "model": model,
        "levers": ["marketing_spend", "hires_per_quarter", "acv", "deals_per_ae"],
        "naive": {
            "hires_per_quarter": flat(12),
            "marketing_spend": flat(1050000),
        },
        "careful": {
            "hires_per_quarter": ["4", "4", "3", "3", "2", "2"],
            "marketing_spend": ["500000", "550000", "600000",
                                "600000", "600000", "600000"],
            "acv": ramp_series(9200, 1.04),
        },
        "notes": "Series B SaaS with an AE-capacity engine: Q1 AE hires only "
                 "produce bookings from Q3 (2-quarter ramp), while payroll "
                 "starts immediately — the H4 coherence trap.",
    }


def build_harborlist():
    """Goods marketplace with explicit acquisition cohorts (cohort topology)."""
    nodes = [
        leaf("marketing_spend", "Buyer acquisition spend", "usd/quarter", 600000),
        leaf("cac", "Blended CAC per buyer", "usd", 60),
        leaf("gmv_per_buyer", "GMV per active buyer", "usd/quarter", 220),
        leaf("take_rate", "Marketplace take rate", "ratio", "0.12"),
        leaf("processing_rate", "Payment processing (share of GMV)", "ratio", "0.025"),
        leaf("fixed_opex", "Fixed OpEx", "usd/quarter", 400000),
        comp("new_buyers", "New buyers acquired", "buyers/quarter",
             DIV(R("marketing_spend"), R("cac"))),
    ]
    lanes = []
    rev_terms, buyer_terms = [], []
    # Lane 0 is the pre-existing buyer base; lanes 1..5 are per-quarter vintages.
    base_lane = {
        "cohort_id": "c_base", "acquisition_period": 0, "acquisition_size": "25000",
        "new_customers_driver": "new_buyers_c_base", "churn_driver": "churn_c_base",
        "customers_node": "buyers_c_base", "revenue_node": "gmv_c_base",
    }
    nodes += [
        leaf("new_buyers_c_base", "New buyers (base cohort)", "buyers/quarter", 0),
        leaf("churn_c_base", "Churn (base cohort)", "ratio", "0.08"),
        roll("buyers_c_base", "Buyers (base cohort)", "buyers", 25000,
             ADD(MUL(PRIOR, SUB(C(1), R("churn_c_base"))), R("new_buyers_c_base"))),
        comp("gmv_c_base", "GMV (base cohort)", "usd/quarter",
             MUL(R("buyers_c_base"), R("gmv_per_buyer"))),
    ]
    lanes.append(base_lane)
    rev_terms.append("gmv_c_base")
    buyer_terms.append("buyers_c_base")
    for q in range(HORIZON):
        cid = f"c{q}"
        mask = ["1" if t == q else "0" for t in range(HORIZON)]
        nodes += [
            leaf(f"mask_{cid}", f"Acquisition mask (vintage {cid})", "ratio", mask),
            leaf(f"churn_{cid}", f"Churn (vintage {cid})", "ratio", "0.10"),
            comp(f"new_buyers_{cid}", f"New buyers (vintage {cid})", "buyers/quarter",
                 MUL(R(f"mask_{cid}"), R("new_buyers"))),
            roll(f"buyers_{cid}", f"Buyers (vintage {cid})", "buyers", 0,
                 ADD(MUL(PRIOR, SUB(C(1), R(f"churn_{cid}"))),
                     R(f"new_buyers_{cid}"))),
            comp(f"gmv_{cid}", f"GMV (vintage {cid})", "usd/quarter",
                 MUL(R(f"buyers_{cid}"), R("gmv_per_buyer"))),
        ]
        lanes.append({
            "cohort_id": cid, "acquisition_period": q, "acquisition_size": "0",
            "new_customers_driver": f"new_buyers_{cid}",
            "churn_driver": f"churn_{cid}",
            "customers_node": f"buyers_{cid}", "revenue_node": f"gmv_{cid}",
        })
        rev_terms.append(f"gmv_{cid}")
        buyer_terms.append(f"buyers_{cid}")
    nodes += [
        comp("gmv", "Total GMV", "usd/quarter", ADD(*[R(t) for t in rev_terms])),
        comp("active_buyers", "Active buyers", "buyers",
             ADD(*[R(t) for t in buyer_terms])),
        comp("revenue", "Net revenue (take rate x GMV)", "usd/quarter",
             MUL(R("gmv"), R("take_rate"))),
        comp("cogs", "Cost of revenue (payments + support)", "usd/quarter",
             MUL(R("gmv"), R("processing_rate"))),
    ]
    staff_block(nodes, headcount_initial=60, salary=26000, base_hires=2)
    ramp_block(nodes, pool_id="ramped_ops",
               pool_label="Ramped ops staff", pool_initial=60)
    cash_block(nodes, initial_cash=14000000,
               opex_terms=["payroll_cost", "marketing_spend", "fixed_opex"])
    topology = {"kind": "cohort", "lanes": lanes,
                "total_revenue_node": "gmv", "arpu_driver": "gmv_per_buyer"}
    outputs = ([out("gmv", "GMV", "other"),
                out("active_buyers", "Active buyers", "other"),
                out("take_rate", "Take rate", "other"),
                out("marketing_spend", "Acquisition spend", "operating_expense")]
               + STANDARD_OUTPUTS)
    model = model_shell(
        "harborlist-marketplace", "HarborList — Goods Marketplace",
        "harborlist", topology, nodes, outputs,
        cost_behavior={"cogs": "variable", "marketing_spend": "variable",
                       "payroll_cost": "variable", "fixed_opex": "fixed"})
    return {
        "category": "marketplace", "model": model,
        "levers": ["marketing_spend", "hires_per_quarter", "take_rate"],
        "naive": {
            "marketing_spend": flat(2400000),
            "hires_per_quarter": flat(8),
        },
        "careful": {
            "marketing_spend": ["1000000", "1100000", "1200000",
                                "1200000", "1200000", "1200000"],
            "take_rate": ["0.125", "0.13", "0.135", "0.14", "0.145", "0.15"],
            "hires_per_quarter": ["3", "3", "2", "2", "2", "2"],
        },
        "notes": "Goods marketplace with per-vintage cohorts (10%/q vintage "
                 "decay vs 8%/q for the legacy base). Take-rate creep is the "
                 "margin-friendly lever naive plans ignore.",
    }


def build_gigbridge():
    """Labor marketplace, aggregate two-sided dynamics (funnel topology)."""
    nodes = [
        leaf("marketing_spend", "Employer acquisition spend", "usd/quarter", 350000),
        leaf("cac_employer", "CAC per employer", "usd", 700),
        leaf("employer_churn", "Employer churn", "ratio", "0.07"),
        leaf("jobs_per_employer", "Jobs posted per employer", "jobs/quarter", 14),
        leaf("fill_rate", "Job fill rate", "ratio", "0.70"),
        leaf("avg_job_value", "Avg job value", "usd", 480),
        leaf("take_rate", "Take rate", "ratio", "0.18"),
        leaf("support_cost_per_fill", "Support cost per filled job", "usd", 9),
        leaf("fixed_opex", "Fixed OpEx", "usd/quarter", 280000),
        comp("new_employers", "New employers", "employers/quarter",
             DIV(R("marketing_spend"), R("cac_employer"))),
        roll("employers", "Active employers", "employers", 800,
             ADD(MUL(PRIOR, SUB(C(1), R("employer_churn"))), R("new_employers"))),
        comp("jobs_posted", "Jobs posted", "jobs/quarter",
             MUL(R("employers"), R("jobs_per_employer"))),
        comp("filled_jobs", "Filled jobs", "jobs/quarter",
             MUL(R("jobs_posted"), R("fill_rate"))),
        comp("revenue", "Revenue", "usd/quarter",
             MUL(R("filled_jobs"), R("avg_job_value"), R("take_rate"))),
        comp("cogs", "Cost of revenue (support + payments)", "usd/quarter",
             MUL(R("filled_jobs"), R("support_cost_per_fill"))),
    ]
    staff_block(nodes, headcount_initial=45, salary=24000, base_hires=2)
    cash_block(nodes, initial_cash=14000000,
               opex_terms=["payroll_cost", "marketing_spend", "fixed_opex"])
    topology = {
        "kind": "funnel",
        "traffic_driver": "new_employers", "conversion_driver": "fill_rate",
        "acv_driver": "avg_job_value", "churn_driver": "employer_churn",
        "new_customers_node": "new_employers",
        "ending_base_node": "employers", "revenue_node": "revenue",
    }
    outputs = ([out("employers", "Active employers", "other"),
                out("jobs_posted", "Jobs posted", "other"),
                out("filled_jobs", "Filled jobs", "other"),
                out("fill_rate", "Fill rate", "other"),
                out("marketing_spend", "Acquisition spend", "operating_expense")]
               + STANDARD_OUTPUTS)
    model = model_shell(
        "gigbridge-marketplace", "GigBridge — Labor Marketplace",
        "gigbridge", topology, nodes, outputs,
        cost_behavior={"cogs": "variable", "marketing_spend": "variable",
                       "payroll_cost": "variable", "fixed_opex": "fixed"})
    return {
        "category": "marketplace", "model": model,
        "levers": ["marketing_spend", "hires_per_quarter", "fill_rate", "take_rate"],
        "naive": {
            "marketing_spend": flat(2400000),
            "hires_per_quarter": flat(7),
        },
        "careful": {
            "marketing_spend": ["600000", "650000", "700000",
                                "700000", "700000", "700000"],
            "fill_rate": ["0.72", "0.75", "0.78", "0.80", "0.82", "0.84"],
            "take_rate": ["0.185", "0.19", "0.195", "0.20", "0.20", "0.20"],
            "hires_per_quarter": ["3", "2", "2", "2", "1", "1"],
        },
        "notes": "Labor marketplace. Fill-rate (ops quality) and take-rate are "
                 "the efficient levers; naive plans buy demand they can't fill.",
    }


def build_atlas():
    """Consulting (capacity topology): billable pool ramps 2 quarters."""
    nodes = [
        leaf("utilization", "Billable utilization", "ratio", "0.65"),
        leaf("rate_per_consultant", "Realized revenue per billable consultant",
             "usd/quarter", 120000),
        leaf("delivery_cost_rate", "Delivery cost share of revenue", "ratio", "0.18"),
        leaf("bd_spend", "Business development spend", "usd/quarter", 180000),
        leaf("fixed_opex", "Fixed OpEx", "usd/quarter", 220000),
    ]
    ramp_block(nodes, pool_id="billable_pool",
               pool_label="Billable consultants (ramped)", pool_initial=40,
               attrition="0.04")
    nodes += [
        comp("revenue", "Revenue", "usd/quarter",
             MUL(R("billable_pool"), R("utilization"), R("rate_per_consultant"))),
        comp("cogs", "Cost of revenue (delivery)", "usd/quarter",
             MUL(R("revenue"), R("delivery_cost_rate"))),
    ]
    staff_block(nodes, headcount_initial=52, salary=42000, base_hires=2)
    cash_block(nodes, initial_cash=18000000,
               opex_terms=["payroll_cost", "bd_spend", "fixed_opex"])
    topology = {"kind": "capacity", "capacity_driver": "billable_pool",
                "utilization_driver": "utilization",
                "price_driver": "rate_per_consultant", "revenue_node": "revenue"}
    outputs = ([out("billable_pool", "Billable consultants", "other"),
                out("utilization", "Utilization", "other"),
                out("bd_spend", "BD spend", "operating_expense")]
               + STANDARD_OUTPUTS)
    model = model_shell(
        "atlas-advisory", "Atlas Advisory — Consulting", "atlas",
        topology, nodes, outputs,
        cost_behavior={"cogs": "variable", "bd_spend": "variable",
                       "payroll_cost": "variable", "fixed_opex": "fixed"})
    return {
        "category": "services", "model": model,
        "levers": ["hires_per_quarter", "utilization", "rate_per_consultant",
                   "bd_spend"],
        "naive": {
            "hires_per_quarter": flat(10),
            "bd_spend": flat(540000),
        },
        "careful": {
            "hires_per_quarter": ["4", "4", "3", "2", "2", "2"],
            "utilization": ["0.68", "0.71", "0.74", "0.76", "0.78", "0.78"],
            "rate_per_consultant": ramp_series(123000, 1.04),
            "bd_spend": ["260000", "280000", "300000",
                         "300000", "300000", "300000"],
        },
        "notes": "Consulting: hires are paid immediately but bill only after a "
                 "2-quarter ramp (with 4%/q attrition of the billable pool). "
                 "Utilization and rate are the efficient levers.",
    }


def build_brightclean():
    """Field services (capacity topology): crews x jobs x price."""
    nodes = [
        leaf("jobs_per_crew", "Jobs per crew per quarter", "jobs", 130),
        leaf("price_per_job", "Price per job", "usd", 320),
        leaf("supplies_rate", "Supplies share of revenue", "ratio", "0.12"),
        leaf("crew_cost", "Crew cost (per crew)", "usd/quarter", 28000),
        leaf("marketing_spend", "Local marketing", "usd/quarter", 120000),
        leaf("fixed_opex", "Fixed OpEx", "usd/quarter", 90000),
    ]
    ramp_block(nodes, pool_id="crews",
               pool_label="Active crews (trained)", pool_initial=24)
    nodes += [
        comp("jobs_done", "Jobs completed", "jobs/quarter",
             MUL(R("crews"), R("jobs_per_crew"))),
        comp("revenue", "Revenue", "usd/quarter",
             MUL(R("jobs_done"), R("price_per_job"))),
        comp("crew_wages", "Crew wages", "usd/quarter",
             MUL(R("crews"), R("crew_cost"))),
        comp("supplies_cost", "Supplies", "usd/quarter",
             MUL(R("revenue"), R("supplies_rate"))),
        comp("cogs", "Cost of revenue (wages + supplies)", "usd/quarter",
             ADD(R("crew_wages"), R("supplies_cost"))),
    ]
    staff_block(nodes, headcount_initial=12, salary=20000, base_hires=1)
    cash_block(nodes, initial_cash=8000000,
               opex_terms=["payroll_cost", "marketing_spend", "fixed_opex"])
    topology = {"kind": "capacity", "capacity_driver": "crews",
                "utilization_driver": "jobs_per_crew",
                "price_driver": "price_per_job", "revenue_node": "revenue"}
    outputs = ([out("crews", "Active crews", "other"),
                out("jobs_done", "Jobs completed", "other"),
                out("price_per_job", "Price per job", "other"),
                out("marketing_spend", "Local marketing", "operating_expense")]
               + STANDARD_OUTPUTS)
    model = model_shell(
        "brightclean-services", "BrightClean — Field Services", "brightclean",
        topology, nodes, outputs,
        cost_behavior={"cogs": "variable", "marketing_spend": "variable",
                       "payroll_cost": "variable", "fixed_opex": "fixed"})
    return {
        "category": "services", "model": model,
        "levers": ["hires_per_quarter", "price_per_job", "jobs_per_crew",
                   "marketing_spend"],
        "naive": {
            "hires_per_quarter": flat(6),
            "marketing_spend": flat(480000),
        },
        "careful": {
            "hires_per_quarter": ["2", "2", "2", "1", "1", "1"],
            "price_per_job": ramp_series(330, 1.04),
            "jobs_per_crew": ["133", "136", "139", "142", "145", "148"],
            "marketing_spend": ["180000", "200000", "220000",
                                "220000", "220000", "220000"],
        },
        "notes": "Field services. Crew hires train for 2 quarters before "
                 "producing; price and route-density (jobs/crew) are the "
                 "margin-safe levers.",
    }


def build_voltcore():
    """Device maker (funnel topology): units + consumables attach, capex."""
    nodes = [
        leaf("marketing_spend", "Demand-gen spend", "usd/quarter", 900000),
        leaf("cost_per_lead", "Cost per qualified lead", "usd", 150),
        leaf("close_rate", "Lead -> unit close rate", "ratio", "0.20"),
        leaf("asp", "Avg selling price per unit", "usd", 1800),
        leaf("unit_cost", "Unit production cost", "usd", 1150),
        leaf("consumable_per_unit", "Consumables revenue per installed unit",
             "usd/quarter", 90),
        leaf("consumable_margin", "Consumables gross margin", "ratio", "0.55"),
        leaf("fleet_attrition", "Installed-base attrition", "ratio", "0.03"),
        leaf("capex", "Tooling capex", "usd/quarter",
             ["250000", "250000", "900000", "250000", "250000", "250000"]),
        leaf("dso", "Days sales outstanding", "days", 45),
        leaf("dpo", "Days payable outstanding", "days", 38),
        leaf("dio", "Days inventory outstanding", "days", 60),
        leaf("fixed_opex", "Fixed OpEx", "usd/quarter", 350000),
        comp("leads", "Qualified leads", "leads/quarter",
             DIV(R("marketing_spend"), R("cost_per_lead"))),
        comp("units_sold", "Units sold", "units/quarter",
             MUL(R("leads"), R("close_rate"))),
        roll("installed_base", "Installed base", "units", 5200,
             ADD(MUL(PRIOR, SUB(C(1), R("fleet_attrition"))), R("units_sold"))),
        comp("device_revenue", "Device revenue", "usd/quarter",
             MUL(R("units_sold"), R("asp"))),
        comp("consumable_revenue", "Consumables revenue", "usd/quarter",
             MUL(R("installed_base"), R("consumable_per_unit"))),
        comp("revenue", "Revenue", "usd/quarter",
             ADD(R("device_revenue"), R("consumable_revenue"))),
        comp("device_cogs", "Device COGS", "usd/quarter",
             MUL(R("units_sold"), R("unit_cost"))),
        comp("consumable_cogs", "Consumables COGS", "usd/quarter",
             MUL(R("consumable_revenue"), SUB(C(1), R("consumable_margin")))),
        comp("cogs", "Cost of revenue", "usd/quarter",
             ADD(R("device_cogs"), R("consumable_cogs"))),
        comp("ar", "Accounts receivable", "usd",
             DIV(MUL(R("revenue"), R("dso")), C(91))),
        comp("ap", "Accounts payable", "usd",
             DIV(MUL(R("cogs"), R("dpo")), C(91))),
        comp("inventory", "Inventory", "usd",
             DIV(MUL(R("cogs"), R("dio")), C(91))),
    ]
    staff_block(nodes, headcount_initial=70, salary=27000, base_hires=2)
    cash_block(nodes, initial_cash=30000000,
               opex_terms=["payroll_cost", "marketing_spend", "fixed_opex"],
               capex_term="capex")
    topology = {
        "kind": "funnel",
        "traffic_driver": "leads", "conversion_driver": "close_rate",
        "acv_driver": "asp", "churn_driver": "fleet_attrition",
        "new_customers_node": "units_sold",
        "ending_base_node": "installed_base", "revenue_node": "revenue",
    }
    outputs = ([out("units_sold", "Units sold", "other"),
                out("installed_base", "Installed base", "other"),
                out("device_revenue", "Device revenue", "revenue"),
                out("consumable_revenue", "Consumables revenue", "revenue"),
                out("marketing_spend", "Demand-gen spend", "operating_expense"),
                out("capex", "Capex", "capex"),
                out("ar", "Accounts receivable", "working_capital"),
                out("ap", "Accounts payable", "working_capital"),
                out("inventory", "Inventory", "working_capital")]
               + STANDARD_OUTPUTS)
    model = model_shell(
        "voltcore-devices", "VoltCore — Hardware Devices", "voltcore",
        topology, nodes, outputs,
        cost_behavior={"cogs": "variable", "marketing_spend": "variable",
                       "payroll_cost": "variable", "fixed_opex": "fixed"},
        wc_tags={"ar": "accounts_receivable", "ap": "accounts_payable",
                 "inventory": "inventory"})
    return {
        "category": "hardware", "model": model,
        "levers": ["marketing_spend", "hires_per_quarter", "asp",
                   "consumable_per_unit", "capex"],
        "naive": {
            # Brute force: 4x demand spend AND a price cut to chase volume —
            # burn and margin both breach.
            "marketing_spend": flat(3600000),
            "asp": flat(1620),
            "hires_per_quarter": flat(8),
        },
        "careful": {
            "marketing_spend": ["1400000", "1600000", "1800000",
                                "1800000", "1800000", "1800000"],
            "asp": ramp_series(1840, 1.03),
            "consumable_per_unit": ["92", "95", "98", "101", "104", "107"],
            "hires_per_quarter": ["3", "3", "2", "2", "2", "2"],
        },
        "notes": "Hardware: thin device margins + consumables attach. Naive "
                 "cuts price to chase volume (margin floor breach) while "
                 "quadrupling demand spend (burn breach).",
    }


def build_sensorgrid():
    """IoT hybrid (funnel topology): device sales + per-device subscription."""
    nodes = [
        leaf("marketing_spend", "Demand-gen spend", "usd/quarter", 500000),
        leaf("cost_per_lead", "Cost per qualified lead", "usd", 110),
        leaf("close_rate", "Lead -> unit close rate", "ratio", "0.22"),
        leaf("asp", "Device ASP", "usd", 950),
        leaf("unit_cost", "Device unit cost", "usd", 600),
        leaf("sub_price", "Subscription per active device", "usd/quarter", 75),
        leaf("cloud_cost_per_device", "Cloud cost per active device",
             "usd/quarter", 11),
        leaf("fleet_attrition", "Fleet attrition", "ratio", "0.03"),
        leaf("capex", "Production line capex", "usd/quarter",
             ["150000", "150000", "150000", "600000", "150000", "150000"]),
        leaf("fixed_opex", "Fixed OpEx", "usd/quarter", 240000),
        comp("leads", "Qualified leads", "leads/quarter",
             DIV(R("marketing_spend"), R("cost_per_lead"))),
        comp("units_sold", "Units sold", "units/quarter",
             MUL(R("leads"), R("close_rate"))),
        roll("fleet", "Active device fleet", "units", 9000,
             ADD(MUL(PRIOR, SUB(C(1), R("fleet_attrition"))), R("units_sold"))),
        comp("device_revenue", "Device revenue", "usd/quarter",
             MUL(R("units_sold"), R("asp"))),
        comp("sub_revenue", "Subscription revenue", "usd/quarter",
             MUL(R("fleet"), R("sub_price"))),
        comp("revenue", "Revenue", "usd/quarter",
             ADD(R("device_revenue"), R("sub_revenue"))),
        comp("device_cogs", "Device COGS", "usd/quarter",
             MUL(R("units_sold"), R("unit_cost"))),
        comp("cloud_cogs", "Cloud COGS", "usd/quarter",
             MUL(R("fleet"), R("cloud_cost_per_device"))),
        comp("cogs", "Cost of revenue", "usd/quarter",
             ADD(R("device_cogs"), R("cloud_cogs"))),
    ]
    staff_block(nodes, headcount_initial=55, salary=29000, base_hires=2)
    cash_block(nodes, initial_cash=22000000,
               opex_terms=["payroll_cost", "marketing_spend", "fixed_opex"],
               capex_term="capex")
    topology = {
        "kind": "funnel",
        "traffic_driver": "leads", "conversion_driver": "close_rate",
        "acv_driver": "asp", "churn_driver": "fleet_attrition",
        "new_customers_node": "units_sold",
        "ending_base_node": "fleet", "revenue_node": "revenue",
    }
    outputs = ([out("units_sold", "Units sold", "other"),
                out("fleet", "Active fleet", "other"),
                out("device_revenue", "Device revenue", "revenue"),
                out("sub_revenue", "Subscription revenue", "revenue"),
                out("marketing_spend", "Demand-gen spend", "operating_expense"),
                out("capex", "Capex", "capex")]
               + STANDARD_OUTPUTS)
    model = model_shell(
        "sensorgrid-iot", "SensorGrid — IoT Hardware + SaaS", "sensorgrid",
        topology, nodes, outputs,
        cost_behavior={"cogs": "variable", "marketing_spend": "variable",
                       "payroll_cost": "variable", "fixed_opex": "fixed"})
    return {
        "category": "hardware", "model": model,
        "levers": ["marketing_spend", "hires_per_quarter", "asp", "sub_price",
                   "capex"],
        "naive": {
            "marketing_spend": flat(2000000),
            "hires_per_quarter": flat(8),
        },
        "careful": {
            "marketing_spend": ["800000", "900000", "1000000",
                                "1000000", "1000000", "1000000"],
            "sub_price": ["77", "80", "83", "86", "89", "92"],
            "asp": ramp_series(960, 1.02),
            "hires_per_quarter": ["3", "3", "2", "2", "2", "2"],
        },
        "notes": "Hybrid hardware: the fleet's recurring subscription compounds "
                 "from every unit sold (fleet roll-forward) — the coherence "
                 "chain naive plans under-exploit.",
    }


def build_meshverse():
    """Platform ecosystem (funnel topology): devs -> apps -> rev share."""
    nodes = [
        leaf("devrel_spend", "Developer-relations spend", "usd/quarter", 400000),
        leaf("cost_per_dev", "Acquisition cost per developer", "usd", 800),
        leaf("dev_viral_rate", "Organic dev growth (per existing dev)", "ratio",
             "0.02"),
        leaf("dev_churn", "Developer churn", "ratio", "0.05"),
        leaf("apps_per_dev", "Live apps per developer", "apps", "0.6"),
        leaf("gmv_per_app", "End-customer spend per app", "usd/quarter", 9000),
        leaf("rev_share", "Platform revenue share", "ratio", "0.15"),
        leaf("infra_rate", "Infra cost share of ecosystem GMV", "ratio", "0.02"),
        leaf("seat_price", "Enterprise seat price", "usd/quarter", 1500),
        leaf("seats_per_rep", "Seats sold per ramped rep", "seats/quarter", 25),
        leaf("fixed_opex", "Fixed OpEx", "usd/quarter", 300000),
    ]
    ramp_block(nodes, pool_id="ramped_reps",
               pool_label="Ramped enterprise reps", pool_initial=8)
    nodes += [
        comp("paid_new_devs", "Paid new developers", "devs/quarter",
             DIV(R("devrel_spend"), R("cost_per_dev"))),
        comp("organic_new_devs", "Organic new developers", "devs/quarter",
             MUL(PR("devs"), R("dev_viral_rate"))),
        roll("devs", "Active developers", "devs", 2400,
             ADD(MUL(PRIOR, SUB(C(1), R("dev_churn"))),
                 ADD(R("paid_new_devs"), R("organic_new_devs")))),
        comp("live_apps", "Live apps", "apps", MUL(R("devs"), R("apps_per_dev"))),
        comp("ecosystem_gmv", "Ecosystem GMV", "usd/quarter",
             MUL(R("live_apps"), R("gmv_per_app"))),
        comp("share_revenue", "Revenue-share income", "usd/quarter",
             MUL(R("ecosystem_gmv"), R("rev_share"))),
        comp("new_seats", "New enterprise seats", "seats/quarter",
             MUL(R("ramped_reps"), R("seats_per_rep"))),
        roll("enterprise_seats", "Enterprise seats", "seats", 600,
             ADD(MUL(PRIOR, C("0.97")), R("new_seats"))),
        comp("seat_revenue", "Enterprise seat revenue", "usd/quarter",
             MUL(R("enterprise_seats"), R("seat_price"))),
        comp("revenue", "Revenue", "usd/quarter",
             ADD(R("share_revenue"), R("seat_revenue"))),
        comp("cogs", "Cost of revenue (infra)", "usd/quarter",
             MUL(R("ecosystem_gmv"), R("infra_rate"))),
    ]
    staff_block(nodes, headcount_initial=85, salary=31000, base_hires=2)
    cash_block(nodes, initial_cash=22000000,
               opex_terms=["payroll_cost", "devrel_spend", "fixed_opex"])
    topology = {
        "kind": "funnel",
        "traffic_driver": "paid_new_devs", "conversion_driver": "apps_per_dev",
        "acv_driver": "gmv_per_app", "churn_driver": "dev_churn",
        "new_customers_node": "paid_new_devs",
        "ending_base_node": "devs", "revenue_node": "revenue",
    }
    outputs = ([out("devs", "Active developers", "other"),
                out("live_apps", "Live apps", "other"),
                out("ecosystem_gmv", "Ecosystem GMV", "other"),
                out("share_revenue", "Revenue share", "revenue"),
                out("seat_revenue", "Seat revenue", "revenue"),
                out("enterprise_seats", "Enterprise seats", "other"),
                out("devrel_spend", "DevRel spend", "operating_expense")]
               + STANDARD_OUTPUTS)
    model = model_shell(
        "meshverse-platform", "MeshVerse — Platform Ecosystem", "meshverse",
        topology, nodes, outputs,
        cost_behavior={"cogs": "variable", "devrel_spend": "variable",
                       "payroll_cost": "variable", "fixed_opex": "fixed"})
    return {
        "category": "ecosystem", "model": model,
        "levers": ["devrel_spend", "hires_per_quarter", "rev_share",
                   "seat_price"],
        "naive": {
            "devrel_spend": flat(1600000),
            "hires_per_quarter": flat(10),
        },
        "careful": {
            "devrel_spend": ["650000", "720000", "800000",
                             "800000", "800000", "800000"],
            "rev_share": ["0.155", "0.16", "0.165", "0.17", "0.175", "0.18"],
            "hires_per_quarter": ["3", "3", "2", "2", "2", "2"],
            "seat_price": ramp_series(1530, 1.03),
        },
        "notes": "Ecosystem play: paid + viral developer growth compounds into "
                 "app GMV; rev-share creep is high-margin but rate-limited. "
                 "Enterprise reps ramp 2 quarters before selling seats.",
    }


BUILDERS = [
    build_sprout, build_northwind, build_globalgrid,
    build_harborlist, build_gigbridge,
    build_atlas, build_brightclean,
    build_voltcore, build_sensorgrid,
    build_meshverse,
]


# ---------------------------------------------------------------------------
# Tier calibration
# ---------------------------------------------------------------------------

def dmax(xs):
    return max(xs)


def dmin(xs):
    return min(xs)


def floor_to(v, step):
    return (v / step).to_integral_value(rounding="ROUND_FLOOR") * step


def derive_tiers(company):
    """Evaluate base/naive/careful and derive goal + per-tier constraints."""
    model = company["model"]
    base = evaluate(model)
    naive = evaluate(model, company["naive"])
    careful = evaluate(model, company["careful"])

    runs = {"base": base, "naive": naive, "careful": careful}
    stats = {}
    for name, p in runs.items():
        rev = series(p, "revenue")
        stats[name] = {
            "multiple": rev[-1] / rev[0],
            "max_burn": dmax(series(p, "net_burn")),
            "min_cash": dmin(series(p, "cash")),
            "min_margin": dmin(series(p, "gross_margin_pct")),
        }

    def hires_max(bindings):
        hs = bindings.get("hires_per_quarter")
        if hs is None:
            hs = model["nodes"]["hires_per_quarter"]["default"]["values"]
        return max(Decimal(h) for h in hs)

    base_hires = hires_max({})
    naive_hires = hires_max(company["naive"])
    careful_hires = hires_max(company["careful"])

    # Goal: just under the weaker of the two reference plans, floored to 0.05.
    goal_mult = floor_to(
        min(stats["naive"]["multiple"], stats["careful"]["multiple"]),
        Decimal("0.05"))

    # Burn ceilings. Hostile sits just above the careful plan's own usage (so
    # hostile is feasible by construction); soft sits comfortably above naive;
    # hard is the geometric mean of the two.
    careful_anchor = max(stats["careful"]["max_burn"], stats["base"]["max_burn"])
    naive_anchor = max(stats["naive"]["max_burn"], careful_anchor)
    hostile_burn = careful_anchor * Decimal("1.03")
    soft_burn = naive_anchor * Decimal("1.25")
    hard_burn = (hostile_burn * soft_burn).sqrt()

    # Hire caps (integers).
    hostile_hires = max(careful_hires, base_hires)
    soft_hires = Decimal(math.ceil(max(naive_hires, hostile_hires) * Decimal("1.5")))
    hard_hires = Decimal(math.ceil((naive_hires + hostile_hires) / 2))

    # Margin floors. Hostile just below the worst margin base/careful hit.
    careful_margin_anchor = min(stats["careful"]["min_margin"],
                                stats["base"]["min_margin"])
    hostile_margin = careful_margin_anchor - Decimal("0.01")
    soft_margin = min(stats["naive"]["min_margin"],
                      careful_margin_anchor) - Decimal("0.05")
    hard_margin = (hostile_margin + soft_margin) / 2

    def q2(v):
        return str(v.quantize(Decimal("0.01")))

    def q4(v):
        return str(v.quantize(Decimal("0.0001")))

    def tier(burn, hires, margin):
        return {
            "constraints": [
                {"id": "burn_ceiling", "kind": "series_max",
                 "driver": "net_burn", "limit": q2(burn),
                 "description": "Quarterly net burn must not exceed the ceiling "
                                "in any quarter."},
                {"id": "cash_floor", "kind": "series_min", "driver": "cash",
                 "limit": "0",
                 "description": "Ending cash must be non-negative every quarter."},
                {"id": "margin_floor", "kind": "series_min",
                 "driver": "gross_margin_pct", "limit": q4(margin),
                 "description": "Gross margin must hold at or above the floor "
                                "every quarter."},
                {"id": "hire_cap", "kind": "binding_max",
                 "driver": "hires_per_quarter", "limit": str(int(hires)),
                 "description": "Net hires per quarter (a plan binding) must "
                                "not exceed the cap."},
            ]
        }

    tiers = {
        "soft": tier(soft_burn, soft_hires, soft_margin),
        "hard": tier(hard_burn, hard_hires, hard_margin),
        "hostile": tier(hostile_burn, hostile_hires, hostile_margin),
    }
    tiers["soft"]["description"] = "Naive plans usually work."
    tiers["hard"]["description"] = "Naive plans sometimes violate."
    tiers["hostile"]["description"] = ("Naive plans are guaranteed to fail; "
                                       "only careful reasoning succeeds.")

    goal = {
        "metric": "revenue",
        "baseline_period": 0,
        "target_period": HORIZON - 1,
        "target_multiple": str(goal_mult),
        "description": (f"Grow quarterly revenue to at least "
                        f"{goal_mult}x its period-0 value by period "
                        f"{HORIZON - 1} while satisfying the tier's "
                        f"constraints."),
    }
    return tiers, goal, stats, {
        "base_hires": base_hires, "naive_hires": naive_hires,
        "careful_hires": careful_hires,
    }


def check_constraints(model, periods, bindings, tier):
    """Return list of violated constraint ids for a run against a tier."""
    violated = []
    for c in tier["constraints"]:
        limit = Decimal(c["limit"])
        if c["kind"] == "series_max":
            if dmax(series(periods, c["driver"])) > limit:
                violated.append(c["id"])
        elif c["kind"] == "series_min":
            if dmin(series(periods, c["driver"])) < limit:
                violated.append(c["id"])
        elif c["kind"] == "binding_max":
            vals = bindings.get(c["driver"])
            if vals is None:
                vals = model["nodes"][c["driver"]]["default"]["values"]
            if max(Decimal(v) for v in vals) > limit:
                violated.append(c["id"])
    return violated


def calibrate(company):
    """Derive tiers and assert the eval plan's tier semantics."""
    model = company["model"]
    tiers, goal, stats, hires = derive_tiers(company)
    runs = {
        "base": (evaluate(model), {}),
        "naive": (evaluate(model, company["naive"]), company["naive"]),
        "careful": (evaluate(model, company["careful"]), company["careful"]),
    }
    target = Decimal(goal["target_multiple"])
    problems = []

    def mult(p):
        rev = series(p, "revenue")
        return rev[-1] / rev[0]

    if mult(runs["base"][0]) >= target:
        problems.append(
            f"base reaches goal ({mult(runs['base'][0]):.3f} >= {target})")
    for plan in ("naive", "careful"):
        if mult(runs[plan][0]) < target:
            problems.append(f"{plan} misses goal ({mult(runs[plan][0]):.3f})")

    expectations = {
        # run: {tier: must_pass}
        "base": {"soft": True, "hard": True, "hostile": True},
        "naive": {"soft": True, "hard": False, "hostile": False},
        "careful": {"soft": True, "hard": True, "hostile": True},
    }
    matrix = {}
    for run_name, (periods, bindings) in runs.items():
        for tier_name, tier in tiers.items():
            v = check_constraints(model, periods, bindings, tier)
            matrix[f"{run_name}/{tier_name}"] = v
            must_pass = expectations[run_name][tier_name]
            if must_pass and v:
                problems.append(f"{run_name} violates {tier_name}: {v}")
            if not must_pass and not v:
                problems.append(f"{run_name} unexpectedly passes {tier_name}")

    report = {
        "model_id": model["id"],
        "goal": goal,
        "stats": {k: {kk: str(vv) for kk, vv in v.items()}
                  for k, v in stats.items()},
        "hire_levels": {k: str(v) for k, v in hires.items()},
        "violation_matrix": matrix,
        "reference_plans": {"naive": company["naive"],
                            "careful": company["careful"]},
    }
    return tiers, goal, report, problems


# ---------------------------------------------------------------------------
# Emit (flat package layout)
# ---------------------------------------------------------------------------

def build_all():
    """Build and calibrate every company. Returns [(company, tiers, goal, report)]."""
    out = []
    failures = []
    with localcontext(paper_context()):
        for build in BUILDERS:
            company = build()
            tiers, goal, report, problems = calibrate(company)
            if problems:
                failures.append((company["model"]["id"], problems))
                continue
            out.append((company, tiers, goal, report))
    if failures:
        msg = "; ".join(f"{mid}: {probs}" for mid, probs in failures)
        raise RuntimeError(f"calibration failed: {msg}")
    return out


def emit(worlds_dir, calibration_path):
    """Write <worlds_dir>/<id>/{operating-model,scenarios,eval-tiers}.json and the calibration report."""
    worlds_dir = Path(worlds_dir)
    calibration_path = Path(calibration_path)
    calibration = []
    for company, tiers, goal, report in build_all():
        model = company["model"]
        mdir = worlds_dir / model["id"]
        mdir.mkdir(parents=True, exist_ok=True)
        (mdir / "operating-model.json").write_text(
            json.dumps(model, indent=2) + "\n", encoding="utf-8")
        (mdir / "scenarios.json").write_text(
            json.dumps([{"id": "base", "name": "Base", "bindings": {}}],
                       indent=2) + "\n", encoding="utf-8")
        (mdir / "eval-tiers.json").write_text(json.dumps({
            "model_id": model["id"],
            "registry_ref": f"local:{model['id']}",
            "category": company["category"],
            "notes": company["notes"],
            "goal": goal,
            "levers": company["levers"],
            "tiers": tiers,
        }, indent=2) + "\n", encoding="utf-8")
        calibration.append(report)
    calibration_path.parent.mkdir(parents=True, exist_ok=True)
    calibration_path.write_text(json.dumps(calibration, indent=2) + "\n", encoding="utf-8")
    return [r["model_id"] for r in calibration]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Regenerate the eval-1 worlds and calibration report.")
    ap.add_argument("--out", required=True,
                    help="output dir; writes <out>/worlds/<id>/... and <out>/calibration/report.json")
    args = ap.parse_args(argv)
    out = Path(args.out)
    ids = emit(out / "worlds", out / "calibration" / "report.json")
    print(f"wrote {len(ids)} companies under {out / 'worlds'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
