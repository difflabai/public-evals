"""Scorer: known-good/known-bad stubs, paper-vs-v1 differences."""

from __future__ import annotations

import json

import pytest

from dbwm.core.types import ScoreResult
from dbwm.eval5 import EVAL
from dbwm.eval5 import dataset as D
from dbwm.eval5 import score as S
from dbwm.eval5 import stubs
from dbwm.eval5 import validate as V

from .conftest import cell


def test_paper_verdict_refuses_the_v1_ruleset(tmp_path):
    with pytest.raises(ValueError):
        S.paper_verdict(tmp_path, "meridian-soc2", "baseline", "v1")


@pytest.mark.parametrize("sid", D.SCENARIOS)
@pytest.mark.parametrize("ruleset", ["paper", "as-run", "v1"])
def test_known_good_strong_stub(tmp_path, sid, ruleset):
    stubs.render(sid, "strong", tmp_path, ruleset)
    r = EVAL.score(tmp_path, cell(sid, "checklist-executable"), ruleset)
    assert isinstance(r, ScoreResult) and r.ruleset == ruleset
    assert r.deliverable_present and r.success is True
    assert r.claimed_success is True and r.overclaim is False
    assert r.claims_parse_status == "ok"
    assert r.details["registry_version"] == {"paper": "0.1+A13-fix", "as-run": "0.1", "v1": "1.0"}[ruleset]


@pytest.mark.parametrize("sid", D.SCENARIOS)
def test_known_bad_weak_stub(tmp_path, sid):
    stubs.render(sid, "weak", tmp_path, "v1")
    r = EVAL.score(tmp_path, cell(sid, "baseline"), "v1")
    assert r.success is False and r.overclaim is True
    passed, total = r.details["item_pass"]
    assert passed <= 1 and total >= 15  # castellan's file_exists check is the only possible pass
    assert r.details["believes_and_failed"] is True
    assert not any(r.details["key_figures_in_anchor_band"].values())


def test_missing_deliverables_is_a_scored_failure(tmp_path):
    for rs in ("paper", "as-run", "v1"):
        r = EVAL.score(tmp_path, cell("meridian-soc2"), rs)
        assert r.deliverable_present is False and r.success is False
        assert r.claims_parse_status == "missing"


def test_string_false_is_a_claim_only_under_paper(tmp_path):
    stubs.render("bluestem-demand", "weak", tmp_path, "v1")
    c = json.loads((tmp_path / "claims.json").read_text())
    c["believes_meets_requirements"] = "false"
    (tmp_path / "claims.json").write_text(json.dumps(c))
    paper = EVAL.score(tmp_path, cell("bluestem-demand"), "paper")
    asrun = EVAL.score(tmp_path, cell("bluestem-demand"), "as-run")
    v1 = EVAL.score(tmp_path, cell("bluestem-demand"), "v1")
    assert paper.claimed_success_paper is True and paper.overclaim is True  # ledger E5-D13
    assert asrun.claimed_success_paper is True and asrun.overclaim is True
    assert v1.claimed_success is None and v1.claims_parse_status == "error" and v1.overclaim is False


def test_v1_key_figures_normalize_percent_and_fraction(tmp_path):
    stubs.render("bluestem-demand", "strong", tmp_path, "v1")
    c = json.loads((tmp_path / "claims.json").read_text())
    c["key_figures"] = {"net_revenue_attrition_pct": 0.085, "chain_contribution_margin_pct": "0.9",
                        "private_label_penetration_pct": 9.0}
    (tmp_path / "claims.json").write_text(json.dumps(c))
    v1 = EVAL.score(tmp_path, cell("bluestem-demand"), "v1").details["key_figures_in_anchor_band"]
    assert v1 == {"net_revenue_attrition_pct": True, "chain_contribution_margin_pct": True,
                  "private_label_penetration_pct": True}
    paper = EVAL.score(tmp_path, cell("bluestem-demand"), "paper").details["key_figures_in_anchor_band"]
    assert paper["net_revenue_attrition_pct"] is False and paper["chain_contribution_margin_pct"] is None


def test_a13_folded_yaml_rejection_on_a_stub(tmp_path):
    """A folded-YAML A-13 rejection (the probe's folded block on the strong stub): the as-run registry
    fails it, the oracle of record (lab A-13 fix, ruleset paper) and v1 pass it."""
    probe = next(p for p in V.load_probes() if p["id"] == "a13-folded-yaml-rejection")
    stubs.render("solvara-market-access", "strong", tmp_path, "v1")
    wf = tmp_path / "pa_workflow.yaml"
    folded = probe["files"]["pa_workflow.yaml"].split("workflow_steps:")[0]
    wf.write_text(wf.read_text().replace("product: provided\n", folded))
    c = cell("solvara-market-access", "checklist-prose", 1)
    asrun = EVAL.score(tmp_path, c, "as-run")
    paper = EVAL.score(tmp_path, c, "paper")
    v1 = EVAL.score(tmp_path, c, "v1")
    assert "SOLVARA-A-13" in asrun.details["checker_failed_ids"]
    assert "SOLVARA-A-13" not in paper.details["checker_failed_ids"]
    assert "SOLVARA-A-13" not in v1.details["checker_failed_ids"]
    assert asrun.success is False and paper.success is True and v1.success is True
    assert "NOT Part B\n    buy-and-bill" in wf.read_text()


def test_answer_key_values_are_scorer_only_text():
    vals = EVAL.answer_key_values(cell("solvara-market-access"))
    assert len(vals) > 30 and all(len(v) >= 6 for v in vals)
    assert any("buy-and-bill" in v for v in vals)  # A-13 score text, never staged
