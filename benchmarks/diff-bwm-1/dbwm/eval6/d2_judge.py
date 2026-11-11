"""D2 LLM-judge arm: can a judge, given the no-ledger information, rank candidates as well as the ledger?

The offline signals (d2_signals) rank six scripted candidate plans per state; the
judge gets the information of MECHANICAL_NO_LEDGER (recent observable history,
the candidates, each candidate's crude projected profit and deviation, the
observable reconciliation RMS) but no private nowcast, and returns a ranking.
tau(judge, V*) is compared with the ledger's tau on the same states.

Ported from the paper harness (bwm_latent/d2_judge.py). Changes:

* The judge runs through ``dbwm.core.judge`` (BUILD-SPEC decision 8): a
  tool-less ``claude -p`` in a fresh empty directory with the stream saved and
  zero tool_use asserted (``claude_cli``), the Anthropic API (``anthropic_api``)
  or a fixture (``mock``). The paper judge inherited the launcher's working
  directory and, before 2026-09-12, the operator's tool settings (Appendix A
  entry 13). A judge error (isolation breach, unparseable answer, infra) is recorded and
  excluded, never scored as a ranking.
* Two prompts. ``v1`` is the paper prompt verbatim. It tells the judge that the
  crude projected profit "ignores the confounded price effect, customer churn
  dynamics ..., cross-segment effects" (d2_judge.py:97-100), which is false: the
  crude V-hat is a simulation of the TRUE structural model that differs from V*
  only in its starting latent state (prior mean) and path count (ledger E6-12).
  ``v2`` describes it accurately. Results always record the prompt version.
* ``--offline-report`` is required (the paper defaulted to a path three
  directories above the script, d2_judge.py:250-252).
* Per-session JSON keeps the paper's fields and adds tool_use_count, the
  adapter, the prompt version and sha256, the served model and any judge error.
* Two aggregations. ``aggregate_sessions`` is the paper's ``aggregate``
  (session-level world-clustered bootstrap), including its hard-coded verdict
  string. ``aggregate_per_state`` averages each state's reps first (the
  paper's other summary form), adds judge-minus-no-ledger / -myopic /
  -VHAT_ONLY and, given the projection-sort taus, judge minus the handed
  projection, and states a neutral ``verdict_v1`` (``judge run`` summaries and
  every report use it).
* The GLM 5.1 judge (Fireworks chat completion) runs through the core
  ``openai_compat`` judge (``--judge openai_compat --base-url ... --api-key-env
  ... [--pricing IN,CACHED_IN,OUT]``): the paper's request shape (max_tokens
  4000, ``tools: []``); tested against a local fake endpoint only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import d2_signals as ds
from . import latent_engine as le

RECENT_QUARTERS = 12                      # observable history rows shown to the judge
PROMPT_VERSIONS = ("v1", "v2")
JUDGE_SCHEMA = {
    "type": "object",
    "required": ["ranking", "reasoning"],
    "properties": {
        "ranking": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
    },
}
DEFAULT_JUDGE_MODEL = None                # the judge model IS the treatment in D2: always pass one

_V1_JUDGMENT = """The crude projected profit is only a baseline: it ignores the confounded price effect,
customer churn dynamics over the {K}-quarter horizon, cross-segment effects, and how
trustworthy the estimate is far from the reference point (large deviation => less
reliable). Use your judgment to RE-RANK the candidates for true expected {K}-quarter
profit. Reason about: does raising price actually help once churn and the true (negative)
price effect are considered? Do high-deviation plans deserve the crude estimate's credit?"""

_V2_JUDGMENT = """The crude projected profit is a forward simulation of the business's structural model
(price effect, price-driven churn over the {K}-quarter horizon and cross-segment marketing
effects all included). What it does NOT know is the current latent demand: it starts every
segment at its long-run average demand, because the actual current level is unobserved, and
it averages only a few simulated paths. Use the recent history to judge whether each
segment's demand is currently running above or below its long-run level, and RE-RANK the
candidates for true expected {K}-quarter profit accordingly."""

