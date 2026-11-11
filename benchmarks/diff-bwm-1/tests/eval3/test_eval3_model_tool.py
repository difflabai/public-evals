"""The model DSL and the model_eval / model_test renderings vs the paper's model_tool.py.

Goldens: fixtures/model_tool_golden.json holds the PAPER tool's stdout on the fixture models of
fixtures/model_tool_models.json (every error envelope and the two paper CLI crashes), each in both modes:
50 cases, captured by running the paper's model_tool.py. ``render(..., "paper")`` must reproduce them
byte for byte; ``render(..., "v1")`` must differ only as documented (V2-V4, V6).
"""

from __future__ import annotations

import copy
import json
import threading
from decimal import Decimal
from pathlib import Path

import pytest

from dbwm.core import paths
from dbwm.eval2 import load_brief, load_reference, load_world
from dbwm.eval3 import model_dsl
from dbwm.eval3.fixtures import FAITHFUL_FORMULAS, faithful_model, plan_from_allocation
from dbwm.eval3.oracle import LIMITS, action_space_reasons, render, unknown_reference_problem
from dbwm.eval3.task import load_task

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_MODELS = json.loads((FIXTURES / "model_tool_models.json").read_text(encoding="utf-8"))
CASES = json.loads((FIXTURES / "model_tool_golden.json").read_text(encoding="utf-8"))["cases"]
WORLDS = ("orion-holdings", "vega-group", "lyra-partners")


def _spec(case):
    return case["model"]


def _op(case):
    return "model_eval" if case["mode"] == "eval" else "model_test"


def _flat(world="orion-holdings", mkt="60000", hires="1"):
    b = load_brief(world)
    uids = [u["id"] for u in b["units"]]
    return faithful_model(b, {u: [mkt] * 6 for u in uids}, {u: [hires] * 6 for u in uids})


def test_golden_file_covers_every_class():
    names = {c["name"] for c in CASES}
    assert len(CASES) == 50
    modes = ("eval", "test")
    assert sorted((c["name"], c["mode"]) for c in CASES) == sorted((n, m) for n in FIXTURE_MODELS for m in modes)
    assert {c["name"]: {"world": c["world"], "model": c["model"]} for c in CASES} == FIXTURE_MODELS
    assert {
        "err-syntax",
        "err-cycle",
        "err-zero-division",
        "err-unknown-ref",
        "crash-unit-without-plan",
        "crash-horizon-7",
        "layoff-orion",
        "missing-unit-orion",
        "err-model-is-list",
    } <= names
    crashes = [c for c in CASES if c["returncode"] and not c["stdout"].strip()]
    assert sorted((c["name"], c["mode"]) for c in crashes) == [
        ("crash-horizon-7", "test"),
        ("crash-unit-without-plan", "test"),
    ]


@pytest.mark.parametrize("case", CASES, ids=[f"{c['name']}/{c['mode']}" for c in CASES])
def test_paper_rendering_equals_paper_tool_stdout(case):
    task = load_task(case["world"])
    if case["returncode"] and not case["stdout"].strip():
        with pytest.raises((KeyError, IndexError)):  # the paper CLI's two tracebacks
            render(_spec(case), _op(case), "paper", task=task)
        return
    out = render(_spec(case), _op(case), "paper", task=task)
    assert json.dumps(out, indent=2) + "\n" == case["stdout"]
    assert (case["returncode"] == 0) == (out.get("ok") is True)


@pytest.mark.parametrize(
    "case", [c for c in CASES if c["mode"] == "eval"], ids=[c["name"] for c in CASES if c["mode"] == "eval"]
)
def test_v1_eval_equals_paper_except_documented(case):
    spec = _spec(case)
    v1 = render(spec, "model_eval", "v1", task=None)
    paper = render(spec, "model_eval", "paper", task=None)
    if case["name"] == "err-unknown-ref":  # V6: the true reason instead of "cycle among [...]"
        assert paper["error"].startswith("evaluation failed: cycle among")
        assert v1 == {
            "ok": False,
            "error": "model error: formula 'leads' references unknown name(s) 'cpl' in unit "
            "alpha-cloud (not a parameter, a plan lever or a formula)",
        }
    else:
        assert v1 == paper


@pytest.mark.parametrize(
    "case", [c for c in CASES if c["mode"] == "test"], ids=[c["name"] for c in CASES if c["mode"] == "test"]
)
def test_v1_test_is_paper_plus_action_space(case):
    spec = _spec(case)
    task = load_task(case["world"])
    v1 = render(spec, "model_test", "v1", task=task)
    if case["name"] in ("crash-unit-without-plan", "crash-horizon-7"):  # V4
        assert v1["ok"] is False and v1["error"].startswith("test failed: ")
        return
    paper = render(spec, "model_test", "paper", task=task)
    if paper.get("ok") is not True:
        if case["name"] == "err-unknown-ref":
            assert v1["error"].startswith("model error: formula 'leads' references unknown")
        else:
            assert v1 == paper
        return
    extra = v1["constraints"][-1]
    assert extra["id"] == "action_space"
    stripped = copy.deepcopy(v1)
    stripped["constraints"] = stripped["constraints"][:-1]
    if not extra["ok"]:
        assert stripped["violations"][-1] == "action_space"
        stripped["violations"] = stripped["violations"][:-1]
    assert stripped == paper
    assert extra["ok"] == (not action_space_reasons(spec, task))


