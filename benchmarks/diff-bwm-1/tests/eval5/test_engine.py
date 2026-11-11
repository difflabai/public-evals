"""Engine goldens: the paper engine is the as-run checker; the v1 engine fixes are targeted."""

from __future__ import annotations

import json
import os

import pytest

from dbwm.eval5 import dataset as D
from dbwm.eval5 import engine as E


def test_paper_and_as_run_share_the_paper_engine():
    """Rulesets paper and as-run differ only in the registry they read (dataset.REGISTRY_FILE)."""
    assert E.Engine("paper").ops is E.Engine("as-run").ops is E.PAPER_OPS
    assert E.Engine("v1").ops is E.V1_OPS
    assert set(E.RULESETS) == {"paper", "as-run", "v1"} and set(E.PAPER_ENGINE_RULESETS) == {"paper", "as-run"}


@pytest.mark.parametrize("text,expected", [
    ("SSE-S3 bucket", []),
    ("CC2.1 and CC7.4", []),
    ("in Q3 we launch", []),
    ("FY2025 revenue", []),
    ("Phase 2 addresses criticals", []),
    ("Section 4.3 of the plan", []),
    ("pulled 2026-05-08", []),
    ("as of 2025-08 inventory", []),
    ("file summary_2026-05-30.md", []),
    ("gap G-02 and INC-2026-007", []),
    ("3 PHI buckets", [3.0]),
    ("benchmark 18-22%", [18.0, 22.0]),
    ("$4.2-6.9M uplift", [4.2, 6.9]),
    ("elasticity of -0.7", [0.7]),
    ("about 4,125 patients", [4125.0]),
    ("38TB per year", [38.0]),
])
def test_v1_number_tokenizer(text, expected):
    assert [x for x, _s, _e in E.v1_numbers(text)] == expected


def test_paper_tokenizer_defects_are_kept():
    # the paper regex reads S3 as 3 and a range end as a negative number
    sub_text = "uses SSE-S3; benchmark 18-22%"
    import re
    got = [m.group(0) for m in re.finditer(r"-?\d[\d,]*\.?\d*", sub_text)]
    assert got == ["3", "18", "-22"]


@pytest.mark.parametrize("text,expected", [
    ("$2.76M at risk", [2760000.0]),
    ("2.76 million at risk", [2760000.0]),
    ("$150K list price", [150000.0]),
    ("Phase 1 protects $2.76 million", [2760000.0]),
    ("$19.4 million over 3 years", [19400000.0, 3.0]),
])
def test_v1_amounts(text, expected):
    assert [x for x, _r, _s in E.v1_amounts(text)] == pytest.approx(expected)


@pytest.mark.parametrize("tok,text,expected", [
    ("IRO", "a tough payer environment", False),
    ("IRO", "escalate to an IRO", True),
    ("api", "rapid response", False),
    ("api", "Client portal and API", True),
    ("appeal", "a four-level appeals ladder", True),
    ("prior authorization", "prior-authorization design", True),
    ("specialty pharmac", "specialty-pharmacy network", True),
    ("hub", "hub services", True),
    ("hub", "github sync", False),
    ("Part D", "Part D coverage", True),
    ("Part D", "part data", False),
    ("buy & bill", "no buy & bill steps", True),
])
def test_v1_token_matching(tok, text, expected):
    assert E.token_in(tok, text) is expected


def test_v1_enum_is_one_directional_and_ignores_empty(tmp_path):
    (tmp_path / "x.json").write_text(json.dumps({"v": ["", "csv"]}))
    spec = {"op": "structured_enum", "file": "x.json", "path": "v[*]", "required_values": ["commercial", "a.csv"]}
    ok_paper, m_paper = E.Engine("paper").run_op(E.Submission(tmp_path), spec)
    ok_v1, m_v1 = E.Engine("v1").run_op(E.Submission(tmp_path), spec)
    assert ok_paper is True and all(m_paper["covered"].values())  # '' in every value (defect kept)
    assert ok_v1 is False and not any(m_v1["covered"].values())


def test_safe_submission_rejects_symlinks_and_outside_files(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "claims.json").write_text("{}")
    sb = tmp_path / "sb"
    sb.mkdir()
    os.symlink(outside / "claims.json", sb / "claims.json")
    sub = E.SafeSubmission(sb, allowed=("claims.json", "x.md"))
    assert sub.exists("claims.json") is False
    assert sub.text("claims.json") is None
    assert sub.rejected["claims.json"] == "symlink"
    assert sub.exists("../outside/claims.json") is False


def test_unknown_ruleset_rejected():
    with pytest.raises(ValueError):
        E.Engine("v2")


def test_figures_v1_redacts_grader_spec(tmp_path):
    (tmp_path / "market_access_plan.md").write_text("WAC: $150,000 per patient per year.\n")
    reg = [c for c in D.registry("solvara-market-access", "v1") if c["id"] in ("SOLVARA-A-15", "SOLVARA-A-08")]
    res = E.run_checks(reg, E.SafeSubmission(tmp_path), "v1", submission_label=".")
    txt = E.figures_render_v1(res)
    assert "measured:" in txt
    for spec_literal in ('"band"', '"context"', "WAC|list price", "3700", "4700"):
        assert spec_literal not in txt
    paper_txt = E.paper_cli_render(res, "figures")
    assert '"band"' in paper_txt  # the paper tool printed the grader's bands