_V1_CANDIDATES = """THE CANDIDATES (each already respects the marketing budget). For each you are HANDED a
pre-parsed crude projected profit (a forward simulation from an OBSERVABLE-only demand
estimate — it does NOT use any private latent nowcast) and how far it strays from the
reference operating point:"""

_V2_CANDIDATES = """THE CANDIDATES (each already respects the marketing budget). For each you are HANDED a
pre-parsed crude projected profit (a forward simulation that starts latent demand at its
long-run average — it does NOT use any estimate of the current latent state) and how far
it strays from the reference operating point:"""


# ---------------------------------------------------------------------------
# Prompt — information parity with MECHANICAL_NO_LEDGER (NO private nowcast)
# ---------------------------------------------------------------------------

def build_judge_prompt(state, version: str = "v1") -> str:
    """The judge prompt. v1 = the paper's text byte-for-byte; v2 = accurate description
    of the crude estimate (the only differences are the two marked paragraphs)."""
    if version not in PROMPT_VERSIONS:
        raise ValueError(f"prompt version must be one of {PROMPT_VERSIONS}")
    world = state["world"]
    obs = le.simulate_history(world)      # deterministic; the observable history
    C = state["cutoff"]
    segs = [s.id for s in world.segments]
    names = list(state["candidates"].keys())

    lo = max(0, C - RECENT_QUARTERS)
    hist_lines = []
    for t in range(lo, C):
        for si, sid in enumerate(segs):
            hist_lines.append(
                f"    q{t} {sid}: mkt={obs['marketing'][t,si]:.0f} price={obs['price'][t,si]:.3f} "
                f"signups={obs['signups'][t,si]:.1f} revenue={obs['revenue'][t,si]:.0f}")
    crude = state["crude_state"]
    cand_lines = []
    for n in names:
        p = state["candidates"][n]
        vhat = ds._forward_value(state, p, crude)
        dev = ds._deviation_weight(world, p, state["ref_alloc"], state["budget"])
        levers = ", ".join(f"{s.id}:mkt={float(p[s.id]['marketing']):.0f},price={float(p[s.id]['price']):.3f}"
                           for s in world.segments)
        cand_lines.append(f"  - {n}: crude_projected_profit={vhat:.0f}  deviation_from_reference={dev:.2f}\n"
                          f"      levers: {levers}")
    K = world.held_out
    recon = state["ledger"]["recon_rms_crude"]
    candidates_par = _V1_CANDIDATES if version == "v1" else _V2_CANDIDATES
    judgment_par = (_V1_JUDGMENT if version == "v1" else _V2_JUDGMENT).format(K=K)

    return f"""You are an expert operating-strategy JUDGE. Rank {len(names)} candidate {K}-quarter
plans for a {len(segs)}-segment subscription business by their EXPECTED TOTAL GROSS PROFIT over
the next {K} quarters (best first). You do NOT act — you only rank.

WHAT YOU KNOW
- Structural form (qualitative, no parameter values): {world.to_public()['form']}
- The historical policy set marketing AND price as a function of UNOBSERVED latent demand,
  so the history is CONFOUNDED: a naive read of "price up => signups up" reflects the
  policy, not the causal price effect. Latent demand is persistent and NOT given to you.
- Recent observable history (last {RECENT_QUARTERS} quarters, top-line only):
{chr(10).join(hist_lines)}
- Observable one-step reconciliation error (RMS of recent top-line prediction) = {recon:.3f}
  (higher = the observable model tracks recent demand LESS well).

{candidates_par}
{chr(10).join(cand_lines)}

YOUR JUDGMENT
{judgment_par}

OUTPUT — ONLY a single JSON object on the last line, no prose after it:
{{"ranking": [<candidate names, BEST expected profit first, all {len(names)}>], "reasoning": "<=2 sentences"}}
Candidate names: {names}
"""


# ---------------------------------------------------------------------------
# Parsing and scoring
# ---------------------------------------------------------------------------

