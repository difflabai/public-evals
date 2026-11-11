"""D2: offline signals, Kendall tau, the judge prompt (v1 paper / v2 corrected), and the judge
pipeline through dbwm.core.judge (mock judge and the fake claude; no LLM is called)."""

from __future__ import annotations

import ast
import contextlib
import io
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

from dbwm.core import stats as core_stats
from dbwm.core.judge import ERR_INFRA, ERR_ISOLATION, ClaudeCliJudge, JudgeResult, MockJudge
from dbwm.eval6 import d2_judge as J
from dbwm.eval6 import d2_report
from dbwm.eval6 import d2_signals as ds

from .conftest import PKG_ROOT

FAKE_CLAUDE = PKG_ROOT / "tests" / "core" / "fake_claude" / "claude"


@pytest.fixture(scope="module")
def offline():
    """The offline signals report, regenerated (about 6 s)."""
    return json.loads(ds.report_json(ds.run(16, None, verbose=False)))


@pytest.fixture(scope="module")
def offline_path(offline, tmp_path_factory):
    """That report as the file --offline-report takes."""
    p = tmp_path_factory.mktemp("d2") / "d2-offline-signals.json"
    p.write_text(ds.report_json(offline))
    return p


@pytest.fixture(scope="module")
def states():
    return [ds.build_state(1, 54), ds.build_state(1, 72)]


def test_d2_signals_selftest():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ds.selftest()
    assert "selftest OK" in buf.getvalue()


def test_kendall_tau_b_matches_core_and_scipy():
    rng = random.Random(11)
    cases = []
    for _ in range(300):
        n = rng.randint(2, 8)
        cases.append(([rng.choice([0, 1, 2, rng.random()]) for _ in range(n)],
                      [rng.choice([0, 1, rng.random()]) for _ in range(n)]))
    for x, y in cases:
        a, b = ds.kendall_tau_b(x, y), core_stats.kendall_tau_b(x, y)
        assert (a != a and b != b) or abs(a - b) <= 1e-12
    try:
        from scipy.stats import kendalltau
    except ImportError as e:
        pytest.skip(f"scipy.stats not importable (optional): {str(e).splitlines()[0][:100]}")
    for x, y in cases:
        a, b = ds.kendall_tau_b(x, y), float(kendalltau(x, y)[0])
        assert (a != a and b != b) or a == b


def test_state_sets():
    assert ds.STATE_SETS == ("paper", "certified")
    cert = ds.states_for("certified", vstar_seeds=4)
    assert [s["world_seed"] for s in cert] == list(ds.CERTIFIED_SEEDS) and {s["cutoff"] for s in cert} == {72}
    assert 8 not in ds.CERTIFIED_SEEDS
    with pytest.raises(ValueError):
        ds.states_for("all")


def test_offline_report_render_is_neutral(offline):
    """v1 readout: where each interval lies and what it computes; no mechanism read into it."""
    txt = d2_report.render(offline)
    assert "LEDGER_MECHANICAL" in txt and "no mechanism inferred" in txt
    assert "ledger beats" not in txt and "YES" not in txt and "private-nowcast content" not in txt
    assert "LEDGER_MECHANICAL_minus_MECHANICAL_NO_LEDGER: +0.400" in txt and "(CI excludes 0)" in txt
    assert "ledger at lambda = 1.0: +0.967; at lambda = 0 (VHAT_ONLY): +0.975" in txt
    assert ds.LAMBDA_CALIBRATION_NOTE in txt                         # the calibration note
    assert "not in this report" in txt                               # the paper-format file has no lambda_zero block


def test_offline_report_lambda_zero_block_side_by_side(offline):
    """New-run reports print lambda = 1 and lambda = 0 side by side (projection sort for the no-ledger control)."""
    rep = dict(offline)
    rep["lambda_zero"] = {"lambda_used": 1.0,
                          "LEDGER_MECHANICAL": {"lambda_used": offline["aggregate"]["LEDGER_MECHANICAL"],
                                                "lambda_0": offline["aggregate"]["VHAT_ONLY"]},
                          "MECHANICAL_NO_LEDGER": {"lambda_used": offline["aggregate"]["MECHANICAL_NO_LEDGER"],
                                                   "lambda_0": {"mean_tau": 0.9333, "tau_ci95": [0.883, 0.983]}},
                          "note": ds.LAMBDA_CALIBRATION_NOTE, "per_state_projection_sort_tau": [0.9] * 16}
    txt = d2_report.render(rep)
    assert "no-ledger at lambda = 1.0: +0.567; at lambda = 0 (sort of the handed projection): +0.933" in txt
    assert "penalty weight side by side" in txt
    proj = J.projection_signals(rep)["projection"]
    assert len(proj) == 16 and set(proj.values()) == {0.9}
    assert J.projection_signals(offline) is None


