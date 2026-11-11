"""Bradley-Terry / Elo ladder over pairwise comparisons (port of scripts/difflab_consulting/elo_report.py).

``bt_fit``, ``to_elo``, ``bootstrap_ci`` (LCG seed 20260702, i.i.d. over
comparisons), ``win_matrix``, ``position_consistency``, ``dimension_rates``
and ``render_text`` / ``report_json`` are the paper's code unchanged.

Release additions (beside the paper's code, which they leave unchanged):

* ``bootstrap_ci(..., cluster="pair" | "scenario")``: resample unordered pairs
  (both presentation orders together) or whole scenarios instead of single
  rows. The i.i.d. row bootstrap treats the two orders of one pair as
  independent evidence and gives intervals that are too narrow (E5-D16).
* ``order_inconsistent_as_ties``: a pair whose winner flips with presentation
  order is a tie, not one win each way.
* ``position_gate``: both-order agreement is reported in every table and a
  ladder below the gate (default 0.75) is labelled POSITION-UNSTABLE; the
  ELO-twin spec itself says such a ladder "is flagged, not trusted".
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

DEFAULT_SEED = 20260702
DEFAULT_GATE = 0.75


def load_comparisons(path, scenario=None, judge=None) -> list[dict[str, Any]]:
    rows = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if scenario and r["scenario"] != scenario:
            continue
        if judge and r.get("judge") != judge:
            continue
        rows.append(r)
    return rows


def bt_fit(rows, iters=500, tol=1e-9, prior_ties=1.0):
    """{competitor: strength} via MM algorithm; ties = half win each; one virtual tie per pair."""
    wins = defaultdict(float)
    n_games = defaultdict(float)
    players = set()
    for r in rows:
        a, b, w = r["first"], r["second"], r["winner"]
        players.update((a, b))
        key = tuple(sorted((a, b)))
        n_games[key] += 1.0
        if w == "tie":
            wins[a] += 0.5
            wins[b] += 0.5
        else:
            wins[w] += 1.0
    for (a, b), _n in list(n_games.items()):
        n_games[(a, b)] += prior_ties
        wins[a] += prior_ties / 2.0
        wins[b] += prior_ties / 2.0
    players = sorted(players)
    if len(players) < 2:
        return {p: 1.0 for p in players}
    p = {x: 1.0 for x in players}
    for _ in range(iters):
        newp = {}
        for i in players:
            denom = 0.0
            for key, n in n_games.items():
                if i not in key:
                    continue
                j = key[0] if key[1] == i else key[1]
                denom += n / (p[i] + p[j])
            newp[i] = (wins[i] / denom) if denom > 0 else p[i]
        logs = [math.log(max(v, 1e-12)) for v in newp.values()]
        gm = math.exp(sum(logs) / len(logs))
        newp = {k: v / gm for k, v in newp.items()}
        delta = max(abs(newp[k] - p[k]) for k in players)
        p = newp
        if delta < tol:
            break
    return p


def to_elo(strengths):
    return {k: 1500.0 + 400.0 * math.log10(max(v, 1e-12)) for k, v in strengths.items()}


def _lcg(seed):
    """Deterministic PRNG (no `random` import: stable across runs and interpreters)."""
    state = seed & 0x7FFFFFFF or 1

    def rand(n):
        nonlocal state
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        return state % n
    return rand


def _clusters(rows, cluster):
    groups = defaultdict(list)
    for r in rows:
        if cluster == "pair":
            key = (r["scenario"], tuple(sorted((r["first"], r["second"]))), r.get("rep"))
        elif cluster == "scenario":
            key = r["scenario"]
        else:
            raise ValueError(f"unknown cluster {cluster!r}")
        groups[key].append(r)
    return [groups[k] for k in sorted(groups, key=lambda k: json.dumps(k, default=str))]


def bootstrap_ci(rows, n_boot=1000, seed=DEFAULT_SEED, cluster=None):
    """Percentile CIs. cluster=None is the paper's i.i.d. row bootstrap (bit-for-bit)."""
    rand = _lcg(seed)
    samples = defaultdict(list)
    groups = _clusters(rows, cluster) if cluster else None
    for _ in range(n_boot):
        if groups is None:
            resample = [rows[rand(len(rows))] for _ in range(len(rows))]
        else:
            resample = [r for _g in range(len(groups)) for r in groups[rand(len(groups))]]
        elo = to_elo(bt_fit(resample, iters=200))
        for k, v in elo.items():
            samples[k].append(v)
    cis = {}
    for k, vals in samples.items():
        vals.sort()
        lo = vals[max(0, int(0.025 * len(vals)) - 1)]
        hi = vals[min(len(vals) - 1, int(0.975 * len(vals)))]
        cis[k] = (lo, hi)
    return cis