def parse_ranking(text, names):
    """The paper's parser: the last JSON object with a 'ranking' list that is a permutation."""
    for m in reversed(list(re.finditer(r"\{[^{}]*\"ranking\"[^{}]*\}", text, re.DOTALL))):
        try:
            obj = json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            continue
        rk = obj.get("ranking")
        if isinstance(rk, list) and set(rk) == set(names):
            return rk, obj.get("reasoning", "")
    return None, None


def valid_ranking(rk, names) -> bool:
    """A ranking must name every candidate exactly once (the paper only checked the set,
    so a ranking with a duplicate and a missing name could not occur after its regex,
    but a structured-output answer could)."""
    return isinstance(rk, list) and len(rk) == len(names) and set(rk) == set(names)


def tau_from_ranking(ranking, vstar, names):
    """d2_judge._tau_from_ranking: ranking (best first) -> scores n-1..0 -> tau-b vs V*."""
    pos = {n: len(names) - 1 - i for i, n in enumerate(ranking)}
    js = [pos[n] for n in names]
    vs = [vstar[n] for n in names]
    tau = ds.kendall_tau_b(js, vs)
    return float(tau) if tau is not None else 0.0


def session_record(state_key, rep, ranking, reasoning, vstar, names, cost=None, turns=None) -> dict:
    """The paper's per-session JSON fields for a parsed ranking."""
    tau = tau_from_ranking(ranking, vstar, names)
    vbest = max(vstar.values())
    top1 = ranking[0]
    return {"world_seed": state_key[0], "cutoff": state_key[1], "rep": rep,
            "judge_tau": tau, "judge_ranking": ranking, "top1_pick": top1,
            "top1_regret": float(vbest - vstar[top1]), "reasoning": reasoning,
            "cost_usd": cost, "turns": turns, "capped": False}


def judge_state(state, adapter, model: str, rep: int = 0, version: str = "v1") -> dict:
    """Run one judge pass on one state through a core judge adapter."""
    names = list(state["candidates"].keys())
    vstar = ds.vstar_values(state)
    prompt = build_judge_prompt(state, version)
    label = f"s{state['world_seed']}_c{state['cutoff']}_r{rep}"
    res = adapter.judge("", prompt, JUDGE_SCHEMA, model, label=label)
    base = {"world_seed": state["world_seed"], "cutoff": state["cutoff"], "rep": rep}
    meta = {"prompt_version": version, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "judge_adapter": getattr(adapter, "name", type(adapter).__name__), "model": model,
            "models_served": list(res.model_served), "tool_use_count": res.tool_use_count,
            "raw": res.raw_path.name if res.raw_path else None}
    if not res.ok:
        return {**base, "judge_error": True, "error": res.error, "error_class": res.error_class,
                "cost_usd": res.cost_usd, "turns": res.turns, **meta}
    rk = (res.parsed or {}).get("ranking")
    if not valid_ranking(rk, names):
        return {**base, "judge_error": True, "error": f"ranking is not a permutation of {names}: {rk!r}",
                "error_class": "parse", "cost_usd": res.cost_usd, "turns": res.turns, **meta}
    rec = session_record((state["world_seed"], state["cutoff"]), rep, rk,
                         (res.parsed or {}).get("reasoning", ""), vstar, names, res.cost_usd, res.turns)
    rec.update(meta)
    return rec


def run_judge(states, adapter, model: str, out: Path, reps: int = 2, version: str = "v1",
              resume: bool = True, log=print) -> list[dict]:
    """Judge every (state, rep); one JSON per session in ``out`` (resumable)."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for st in states:
        for rep in range(reps):
            f = out / f"s{st['world_seed']}_c{st['cutoff']}_r{rep}.json"
            if resume and f.exists():
                try:
                    d = json.loads(f.read_text())
                    if d.get("judge_tau") is not None and d.get("prompt_version", "v1") == version:
                        rows.append(d)
                        continue
                except ValueError:
                    pass
            r = judge_state(st, adapter, model, rep, version)
            f.write_text(json.dumps(r, indent=2, default=str) + "\n")
            rows.append(r)
            if log:
                log(f"s{st['world_seed']}/c{st['cutoff']}/r{rep}: "
                    + (f"JUDGE_ERROR ({r.get('error_class')}): {r.get('error')}" if r.get("judge_error")
                       else f"judge_tau={r['judge_tau']:+.2f} top1={r['top1_pick']}"))
    return rows


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def load_sessions(out: Path) -> list[dict]:
    """Session JSONs in the paper's order (sorted s*_c*_r*.json)."""
    rows = []
    for jf in sorted(Path(out).glob("s*_c*_r*.json")):
        try:
            rows.append(json.loads(jf.read_text()))
        except ValueError:
            continue
    return rows