# -- prompt ------------------------------------------------------------------------------------

def test_prompt_v1_is_the_paper_text_and_v2_only_fixes_the_crude_description(states):
    st = states[1]
    v1, v2 = J.build_judge_prompt(st, "v1"), J.build_judge_prompt(st, "v2")
    assert "it ignores the confounded price effect" in v1           # the E6-12 misstatement, kept in v1
    assert "ignores the confounded price effect" not in v2
    assert "starts every\nsegment at its long-run average demand" in v2
    l1, l2 = v1.splitlines(), v2.splitlines()
    same_head = next(i for i, (a, b) in enumerate(zip(l1, l2)) if a != b)
    assert l1[:same_head] == l2[:same_head] and l1[-3:] == l2[-3:]
    names = ast.literal_eval(v1.rsplit("Candidate names: ", 1)[1].strip())
    assert names == list(st["candidates"])
    with pytest.raises(ValueError):
        J.build_judge_prompt(st, "v3")


def test_prompt_carries_no_private_nowcast_or_vstar(states, offline):
    st = states[0]
    p = J.build_judge_prompt(st, "v2")
    vstar = next(s for s in offline["per_state"] if (s["world_seed"], s["cutoff"]) == (1, 54))["vstar"]
    for v in vstar.values():
        assert f"{v:.0f}" not in p
    assert "nowcast=" not in p and f"{st['latent_state'][0]:.3f}" not in p


# -- judge pipeline --------------------------------------------------------------------------------

def _names(user):
    return ast.literal_eval(user.rsplit("Candidate names: ", 1)[1].strip())


def test_mock_judge_run_and_aggregate(tmp_path, states, offline):
    def respond(system, user, schema, model):
        assert system == "" and model == "claude-test-judge" and schema == J.JUDGE_SCHEMA
        return {"ranking": sorted(_names(user)), "reasoning": "alphabetical"}
    adapter = MockJudge(respond, out_dir=tmp_path / "raw")
    rows = J.run_judge(states, adapter, "claude-test-judge", tmp_path / "out", reps=2, version="v2", log=None)
    assert len(rows) == 4 and len(list((tmp_path / "out").glob("s*_c*_r*.json"))) == 4
    r = rows[0]
    for k in ("world_seed", "cutoff", "rep", "judge_tau", "judge_ranking", "top1_pick", "top1_regret", "reasoning",
              "cost_usd", "turns", "capped", "prompt_version", "prompt_sha256", "judge_adapter", "tool_use_count"):
        assert k in r
    assert r["prompt_version"] == "v2" and r["judge_adapter"] == "mock" and r["tool_use_count"] == 0
    vstar = ds.vstar_values(states[0])
    assert r["judge_tau"] == J.tau_from_ranking(sorted(vstar), vstar, list(states[0]["candidates"]))
    again = J.run_judge(states, MockJudge(lambda *a: None), "claude-test-judge", tmp_path / "out", reps=2,
                        version="v2", log=None)               # resumes: nothing re-judged
    assert [x["judge_tau"] for x in again] == [x["judge_tau"] for x in rows]
    agg = J.aggregate_per_state(J.load_sessions(tmp_path / "out"), offline)
    assert agg["n_states"] == 2 and agg["n_sessions"] == 4
    for k in ("judge_minus_ledger", "judge_minus_no_ledger", "judge_minus_myopic", "judge_minus_vhat_only"):
        assert len(agg[k]) == 3
    assert agg["ledger_tau"][0] == offline["aggregate"]["LEDGER_MECHANICAL"]["mean_tau"]
    s = J.aggregate_sessions(J.load_sessions(tmp_path / "out"), offline)
    assert s["n_states"] == 4 and s["judge_minus_ledger_tau"]["n"] == 4