def win_matrix(rows):
    m = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    for r in rows:
        a, b, w = r["first"], r["second"], r["winner"]
        for x, y in ((a, b), (b, a)):
            m[x][y][1] += 1
        if w != "tie":
            loser = b if w == a else a
            m[w][loser][0] += 1
        else:
            m[a][b][0] += 0.5
            m[b][a][0] += 0.5
    return m


def position_consistency(rows):
    """Of unordered pairs judged in both orders: fraction with the same winner both times,
    plus the first-position win rate over decisive games."""
    seen = {}
    agree = total = 0
    first_wins = decisive = 0
    for r in rows:
        if r["winner"] != "tie":
            decisive += 1
            if r["winner"] == r["first"]:
                first_wins += 1
        key = (r["scenario"], tuple(sorted((r["first"], r["second"]))))
        if key in seen:
            total += 1
            if seen[key] == r["winner"]:
                agree += 1
        else:
            seen[key] = r["winner"]
    return {
        "both_order_pairs": total,
        "same_winner_rate": round(agree / total, 3) if total else None,
        "first_position_win_rate": round(first_wins / decisive, 3) if decisive else None,
        "decisive_games": decisive,
    }


def both_order_agreement(rows):
    """Rep-aware both-order agreement: unordered pairs judged in both orders within the same
    (scenario, rep, pass), fraction with the same winner. position_consistency (the paper's
    function) keys by scenario only and so mixes reps when several are judged."""
    by = defaultdict(list)
    for r in rows:
        by[(r["scenario"], r.get("rep"), r.get("pass", 0), tuple(sorted((r["first"], r["second"]))))].append(r["winner"])
    pairs = [ws for ws in by.values() if len(ws) >= 2]
    if not pairs:
        return None, 0
    agree = sum(1 for ws in pairs if len(set(ws)) == 1)
    return round(agree / len(pairs), 3), len(pairs)


def dimension_rates(rows):
    out = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    for r in rows:
        for dim, w in (r.get("dimensions") or {}).items():
            for c in (r["first"], r["second"]):
                out[dim][c][1] += 1
            if w == "tie":
                out[dim][r["first"]][0] += 0.5
                out[dim][r["second"]][0] += 0.5
            elif w in (r["first"], r["second"]):
                out[dim][w][0] += 1
    return {dim: {c: round(w / g, 3) for c, (w, g) in comps.items() if g} for dim, comps in out.items()}


def inconsistent_pairs(rows):
    """Unordered pairs (scenario, rep, {a,b}) whose winner differs between the two orders."""
    by = defaultdict(list)
    for r in rows:
        by[(r["scenario"], r.get("rep"), tuple(sorted((r["first"], r["second"]))))].append(r["winner"])
    return sorted((k for k, ws in by.items() if len(ws) >= 2 and len(set(ws)) > 1),
                  key=lambda k: json.dumps(k, default=str))


def order_inconsistent_as_ties(rows):
    bad = set(inconsistent_pairs(rows))
    out = []
    for r in rows:
        k = (r["scenario"], r.get("rep"), tuple(sorted((r["first"], r["second"]))))
        out.append(dict(r, winner="tie") if k in bad else r)
    return out


def ladder_table(rows, n_boot, seed=DEFAULT_SEED, cluster=None):
    elo = to_elo(bt_fit(rows))
    cis = bootstrap_ci(rows, n_boot=n_boot, seed=seed, cluster=cluster) if n_boot else {}
    games = defaultdict(int)
    for r in rows:
        games[r["first"]] += 1
        games[r["second"]] += 1
    lines = ["| # | competitor | Elo | 95% CI | games |", "|---|---|---|---|---|"]
    ranked = sorted(elo.items(), key=lambda kv: -kv[1])
    for i, (c, e) in enumerate(ranked, 1):
        ci = cis.get(c)
        ci_s = f"{ci[0]:.0f}–{ci[1]:.0f}" if ci else "—"
        lines.append(f"| {i} | {c} | {e:.0f} | {ci_s} | {games[c]} |")
    return "\n".join(lines), {c: round(e, 1) for c, e in elo.items()}, cis