def usable(rows) -> list[dict]:
    """Sessions that produced a ranking (capped, parse errors and judge errors are excluded)."""
    return [r for r in rows if not (r.get("capped") or r.get("parse_error") or r.get("judge_error")
                                    or r.get("judge_tau") is None)]


SUMMATIONS = ("lsum", "neumaier")


def lsum(xs) -> float:
    total = 0
    for x in xs:
        total = total + x
    return total


def neumaier_sum(xs) -> float:
    """Neumaier compensated summation (the algorithm CPython 3.12 uses in ``sum``)."""
    s = 0.0
    c = 0.0
    for x in xs:
        x = float(x)
        t = s + x
        if abs(s) >= abs(x):
            c += (s - t) + x
        else:
            c += (x - t) + s
        s = t
    return s + c


def _sum(xs, summation):
    xs = list(xs)
    if summation == "lsum":
        return lsum(xs)
    if summation == "neumaier":
        return neumaier_sum(xs)
    raise ValueError(f"summation must be one of {SUMMATIONS}")


VERDICT_LEDGER_WINS = ("LEDGER WINS: judge τ significantly BELOW ledger (CI<0) — the private "
                       "nowcast carries decision-relevant info the judge cannot infer. "
                       "Claim C's partial-reward mechanism SURVIVES; KILL-1 does NOT fire.")
VERDICT_KILL = ("KILL-1 (or near): judge τ matches/exceeds ledger (diff CI lower bound "
                ">-0.05) — a Fable-5 judge with pre-parsed deltas is as good a reward "
                "signal as the ledger; partial rewards are redundant.")
VERDICT_INCONCLUSIVE = "INCONCLUSIVE: judge−ledger CI spans the equivalence margin; need more states."


def _decision(dlo, dhi):
    """The paper's hard-coded verdict string (d2_judge.aggregate). Kept VERBATIM on the paper
    summary path only (``aggregate_sessions``); every other report uses :func:`verdict_v1`."""
    if dhi < 0:
        return VERDICT_LEDGER_WINS
    if dlo > -0.05:
        return VERDICT_KILL
    return VERDICT_INCONCLUSIVE


VERDICT_RULE_V1 = ("v1: states where the judge-minus-ledger interval lies; no mechanism is inferred (both "
                   "ledger signals and every judge's handed projection run the true engine, v7 Section 8 D2)")


def verdict_v1(dm, dlo, dhi) -> str:
    """Neutral reading of judge minus ledger: where the interval lies, nothing more."""
    where = "below 0" if dhi < 0 else ("above 0" if dlo > 0 else "spanning 0")
    return (f"judge - ledger {dm:+.3f} CI [{dlo:+.3f}, {dhi:+.3f}] {where}; no mechanism inferred")


def _signal_taus(offline_report: dict, signal: str) -> dict:
    return {(s["world_seed"], s["cutoff"]): s["signals"][signal]["tau"] for s in offline_report["per_state"]}