def test_recompute_sessions_from_rankings(tmp_path, states, offline):
    """Each usable session's tau, top-1 pick and regret are re-derived from its stored ranking and the report's
    V*, not copied from the stored fields; sessions without a ranking are skipped."""
    def respond(system, user, schema, model):
        return {"ranking": sorted(_names(user), reverse=True), "reasoning": "reverse alphabetical"}
    rows = J.run_judge(states, MockJudge(respond, out_dir=tmp_path / "raw"), "claude-test-judge", tmp_path / "out",
                       reps=2, version="v2", log=None)
    rows = rows + [{"world_seed": 1, "cutoff": 54, "rep": 9, "judge_error": True}]
    rec = J.recompute_sessions(rows, offline)
    assert len(rec) == len(J.usable(rows)) == 4
    for a, b in zip(rec, J.usable(rows)):
        assert (a["judge_tau"], a["top1_pick"], a["top1_regret"]) == (b["judge_tau"], b["top1_pick"], b["top1_regret"])
    stale = dict(rows[0], judge_tau=0.123, top1_regret=-1.0)
    again = J.recompute_sessions([stale], offline)[0]
    assert (again["judge_tau"], again["top1_regret"]) == (rows[0]["judge_tau"], rows[0]["top1_regret"])


def test_judge_errors_are_recorded_and_never_scored(tmp_path, states, offline):
    seq = [{"ranking": ["reference"], "reasoning": "incomplete"},                  # not a permutation
           JudgeResult(parsed=None, raw_path=None, tool_use_count=1, cost_usd=0.01, turns=2,
                       error="tool_use in judge stream", error_class=ERR_ISOLATION, adapter="mock"),
           None]                                                                  # infra: no fixture
    rows = J.run_judge(states[:1], MockJudge(seq), "m", tmp_path / "out", reps=3, version="v1", log=None)
    assert [r.get("error_class") for r in rows] == ["parse", ERR_ISOLATION, ERR_INFRA]
    assert all(r["judge_error"] and "judge_tau" not in r for r in rows)
    assert J.usable(rows) == [] and J.aggregate_sessions(rows, offline) is None
    assert J.aggregate_per_state(rows, offline) is None


def _fake_judge(tmp_path, script):
    if not FAKE_CLAUDE.exists():
        pytest.fail("tests/core/fake_claude/claude missing (core-runtime test double)")
    tmp_path.mkdir(parents=True, exist_ok=True)
    sp = tmp_path / "script.json"
    sp.write_text(json.dumps(script))
    (tmp_path / "jtmp").mkdir(exist_ok=True)
    return ClaudeCliJudge(tmp_path / "judge_out", claude_bin=str(FAKE_CLAUDE), tmp_root=tmp_path / "jtmp",
                          extra_env={"DBWM_FAKE_LOG": str(tmp_path / "log.jsonl"), "DBWM_FAKE_SCRIPT": str(sp)})


def test_fake_claude_judge_valid_and_isolation_breach(tmp_path, states):
    st = states[0]
    ranking = list(st["candidates"])[::-1]
    text = "Reasoned. " + json.dumps({"ranking": ranking, "reasoning": "reverse"})
    ok = J.judge_state(st, _fake_judge(tmp_path / "a", {"cost": 0.003, "actions": [{"op": "result", "result": text}]}),
                       "claude-sonnet-4-6", 0, "v2")
    assert ok.get("judge_error") is None and ok["judge_ranking"] == ranking and ok["tool_use_count"] == 0
    log = json.loads((tmp_path / "a" / "log.jsonl").read_text().splitlines()[-1])
    argv = log["argv"]
    assert argv[argv.index("--tools") + 1] == "" and "--restricted" in argv
    assert log["stdin"] == J.build_judge_prompt(st, "v2")
    assert Path(log["cwd"]).name.startswith("dbwm_judge_")
    bad = J.judge_state(st, _fake_judge(tmp_path / "b", {"actions": [{"op": "bash", "command": "ls"},
                                                                     {"op": "result", "result": text}]}),
                        "claude-sonnet-4-6", 0, "v2")
    assert bad["judge_error"] is True and bad["error_class"] == ERR_ISOLATION


def test_verdict_v1_is_neutral():
    assert J.verdict_v1(-0.5, -0.6, -0.4) == "judge - ledger -0.500 CI [-0.600, -0.400] below 0; no mechanism inferred"
    assert J.verdict_v1(0.1, -0.1, 0.3).endswith("spanning 0; no mechanism inferred")
    assert "above 0" in J.verdict_v1(0.2, 0.1, 0.3)


def _synthetic_offline(ledger: dict, no_ledger: dict) -> dict:
    """An offline report reduced to what the aggregators read: per-state signal taus."""
    other = {"MYOPIC_ACTUALS": {"tau": 0.0}, "VHAT_ONLY": {"tau": 0.0}}
    return {"per_state": [{"world_seed": k[0], "cutoff": k[1],
                           "signals": {"LEDGER_MECHANICAL": {"tau": ledger[k]},
                                       "MECHANICAL_NO_LEDGER": {"tau": no_ledger[k]}, **other}} for k in ledger],
            "aggregate": {"LEDGER_MECHANICAL": {"mean_tau": 0.5, "tau_ci95": [0.25, 0.75]}}}