def test_action_space_reasons_name_the_rule():
    task = load_task("orion-holdings")
    spec = _flat()
    assert action_space_reasons(spec, task) == []
    m = copy.deepcopy(spec)
    m["units"]["beta-systems"]["plan"]["hires"][1] = "-1"
    m["units"]["alpha-cloud"]["plan"]["hires"][2] = "0.5"
    m["units"]["gamma-data"]["plan"]["marketing"][0] = -5
    assert action_space_reasons(m, task) == [
        "fractional_hires:hires:alpha-cloud:q2",
        "layoff:hires:beta-systems:q1",
        "negative:marketing:gamma-data:q0",
    ]
    m = copy.deepcopy(spec)
    del m["units"]["gamma-data"]
    m["units"]["zeta"] = copy.deepcopy(spec["units"]["alpha-cloud"])
    del m["units"]["alpha-cloud"]["plan"]["marketing"]
    assert action_space_reasons(m, task) == [
        "unknown_unit:zeta",
        "missing_lever:alpha-cloud:marketing",
        "missing_unit:gamma-data",
    ]


@pytest.mark.parametrize(
    "mutate,needle",
    [
        (lambda m: m.__setitem__("horizon", LIMITS["horizon"] + 1), "horizon 25 exceeds the limit"),
        (
            lambda m: m["formulas"].__setitem__("leads", "marketing / cost_per_lead" + " + 0" * 200),
            "characters; the limit",
        ),
        (
            lambda m: m["formulas"].update({f"x{i}": "1" for i in range(LIMITS["formulas"])}),
            "formulas exceed the limit",
        ),
        (lambda m: m["units"].update({f"u{i}": {} for i in range(LIMITS["units"])}), "units exceed the limit"),
        (
            lambda m: m["units"]["alpha-cloud"]["params"].update(
                {f"p{i}": 1 for i in range(LIMITS["params_per_unit"])}
            ),
            "params exceed the limit",
        ),
    ],
)
def test_v1_size_limits(mutate, needle):
    m = _flat()
    mutate(m)
    for op in ("model_eval", "model_test"):
        out = render(m, op, "v1", task=load_task("orion-holdings"))
        assert (
            out["ok"] is False and out["error"].startswith("model error: model too large: ") and needle in out["error"]
        )


def test_unknown_reference_ignores_prior_arguments_and_reports_missing_names():
    m = _flat()
    assert unknown_reference_problem(m) is None  # prior(customers, customers_init): neither is a "reference"
    m["formulas"]["revenue"] = "customers * price"
    assert "unknown name(s) 'price'" in unknown_reference_problem(m)
    m = _flat()
    m["formulas"]["leads"] = "marketing / / cost_per_lead"
    assert unknown_reference_problem(m) is None  # syntax errors are left to the paper's message


@pytest.mark.parametrize("world", WORLDS)
def test_faithful_model_reproduces_the_true_world(world):
    """model_tool_selftest.py's check, made a failure (not a skip) when data is missing (E23-D18)."""
    b, w = load_brief(world), load_world(world)
    g, h = plan_from_allocation(load_reference(world)["careful_optimum"]["allocation"])
    ev = model_dsl.evaluate_model(faithful_model(b, g, h))
    true = w.evaluate({"growth_capital": g, "eng_headcount": h})["parent"]
    for d in ("revenue", "cash", "gross_profit", "headcount"):
        for t in range(w.horizon):
            assert abs(ev.parent[d][t] - true[d][t]) <= Decimal("1e-9") * abs(true[d][t])


def test_faithful_formulas_are_the_paper_selftest_formulas():
    src = (paths.PKG_ROOT / "dbwm" / "eval3" / "fixtures.py").read_text()
    assert len(FAITHFUL_FORMULAS) == 11 and "model_tool_selftest.py:18-30" in src


def test_decimal_context_is_thread_independent():
    """The paper set prec 50 globally at import; worker threads start at prec 28. The port must not care."""
    spec = _flat("vega-group", mkt="77777.77", hires="2")
    task = load_task("vega-group")
    main = (render(spec, "model_eval", "paper"), render(spec, "model_test", "paper", task=task))
    got = []

    def worker():
        got.append((render(spec, "model_eval", "paper"), render(spec, "model_test", "paper", task=task)))

    th = threading.Thread(target=worker)
    th.start()
    th.join()
    assert got == [main]
    assert len(main[0]["trajectory"]["gross_margin_pct"][0]) > 40  # 50-digit context, not 28


# sha256[:16] of ast.dump of each function body (docstring excluded), computed from the paper's
# bwm_coupled/model_tool.py (sha256 cf41620751d2...ae). _load_model is load_model here; _emit_eval
# differs only by naming its note text (model_dsl.EVAL_NOTE), which the goldens cover.
PAPER_BODY_HASHES = {
    "_const_value": "a05b6e67407d7e06",
    "_compile": "9da90964693fc087",
    "compile_formula": "5c3a6ecf0405c422",
    "build_unit_model": "c9697e4e7de395c5",
    "load_model": "0e6bac0a6be14bfb",
    "rollup": "0e24aabc4be4b883",
    "_ser": "226d0b5b427ccffb",
    "run_checks": "e4871c1d8d89b848",
}


def test_dsl_function_bodies_are_the_paper_tool_verbatim():
    import ast
    import hashlib

    tree = ast.parse(Path(model_dsl.__file__).read_text())
    got = {}
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in PAPER_BODY_HASHES:
            body = n.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(getattr(body[0], "value", None), ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            got[n.name] = hashlib.sha256("".join(ast.dump(s) for s in body).encode()).hexdigest()[:16]
    assert got == PAPER_BODY_HASHES