def aggregate_sessions(rows, offline_report: dict, summation: str = "lsum") -> dict | None:
    """d2_judge.aggregate: session-level world-clustered bootstrap (B=2000, seed 12345),
    paired against the ledger on the same states, bit for bit."""
    rows = usable(rows)
    if not rows:
        return None
    taus = [r["judge_tau"] for r in rows]
    wids = [r["world_seed"] for r in rows]
    m, lo, hi = ds.clustered_bootstrap(taus, wids, ds.BOOTSTRAP_B)
    summ = {"n_states": len(rows), "judge_mean_tau": m, "judge_tau_ci95": [lo, hi],
            "judge_mean_top1_regret": _sum((r["top1_regret"] for r in rows), summation) / len(rows),
            "total_cost_usd": _sum((r["cost_usd"] for r in rows if r.get("cost_usd")), summation)}
    led = _signal_taus(offline_report, "LEDGER_MECHANICAL")
    paired = [(r["judge_tau"], led[(r["world_seed"], r["cutoff"])])
              for r in rows if (r["world_seed"], r["cutoff"]) in led]
    verdict = None
    if paired:
        diffs = [j - l for j, l in paired]
        dm, dlo, dhi = ds.clustered_bootstrap(diffs, [r["world_seed"] for r in rows
                                                      if (r["world_seed"], r["cutoff"]) in led],
                                              ds.BOOTSTRAP_B)
        summ["ledger_mean_tau_same_states"] = _sum((l for _, l in paired), summation) / len(paired)
        summ["judge_minus_ledger_tau"] = {"mean": dm, "ci95": [dlo, dhi], "n": len(paired)}
        verdict = _decision(dlo, dhi)
    summ["verdict"] = verdict
    return summ


def summary_json(summ: dict) -> str:
    return json.dumps(summ, indent=2, default=str) + "\n"


def aggregate_per_state(rows, offline_report: dict, B: int = ds.BOOTSTRAP_B, seed: int = 12345,
                        extra_signals: dict | None = None) -> dict | None:
    """Average each state's usable reps, then world-clustered bootstrap over states.

    Adds judge minus no-ledger, minus myopic and minus VHAT_ONLY. States are paired with the
    offline report on (world_seed, cutoff).

    ``extra_signals`` maps a label to per-state taus {(world_seed, cutoff): tau} that are not in
    the offline report, e.g. ``{"projection": d2_signals.projection_taus(...)}`` for a "minus
    handed projection" column; each adds ``<label>_tau`` and ``judge_minus_<label>``. The verdict
    is the neutral :func:`verdict_v1` (the paper string lives on ``aggregate_sessions`` only)."""
    import numpy as np
    rows = usable(rows)
    if not rows:
        return None
    by: dict = {}
    for r in rows:
        by.setdefault((r["world_seed"], r["cutoff"]), []).append(r["judge_tau"])
    keys = sorted(by)
    per_state = {k: float(np.mean(by[k])) for k in keys}
    w = [k[0] for k in keys]
    vals = [per_state[k] for k in keys]
    out = {"n_states": len(keys), "n_sessions": len(rows),
           "judge_tau": list(ds.clustered_bootstrap(vals, w, B, seed))}
    for sig, label in (("LEDGER_MECHANICAL", "ledger"), ("MECHANICAL_NO_LEDGER", "no_ledger"),
                       ("MYOPIC_ACTUALS", "myopic"), ("VHAT_ONLY", "vhat_only")):
        taus = _signal_taus(offline_report, sig)
        ks = [k for k in keys if k in taus]
        if not ks:
            continue
        agg = offline_report.get("aggregate", {}).get(sig)
        if agg:
            out[f"{label}_tau"] = [agg["mean_tau"], *agg["tau_ci95"]]
        diffs = [per_state[k] - taus[k] for k in ks]
        out[f"judge_minus_{label}"] = list(ds.clustered_bootstrap(diffs, [k[0] for k in ks], B, seed))
        out[f"judge_minus_{label}_n"] = len(ks)
    for label, taus in (extra_signals or {}).items():
        ks = [k for k in keys if k in taus]
        if not ks:
            continue
        out[f"{label}_tau"] = list(ds.clustered_bootstrap([taus[k] for k in ks], [k[0] for k in ks], B, seed))
        diffs = [per_state[k] - taus[k] for k in ks]
        out[f"judge_minus_{label}"] = list(ds.clustered_bootstrap(diffs, [k[0] for k in ks], B, seed))
        out[f"judge_minus_{label}_n"] = len(ks)
    jl = out.get("judge_minus_ledger")
    out["verdict"] = verdict_v1(*jl) if jl else None
    out["verdict_rule"] = VERDICT_RULE_V1
    out["bootstrap"] = {"B": B, "seed": seed, "unit": "state mean over reps; clusters = world_seed"}
    out["cost_usd"] = _sum((float(r["cost_usd"]) for r in rows if r.get("cost_usd")), "lsum")
    return out