def test_aggregate_per_state_averages_reps_and_uses_the_neutral_verdict():
    rows = [{"world_seed": w, "cutoff": c, "rep": rep, "judge_tau": tau, "top1_regret": 0.0, "cost_usd": cost}
            for w, c, rep, tau, cost in ((1, 54, 0, 0.5, 0.5), (1, 54, 1, 0.75, 0.25), (2, 54, 0, 0.25, None),
                                         (3, 72, 0, 0.0, None))]
    rows.append({"world_seed": 1, "cutoff": 54, "rep": 2, "judge_error": True, "error_class": "parse"})
    keys = [(1, 54), (2, 54), (3, 72)]
    off = _synthetic_offline(dict(zip(keys, (0.875, 0.5, 0.25))), dict(zip(keys, (0.125, 0.75, 0.0))))
    ps = J.aggregate_per_state(rows, off, B=200)
    assert ps["n_states"] == 3 and ps["n_sessions"] == 4 and ps["cost_usd"] == 0.75
    assert ps["judge_tau"][0] == pytest.approx((0.625 + 0.25 + 0.0) / 3)          # state means, then states
    assert ps["judge_minus_ledger"] == [-0.25, -0.25, -0.25] and ps["judge_minus_ledger_n"] == 3
    assert ps["judge_minus_no_ledger"][0] == pytest.approx((0.5 - 0.5 + 0.0) / 3)
    assert ps["ledger_tau"] == [0.5, 0.25, 0.75]
    assert ps["verdict"] == "judge - ledger -0.250 CI [-0.250, -0.250] below 0; no mechanism inferred"
    assert ps["verdict_rule"] == J.VERDICT_RULE_V1 and ps["bootstrap"] == {
        "B": 200, "seed": 12345, "unit": "state mean over reps; clusters = world_seed"}
    assert J.aggregate_sessions(rows, off)["verdict"] == J.VERDICT_LEDGER_WINS   # the paper string, that path only
    mixed = J.aggregate_per_state(rows, _synthetic_offline(dict(zip(keys, (0.125, 0.5, 0.25))),
                                                           dict(zip(keys, (0.0, 0.0, 0.0)))), B=200)
    assert mixed["verdict"].endswith("spanning 0; no mechanism inferred") and "LEDGER WINS" not in mixed["verdict"]
    assert J.aggregate_per_state(rows, {"per_state": [], "aggregate": {}}, B=200)["verdict"] is None


def test_projection_sort_on_two_states(offline):
    """The sorted handed projection and two ablations, per state, on the offline report's states 0-1."""
    sts = ds.enumerate_states(2, ds.DEFAULT_CUTOFFS, ds.WORLD_SEED_START)      # = states_for("paper", 16)[:2]
    assert [(s["world_seed"], s["cutoff"]) for s in sts] == [(r["world_seed"], r["cutoff"])
                                                             for r in offline["per_state"][:2]]
    vstars = [s["vstar"] for s in offline["per_state"][:2]]
    variants = (ds.PROJECTION_SORT, ds.PROJECTION_SORT_COUPLING_ZERO, ds.PROJECTION_SORT_SIGN_FLIPPED)
    proj = ds.projection_report(sts, vstars, bootstrap=200, variants=variants)
    assert list(proj["aggregate"]) == list(variants) and len(proj["per_state"]) == 2
    assert ds.projection_taus(proj) == {(r["world_seed"], r["cutoff"]): r["signals"][ds.PROJECTION_SORT]["tau"]
                                        for r in proj["per_state"]}
    # PROJECTION_SORT is the unpenalised no-ledger sim: lambda = 0 makes MECHANICAL_NO_LEDGER equal it
    sc = ds.projection_scores(sts[0])
    assert set(sc) == set(sts[0]["candidates"])
    with pytest.raises(ValueError):
        ds.projection_world(sts[0]["world"], "PROJECTION_SORT_BOGUS")


