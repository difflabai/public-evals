"""Dataset v1.0: validator lints, golden probes, provenance pins, publication hygiene."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import pytest

from dbwm.core.types import CANARY_GUID, CANARY_STRING
from dbwm.eval5 import dataset as D
from dbwm.eval5 import engine as E
from dbwm.eval5 import validate as V

NAMED_V01_DEFECTS = ("SOLVARA-A-13", "MERIDIAN-A-04", "CASTELLAN-A-05", "MERIDIAN-A-05", "SOLVARA-C-01")


def test_validate_v1_clean_for_every_scenario():
    rep = V.validate_all("v1")
    for sid, r in rep["scenarios"].items():
        assert r["errors"] == [], (sid, r["errors"])
        assert r["warnings"] == [], (sid, r["warnings"])
    assert rep["ok"] and rep["canary_ok"]


def test_validate_v01_flags_the_known_defects():
    rep = V.validate_all("as-run")
    errors = " ".join(e for r in rep["scenarios"].values() for e in r["errors"])
    for cid in NAMED_V01_DEFECTS:
        assert cid in errors, cid
    assert not rep["ok"]


# The probes added with the v7 alignment: five adoptions the authors' repaired A-13 spec
# or the old v1.0 tokens miss, two legitimate pharmacy-benefit texts the old v1.0 tokens failed, and
# the negative control for the A-14 hyphen repair (v7 G.2 item 4).
V7_PROBES = ("a13-step-buy-and-billing", "a13-benefit-type-medical-benefit", "a13-benefit-design-part-b",
             "a13-step-jcode", "a13-actor-buy-and-bill", "a13-system-buy-bill",
             "a13-pharmacy-medical-exception-ok", "a13-pharmacy-buy-in-ok", "solvara-a14-no-pa-topic")


def test_v1_probes_all_correct_and_paper_misgrades_pinned():
    probes = V.load_probes()
    assert len(probes) == 57
    ids = [p["id"] for p in probes]
    assert len(set(ids)) == len(ids) and set(V7_PROBES) <= set(ids)
    assert all(set(p["expect"]) == {"paper", "as-run", "v1"} for p in probes)
    v1 = V.run_probes("v1")
    assert all(r["got"] == r["correct"] for r in v1), [r["id"] for r in v1 if r["got"] != r["correct"]]
    for rs in ("paper", "as-run"):
        res = V.run_probes(rs)
        assert all(r["got"] == r["expected"] for r in res), (rs, [r["id"] for r in res if r["got"] != r["expected"]])
    paper = V.run_probes("paper")
    wrong = sorted(r["id"] for r in paper if r["got"] != r["correct"])
    assert wrong == sorted(V.PAPER_KNOWN_MISGRADES) and len(wrong) == 35
    old = [r for r in paper if r["id"] not in V7_PROBES]
    assert len(old) == 48 and sum(1 for r in old if r["got"] != r["correct"]) == 30  # 30 of 48
    asrun = V.run_probes("as-run")
    assert sum(1 for r in asrun if r["id"] not in V7_PROBES and r["got"] != r["correct"]) == 32
    # the repair corrects exactly the two A-13 rejection probes the as-run registry failed
    fixed = {r["id"] for r in asrun if r["got"] != r["correct"]} - set(wrong)
    assert fixed == {"a13-brief-wording-rejection", "a13-folded-yaml-rejection"}
    for res, n in ((asrun, 17), (paper, 16)):
        map20 = [r for r in res if r["origin"] == "map-2026-10-01"]
        assert len(map20) == 20
        assert sum(1 for r in map20 if r["got"] != r["correct"]) == n  # the mapping audit's "17 of 20"; 16 fixed


def test_a14_negative_control_fails_everywhere():
    """v7 G.2 item 4: the A-14 repair is adopted with a negative control (a plan with no PA topic fails)."""
    for rs in ("paper", "as-run", "v1"):
        r = next(x for x in V.run_probes(rs) if x["id"] == "solvara-a14-no-pa-topic")
        assert r["correct"] is False and r["got"] is False, rs


def test_old_v1_a13_spec_misgraded_exactly_the_revised_probes():
    """the pre-revision v1.0 A-13 tokens misgrade exactly the probes the revision targets."""
    old_tokens = ["buy-and-bill", "buy and bill", "buy & bill", "j-code", "medical benefit"]
    old_benefit = ["medical", "buy"]
    reg = {c["id"]: c for c in D.registry("solvara-market-access", "v1")}
    old_auto = json.loads(json.dumps(reg["SOLVARA-A-13"]["auto"]))
    for leg in old_auto["of"][1:]:
        leg["tokens"] = list(old_benefit if leg["path"].startswith(("payer_segments", "product")) else old_tokens)
    wrong = []
    for p in V.load_probes():
        if p["check_id"] != "SOLVARA-A-13":
            continue
        with tempfile.TemporaryDirectory(prefix="dbwm_e5probe_") as td:
            for name, body in p["files"].items():
                (Path(td) / name).write_text(body)
            got, _m = E.Engine("v1").run_op(E.Submission(td), old_auto)
        if bool(got) is not p["correct"]:
            wrong.append(p["id"])
    assert sorted(wrong) == ["a13-benefit-design-part-b", "a13-pharmacy-buy-in-ok",
                             "a13-pharmacy-medical-exception-ok", "a13-step-buy-and-billing"]


def test_checklist_md_rendered_from_registry_v1():
    want_pc = {"solvara-market-access": 17, "meridian-soc2": 16, "castellan-software": 17, "bluestem-demand": 17}
    for sid in D.SCENARIOS:
        reg = D.registry(sid, "v1")
        text = D.checklist_text(sid)
        assert text == E.render_checklist_v1(reg) == E.render_checklist_v1(reg, pass_conditions=True)
        assert text.count(E.PASSES_WHEN) == want_pc[sid] == len(E.pass_conditions(reg))
        for pc in E.pass_conditions(reg):
            assert E.PASSES_WHEN + pc + "\n" in text
        # without pass conditions: the as-run layout of the same registry (the regime opt-out)
        off = E.render_checklist_v1(reg, pass_conditions=False)
        assert off == E.render_checklist_as_run(reg) and E.PASSES_WHEN.strip() not in off
        assert [ln for ln in text.splitlines() if not ln.startswith(E.PASSES_WHEN)] == off.splitlines()
        ids = [c["check_id"] for c in D.raw_checks(sid, "v1")]
        assert ids == [c["check_id"] for c in D.raw_checks(sid, "paper")] == D.checklist_ids(sid)
        assert ids == [c["check_id"] for c in D.raw_checks(sid, "as-run")]


@pytest.mark.parametrize("pass_conditions", [True, False])
def test_checklist_renderings_are_free_of_grading_vocabulary(pass_conditions):
    for sid in D.SCENARIOS:
        low = E.render_checklist_v1(D.registry(sid, "v1"), pass_conditions=pass_conditions).lower()
        assert not [tok for tok in V.FORBIDDEN if tok in low], sid


def test_disclosure_counts_both_ways():
    """validate_all info: matcher literals absent from the checklist text, description only vs with PASSES WHEN
    (the repaired registry 263 -> 169 and 14 -> 18 fully disclosed checks; v1.0 304 -> 201, 13 -> 15)."""
    want = {"v1": (304, 201, 13, 15), "paper": (263, 169, 14, 18)}
    for rs, (a, b, c, d) in want.items():
        rep = V.validate_all(rs)
        info = [r["info"] for r in rep["scenarios"].values()]
        got = tuple(sum(i[k] for i in info) for k in (
            "undisclosed_matcher_literals", "undisclosed_matcher_literals_with_pass_conditions",
            "fully_disclosed_checks", "fully_disclosed_checks_with_pass_conditions"))
        assert got == (a, b, c, d), rs
        assert sum(len(i["undisclosed_by_check"]) for i in info) == 63


def test_v01_auto_specs_are_the_as_run_specs():
    """Hygiene edits touched free text only: every v0.1 auto spec hashes to the as-run value."""
    pins = D.as_run_pins()["v0_1_auto_sha256"]
    seen = 0
    for sid in D.SCENARIOS:
        for c in D.raw_checks(sid, "as-run"):
            h = hashlib.sha256(json.dumps(c.get("auto"), sort_keys=True).encode()).hexdigest()
            assert h == pins[c["check_id"]], c["check_id"]
            seen += 1
    assert seen == 67


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def test_paper_registry_is_v01_plus_the_lab_a13_fix():
    """Ruleset paper (the oracle of record) = registry v0.1 with the authors' 2026-10-03 SOLVARA-A-13 line;
    every other check and every other scenario is v0.1 verbatim."""
    prov = D.as_run_pins()
    v01_pins, fixed_pins = prov["v0_1_auto_sha256"], prov["v0_1_fixed_auto_sha256"]
    assert set(fixed_pins) == {"SOLVARA-A-13"}
    assert fixed_pins["SOLVARA-A-13"] != v01_pins["SOLVARA-A-13"]
    for sid in D.SCENARIOS:
        has_fixed = (D.private_scenario_dir(sid) / "registry_v0.1-fixed.jsonl").exists()
        assert has_fixed == (sid == "solvara-market-access")
        assert D.registry_path(sid, "paper").name == ("registry_v0.1-fixed.jsonl" if has_fixed else "registry_v0.1.jsonl")
        for c, a in zip(D.raw_checks(sid, "paper"), D.raw_checks(sid, "as-run")):
            assert c["check_id"] == a["check_id"]
            if c["check_id"] == "SOLVARA-A-13":
                assert _sha(c["auto"]) == fixed_pins["SOLVARA-A-13"]
                assert c["score_0_criteria"] == a["score_0_criteria"]
                assert c["score_1_criteria"] != a["score_1_criteria"]
                for k in ("files_needed", "specific_file_locations", "title", "check_description"):
                    assert c.get(k) == a.get(k), k
            else:
                assert c == a, c["check_id"]
    assert D.REGISTRY_VERSION == {"paper": "0.1+A13-fix", "as-run": "0.1", "v1": "1.0"}
    with pytest.raises(ValueError):
        D.registry_path("solvara-market-access", "v2")


def test_fixed_line_is_all_of_three_absences():
    a13 = D.raw_checks("solvara-market-access", "paper")[12]
    legs = a13["auto"]["of"]
    assert a13["check_id"] == "SOLVARA-A-13"
    assert a13["auto"]["op"] == "all_of" and [x["op"] for x in legs] == ["structured_text_absent"] * 3
    assert [x["path"] for x in legs] == ["workflow_steps[*].name", "product.benefit_type",
                                         "payer_segments[*].benefit_design"]


def test_registry_v1_changes_are_documented():
    prov = D.as_run_pins()["v1_changes"]
    for sid in D.SCENARIOS:
        p = {c["check_id"]: c for c in D.raw_checks(sid, "as-run")}
        for c in D.raw_checks(sid, "v1"):
            changed = (c.get("auto") != p[c["check_id"]].get("auto")
                       or c["score_1_criteria"] != p[c["check_id"]]["score_1_criteria"]
                       or c["check_description"] != p[c["check_id"]]["check_description"])
            if changed:
                assert c.get("v1_change") and c["check_id"] in prov, c["check_id"]


def test_answer_keys_unchanged_where_scoring_reads_them():
    """Paper scoring reads anchor bands/values and headline keys; renames must not touch them."""
    expect = {"solvara-market-access": {"us_addressable_patients": [3700, 4700], "annual_wac_usd": [145000, 155000]},
              "meridian-soc2": {"misrouted_patient_count": [210, 218], "unmanaged_byod_count": [8, 8]},
              "castellan-software": {"plscadd_seats_needed": [10, 13]},
              "bluestem-demand": {"net_revenue_attrition_pct": [8.0, 9.0]}}
    for sid, bands in expect.items():
        a = D.anchors(sid)
        for k, band in bands.items():
            assert a[k]["band"] == band
        assert D.headline_figures(sid) == D.blueprint(sid)["headline_figures"]


def test_canary_in_private_only():
    assert CANARY_STRING in D.private("CANARY.txt").read_text()
    for p in D.data().rglob("*"):
        if p.is_file():
            assert CANARY_GUID not in p.read_text(errors="replace"), p


# ---------------------------------------------------------------------------
# publication hygiene of the authored (agent-visible) dataset
# ---------------------------------------------------------------------------

def _original_entity_names() -> list[str]:
    """The names the dataset replaced, from the authors' PROVENANCE.json (substitutions[].old; null for the redacted
    IPs, which the documentation-range test below covers). They do not ship, so the file is named by an env var."""
    path = os.environ.get("DBWM_EVAL5_PROVENANCE")
    if not path:
        pytest.skip("needs the authors' PROVENANCE.json, whose names do not ship; set DBWM_EVAL5_PROVENANCE to its path")
    names = [s["old"].lower() for s in json.loads(Path(path).read_text())["substitutions"] if s["old"]]
    assert names, path
    return names


def _agent_visible_and_answer_keys():
    for p in sorted(D.data().rglob("*")):
        if p.is_file():
            yield p
    for p in sorted(D.private("scenarios").rglob("*")):
        if p.is_file():
            yield p


def test_no_real_entity_names_left():
    names = _original_entity_names()
    for p in _agent_visible_and_answer_keys():
        low = p.read_text(errors="replace").lower()
        for name in names:
            assert name not in low, (p, name)


def test_domains_are_example_and_ips_are_documentation_ranges():
    dom = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|io|org|net|co|ai|site|app|dev)\b", re.I)
    ip = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    ok_ip = re.compile(r"^(?:192\.0\.2|198\.51\.100|203\.0\.113)\.")
    for p in _agent_visible_and_answer_keys():
        t = p.read_text(errors="replace")
        assert not [m.group(0) for m in dom.finditer(t) if "huggingface.co" not in m.group(0)], p
        for m in ip.finditer(t):
            assert ok_ip.match(m.group(0)), (p, m.group(0))


def test_fictional_statement_in_every_overview_and_readme():
    for sid in D.SCENARIOS:
        assert "fictional" in D.overview(sid).lower()
    assert "fictional" in D.data("README.md").read_text().lower()


def test_tsc_crib_sheet_is_paraphrased_and_statutes_disclaimed():
    tsc = (D.scenario_dir("meridian-soc2") / "sources/shared/reference/soc2_tsc_quick_reference.md").read_text()
    # COSO-derived criterion sentences of the as-run sheet must not survive
    for phrase in ("demonstrates a commitment to integrity and ethical values",
                   "obtains or generates and uses relevant, quality information",
                   "selects and develops control activities that contribute to the mitigation"):
        assert phrase not in tsc
    for cid in ["CC1.1", "CC9.2", "A1.3", "C1.2"]:
        assert f"**{cid}**" in tsc
    st = (D.scenario_dir("solvara-market-access") / "sources/engagement/data_pulls/state_pa_turnaround_statutes.csv").read_text()
    assert "Ins. Code" not in st and "not legal guidance" in st
    assert "in most statute states above, standard_days = 7 (calendar) with expedited_hours = 72" in st


@pytest.mark.parametrize("sid", D.SCENARIOS)
def test_contradiction_quotes_and_file_plan_still_resolve(sid):
    errs, _warns, _info = V.validate_scenario(sid, "v1")
    assert not [e for e in errs if "contradiction" in e or "planned source" in e]