def recompute_sessions(rows, offline_report: dict) -> list[dict]:
    """Re-derive each session's judge_tau, top1_pick and top1_regret from its stored
    ranking and the offline report's V* (a consistency check of stored sessions)."""
    vstar = {(s["world_seed"], s["cutoff"]): s["vstar"] for s in offline_report["per_state"]}
    out = []
    for r in usable(rows):
        vs = vstar[(r["world_seed"], r["cutoff"])]
        names = list(vs.keys())
        rec = session_record((r["world_seed"], r["cutoff"]), r["rep"], r["judge_ranking"],
                             r.get("reasoning"), vs, names, r.get("cost_usd"), r.get("turns"))
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# CLI (dbwm eval6-d2 judge|summary|prompt)
# ---------------------------------------------------------------------------

def configure_parser(ap):
    sub = ap.add_subparsers(dest="judgecmd", required=True)
    j = sub.add_parser("run", help="run a judge over the D2 states (calls an LLM unless --judge mock)")
    j.add_argument("--model", required=True, help="pinned judge model (the judge IS the treatment)")
    j.add_argument("--judge", default="claude_cli", choices=["claude_cli", "anthropic_api", "openai_compat"],
                   help="judge adapter (dbwm.core.judge); openai_compat = any OpenAI chat-completions endpoint, "
                        "as the paper's GLM 5.1 judge (Fireworks) was run")
    j.add_argument("--base-url", help="openai_compat: endpoint base URL (default $DBWM_JUDGE_BASE_URL), "
                                      "e.g. https://api.fireworks.ai/inference/v1")
    j.add_argument("--api-key-env", help="openai_compat: name of the env var holding the API key "
                                         "(default OPENAI_API_KEY; the key is sent only as the bearer header)")
    j.add_argument("--pricing", metavar="IN,CACHED_IN,OUT",
                   help="openai_compat: USD per 1M input, cached-input and output tokens (session cost_usd); "
                        "without it cost_usd is null")
    j.add_argument("--out", required=True, help="results dir (one JSON per session)")
    j.add_argument("--offline-report", required=True, help="the d2_signals report for the same states")
    j.add_argument("--prompt", default="v2", choices=PROMPT_VERSIONS,
                   help="v1 = paper prompt (misdescribes the crude estimate), v2 = corrected")
    j.add_argument("--reps", type=int, default=2)
    j.add_argument("--state-set", default="paper", choices=ds.STATE_SETS)
    j.add_argument("--n-states", type=int, default=16)
    s = sub.add_parser("summary", help="aggregate a judge results dir against an offline report")
    s.add_argument("--dir", required=True)
    s.add_argument("--offline-report", required=True)
    s.add_argument("--mode", default="per_state", choices=["per_state", "sessions"])
    s.add_argument("--out", default=None)
    p = sub.add_parser("prompt", help="print the judge prompt for one state (no LLM)")
    p.add_argument("--world-seed", type=int, default=1)
    p.add_argument("--cutoff", type=int, default=72)
    p.add_argument("--prompt", default="v2", choices=PROMPT_VERSIONS)
    return ap


def projection_signals(offline_report: dict) -> dict | None:
    """``{"projection": {(world_seed, cutoff): tau}}`` from an offline report written by ``signals run``
    without --paper-format (its lambda_zero block), for aggregate_per_state's judge-minus-projection; None
    for the paper-format report."""
    lz = offline_report.get("lambda_zero") or {}
    taus = lz.get("per_state_projection_sort_tau")
    if not taus or len(taus) != len(offline_report.get("per_state", [])):
        return None
    return {"projection": {(s["world_seed"], s["cutoff"]): t for s, t in zip(offline_report["per_state"], taus)}}