def test_cli_requires_offline_report_and_prints_prompts(tmp_path):
    r = subprocess.run([sys.executable, "-m", "dbwm", "eval6-d2", "judge", "run", "--model", "x", "--out",
                        str(tmp_path / "o")], cwd=PKG_ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 2 and "--offline-report" in r.stderr
    r = subprocess.run([sys.executable, "-m", "dbwm", "eval6-d2", "judge", "prompt", "--world-seed", "2",
                        "--cutoff", "54", "--prompt", "v1"], cwd=PKG_ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and r.stdout == J.build_judge_prompt(ds.build_state(2, 54), "v1")


def test_cli_summary_of_a_mock_judge(tmp_path, states, offline_path):
    rows = J.run_judge(states, MockJudge(lambda s, u, sc, m: {"ranking": sorted(_names(u)), "reasoning": "a"}),
                       "m", tmp_path / "out", reps=2, version="v2", log=None)
    r = subprocess.run([sys.executable, "-m", "dbwm", "eval6-d2", "judge", "summary", "--dir", str(tmp_path / "out"),
                        "--offline-report", str(offline_path)], cwd=PKG_ROOT, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0
    d = json.loads(r.stdout)
    assert (d["n_states"], d["n_sessions"], d["excluded_sessions"], d["mode"]) == (2, 4, 0, "per_state")
    assert d["judge_tau"][0] == pytest.approx(sum(x["judge_tau"] for x in rows) / len(rows))
    assert d["verdict"] == J.verdict_v1(*d["judge_minus_ledger"])


def _fake_chat_api_module():
    import importlib.util

    path = PKG_ROOT / "tests" / "core" / "analysis_fixtures" / "helpers.py"
    name = "_dbwm_core_test_helpers"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod          # dataclasses resolve annotations through sys.modules
        spec.loader.exec_module(mod)
    return sys.modules[name]


def test_cli_openai_compat_judge_against_a_fake_endpoint(tmp_path, monkeypatch, offline_path):
    """SF-04: `--judge openai_compat` with base URL / key env / pricing (paper GLM shape)."""
    h = _fake_chat_api_module()
    monkeypatch.setenv("DBWM_TEST_GLM_KEY", "glm-key-not-real")

    def reply(body):
        user = body["messages"][-1]["content"]
        return h.chat_response('{"ranking": %s, "reasoning": "alphabetical"}' % json.dumps(sorted(_names(user))),
                               model="accounts/fireworks/models/glm-5p1")

    ap = J.configure_parser(__import__("argparse").ArgumentParser())
    out = tmp_path / "glm"
    with h.FakeChatAPI([reply, reply]) as api:
        args = ap.parse_args(["run", "--model", "accounts/fireworks/models/glm-5p1", "--judge", "openai_compat",
                              "--base-url", api.base_url, "--api-key-env", "DBWM_TEST_GLM_KEY",
                              "--pricing", "1.0,0.5,3.0", "--out", str(out), "--reps", "1", "--n-states", "2",
                              "--offline-report", str(offline_path)])
        assert J.run_cli(args) == 0
    assert len(api.requests) == 2
    body = api.requests[0]
    assert body["model"] == "accounts/fireworks/models/glm-5p1" and body["max_tokens"] == 4000 and body["tools"] == []
    assert api.headers[0]["authorization"] == "Bearer glm-key-not-real"
    rows = J.load_sessions(out)
    assert len(rows) == 2 and all(r["judge_adapter"] == "openai_compat" and r["tool_use_count"] == 0 for r in rows)
    assert all(r["cost_usd"] == pytest.approx((600 * 1.0 + 400 * 0.5 + 100 * 3.0) / 1e6) for r in rows)
    summ = json.loads((out / "judge_summary.json").read_text())
    assert summ["judge_adapter"] == "openai_compat" and summ["judge_base_url"] == api.base_url
    assert summ["judge_api_key_env"] == "DBWM_TEST_GLM_KEY" and summ["judge_pricing"]["output"] == 3.0
    saved = "".join(p.read_text(errors="replace") for p in out.rglob("*") if p.is_file())
    assert "glm-key-not-real" not in saved


@pytest.mark.parametrize("argv, match", [
    (["--judge", "claude_cli", "--base-url", "http://127.0.0.1:9/v1"], "only apply to --judge openai_compat"),
    (["--judge", "openai_compat", "--pricing", "1,2"], "IN,CACHED_IN,OUT"),
    (["--judge", "openai_compat"], "needs base_url"),
])
def test_cli_openai_compat_option_errors(tmp_path, monkeypatch, offline_path, argv, match):
    monkeypatch.delenv("DBWM_JUDGE_BASE_URL", raising=False)
    ap = J.configure_parser(__import__("argparse").ArgumentParser())
    args = ap.parse_args(["run", "--model", "m", "--out", str(tmp_path / "o"), "--n-states", "2",
                          "--offline-report", str(offline_path), *argv])
    with pytest.raises(SystemExit, match=match):
        J.run_cli(args)
    assert not (tmp_path / "o").exists() or not list((tmp_path / "o").glob("s*_c*_r*.json"))