def analyze(rows, n_boot=1000, scenario=None, seed=DEFAULT_SEED, cluster=None):
    """(text, json_obj): elo_report.py main() output for already-filtered rows."""
    out: list[str] = []
    judges = sorted({r.get("judge") or "?" for r in rows})
    out.append("# Difflab-consulting-benchmark — Elo report\n")
    judge_cost = sum(r.get("cost_usd") or 0.0 for r in rows)
    out.append(f"{len(rows)} comparisons | judges: {', '.join(judges)}"
               + (f" | judging cost ${judge_cost:.2f}" if judge_cost else "")
               + (f" | scenario: {scenario}" if scenario else " | pooled") + "\n")
    table, elo, cis = ladder_table(rows, n_boot, seed=seed, cluster=cluster)
    out.append("## Ladder (Bradley-Terry → Elo, 1500-centered)\n")
    out.append(table)
    pc = position_consistency(rows)
    out.append("\n## Judge diagnostics\n")
    out.append(f"- both-order agreement: {pc['same_winner_rate']} over {pc['both_order_pairs']} pair-order duplicates")
    out.append(f"- first-position win rate: {pc['first_position_win_rate']} "
               f"(0.5 = unbiased) over {pc['decisive_games']} decisive games")
    tie_rate = sum(1 for r in rows if r["winner"] == "tie") / len(rows)
    out.append(f"- tie rate: {tie_rate:.3f}")
    out.append("\n## Win matrix (row beats column, win-credit / games)\n")
    m = win_matrix(rows)
    comps = sorted(elo, key=lambda c: -elo[c])
    out.append("| | " + " | ".join(comps) + " |")
    out.append("|---" * (len(comps) + 1) + "|")
    for a in comps:
        cells = []
        for b in comps:
            if a == b:
                cells.append("—")
            else:
                w, g = m[a][b]
                cells.append(f"{w:g}/{g:g}" if g else "·")
        out.append(f"| **{a}** | " + " | ".join(cells) + " |")
    out.append("\n## Per-dimension win rates\n")
    dims = dimension_rates(rows)
    for dim, comps_r in sorted(dims.items()):
        ranked = sorted(comps_r.items(), key=lambda kv: -kv[1])
        out.append(f"- {dim}: " + ", ".join(f"{c} {v}" for c, v in ranked))
    per_scenario = {}
    if not scenario:
        out.append("\n## Per-scenario ladders\n")
        for scen in sorted({r["scenario"] for r in rows}):
            sub = [r for r in rows if r["scenario"] == scen]
            elo_s = to_elo(bt_fit(sub))
            ranked = sorted(elo_s.items(), key=lambda kv: -kv[1])
            per_scenario[scen] = {c: round(e, 1) for c, e in ranked}
            out.append(f"- {scen} ({len(sub)} comparisons): " + ", ".join(f"{c} {e:.0f}" for c, e in ranked))
    obj = {"comparisons": len(rows), "judges": judges, "elo": elo, "ci95": {k: list(v) for k, v in cis.items()},
           "position": pc, "tie_rate": round(tie_rate, 3), "dimensions": dims, "per_scenario": per_scenario}
    return "\n".join(out) + "\n", obj


def report_json_text(obj) -> str:
    """Exactly what ``elo_report.py --json`` wrote."""
    return json.dumps(obj, indent=2) + "\n"


def release_summary(rows, n_boot=2000, seed=DEFAULT_SEED, gate=DEFAULT_GATE) -> dict[str, Any]:
    """The release-grade read of a comparison set: gated, pair-clustered, inconsistency-as-tie."""
    agree, n_pairs = both_order_agreement(rows)
    tied = order_inconsistent_as_ties(rows)
    elo_iid = {c: round(e, 1) for c, e in to_elo(bt_fit(rows)).items()}
    elo_tied = {c: round(e, 1) for c, e in to_elo(bt_fit(tied)).items()}
    ci_pair = bootstrap_ci(rows, n_boot=n_boot, seed=seed, cluster="pair")
    ci_tied = bootstrap_ci(tied, n_boot=n_boot, seed=seed, cluster="pair")
    return {
        "comparisons": len(rows),
        "both_order_agreement": agree,
        "both_order_pairs": n_pairs,
        "position_gate": gate,
        "position_unstable": (agree is None) or (agree < gate),
        "inconsistent_pairs": [{"scenario": k[0], "rep": k[1], "pair": list(k[2])} for k in inconsistent_pairs(rows)],
        "elo": elo_iid,
        "ci95_pair_clustered": {k: [round(v[0], 1), round(v[1], 1)] for k, v in sorted(ci_pair.items())},
        "elo_inconsistent_as_ties": elo_tied,
        "ci95_inconsistent_as_ties_pair_clustered": {k: [round(v[0], 1), round(v[1], 1)] for k, v in sorted(ci_tied.items())},
        "bootstrap": {"n_boot": n_boot, "seed": seed, "cluster": "pair"},
    }