def run_cli(args) -> int:
    if args.judgecmd == "prompt":
        print(build_judge_prompt(ds.build_state(args.world_seed, args.cutoff), args.prompt), end="")
        return 0
    if args.judgecmd == "summary":
        rep = json.loads(Path(args.offline_report).read_text())
        rows = load_sessions(Path(args.dir))
        summ = (aggregate_per_state(rows, rep, extra_signals=projection_signals(rep)) if args.mode == "per_state"
                else aggregate_sessions(rows, rep))
        n_err = sum(1 for r in rows if r.get("judge_error") or r.get("parse_error") or r.get("capped"))
        text = summary_json({**(summ or {}), "excluded_sessions": n_err, "mode": args.mode})
        if args.out:
            Path(args.out).write_text(text)
        print(text, end="")
        return 0 if summ else 1
    from dbwm.core.judge import get_judge
    report_path = Path(args.offline_report)
    if not report_path.is_file():
        raise SystemExit(f"--offline-report {report_path} does not exist; run `dbwm eval6-d2 signals run` first")
    rep = json.loads(report_path.read_text())
    states = ds.states_for(args.state_set, args.n_states)
    keys = {(s["world_seed"], s["cutoff"]) for s in rep["per_state"]}
    missing = [(s["world_seed"], s["cutoff"]) for s in states if (s["world_seed"], s["cutoff"]) not in keys]
    if missing:
        raise SystemExit(f"offline report lacks states {missing[:4]}...; build it with the same --state-set")
    out = Path(args.out)
    judge_kw, judge_cfg = judge_options(args)
    try:
        adapter = get_judge(args.judge, out / "judge_raw", **judge_kw)
    except ValueError as e:
        raise SystemExit(f"eval6-d2 judge run: {e}") from e
    rows = run_judge(states, adapter, args.model, out, args.reps, args.prompt)
    summ = aggregate_per_state(rows, rep, extra_signals=projection_signals(rep))
    (out / "judge_summary.json").write_text(summary_json({**(summ or {}), "prompt_version": args.prompt,
                                                          "model": args.model, "judge_adapter": args.judge,
                                                          **judge_cfg}))
    return 0 if summ else 1


def parse_pricing(text: str) -> dict[str, float]:
    """``IN,CACHED_IN,OUT`` (USD per 1M tokens) -> the core judge's pricing mapping."""
    try:
        vals = [float(x) for x in str(text).split(",")]
    except ValueError:
        vals = []
    if len(vals) != 3 or any(v < 0 for v in vals):
        raise SystemExit(f"--pricing must be IN,CACHED_IN,OUT (three non-negative USD per 1M tokens), got {text!r}")
    return {"input": vals[0], "cached_input": vals[1], "output": vals[2]}


def judge_options(args) -> tuple[dict, dict]:
    """(constructor kwargs for get_judge, what judge_summary.json records). The key itself never
    appears in either: only the NAME of its environment variable."""
    oc = {"base_url": getattr(args, "base_url", None), "api_key_env": getattr(args, "api_key_env", None),
          "pricing": parse_pricing(args.pricing) if getattr(args, "pricing", None) else None}
    given = sorted(k for k, v in oc.items() if v is not None)
    if args.judge != "openai_compat":
        if given:
            raise SystemExit(f"--{', --'.join(k.replace('_', '-') for k in given)} only apply to --judge openai_compat")
        return {}, {}
    import os

    kw = {k: v for k, v in oc.items() if v is not None}
    base_url = kw.get("base_url") or os.environ.get("DBWM_JUDGE_BASE_URL")
    cfg = {"judge_base_url": base_url, "judge_api_key_env": kw.get("api_key_env", "OPENAI_API_KEY"),
           "judge_pricing": kw.get("pricing")}
    return kw, cfg
