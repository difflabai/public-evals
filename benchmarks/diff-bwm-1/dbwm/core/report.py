"""Generic per-arm report from verdicts: the numbers every eval prints before its own contrasts.

What it computes, per ruleset and arm:

* success rate under BOTH denominators -- (1) scored cells only, and (2) all
  designed cells, where a cell with no scored attempt (infra-only, missing)
  counts as a fail. The published eval-2 headline used (1) by default
  ("metric A", confirm_report.py:53-62, attrition excluded) and the eval-1
  report computed over present verdicts only; the difference matters
  (ledger A10c), so both are always printed;
* Wilson 95% intervals for every rate;
* overclaim among ALL scored cells and among FAILED scored cells, with counts
  (the published table printed only the latter, confirm_report.py:173-175);
* exclusions by reason (audit flags, unscored cells by their last outcome);
* how many scored attempts ended on the turn budget or hit a rail (clock,
  USD) -- those are scored, never retried;
* process means; and the run's regime, isolation and conformance, printed in
  full. A HOST_UNSAFE run is marked NON-CONFORMING at the top of the report.

Cell selection follows the runner's rule: the scored attempt of a cell is its
FIRST attempt whose outcome is scored; later attempts are never consulted. A
cell whose scored attempt the audit flagged is excluded (not re-drawn).

Evals add their own contrasts on top; ``arm_contrast`` is the generic
world-clustered difference between two arms.
"""

from __future__ import annotations

import json
import os
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from dbwm.core import stats
from dbwm.core.types import RULESETS, SCORED_OUTCOMES, Cell, Isolation, Outcome
from dbwm.core.verdict import VerdictError, atomic_write_text, dump_json, validate_verdict

REPORT_SCHEMA = "dbwm.report/1"
REPORT_JSON = "report.json"
REPORT_MD = "report.md"
RAIL_OUTCOMES = (Outcome.CLOCK_STOP.value, Outcome.COST_STOP.value)


@dataclass
class CellRecord:
    cell_id: str
    instance: str
    arm: str
    rep: int
    attempts: list[dict[str, Any]] = field(default_factory=list)  # verdicts, sorted by attempt
    scored: dict[str, Any] | None = None  # the scored verdict (first scored attempt)
    status: str = "missing"  # scored | excluded | unscored | missing
    exclusion: list[str] = field(default_factory=list)
    invalidated: list[dict[str, Any]] = field(default_factory=list)  # reset/invalidated attempts (ignored)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

VERDICT_FILE_RE = re.compile(r"verdict(?:\.([A-Za-z0-9_-]+))?\.json")
_ATTEMPT_DIR_RE = re.compile(r"attempt-(\d+)")


def _verdict_files(root: Path) -> list[Path]:
    """Verdict files under a run, by layout -- never anything an agent could have written.

    A run directory (it has ``cells/``) is read ONLY at
    ``cells/<cell_id>/attempt-<k>/verdict[.<ruleset>].json``: an agent that
    writes a file called ``verdict.json`` into its sandbox gets it collected
    under ``attempt-<k>/deliverables/`` and must never be read as a verdict.
    Other trees are searched recursively, still
    skipping any ``deliverables``/``sandbox`` directory.
    """
    cells = root / "cells"
    if cells.is_dir():
        out = []
        for cell_dir in sorted(p for p in cells.iterdir() if p.is_dir()):
            for adir in sorted(p for p in cell_dir.iterdir() if p.is_dir() and _ATTEMPT_DIR_RE.fullmatch(p.name)):
                out.extend(sorted(f for f in adir.iterdir() if f.is_file() and VERDICT_FILE_RE.fullmatch(f.name)))
        return out
    out = []
    for p in sorted(root.rglob("verdict*.json")):
        rel_parts = p.relative_to(root).parts[:-1]
        if any(part in ("deliverables", "sandbox") for part in rel_parts):
            continue
        if VERDICT_FILE_RE.fullmatch(p.name):
            out.append(p)
    return out


def load_verdicts(run_dir: str | os.PathLike) -> tuple[list[dict[str, Any]], list[str]]:
    """All valid verdicts under ``run_dir`` (sorted by path) and the load errors.

    Checks beyond the schema: in a run directory, the verdict's cell_id and
    attempt must match ``cells/<cell_id>/attempt-<k>/`` and a
    ``verdict.<ruleset>.json`` must carry that ruleset; a second verdict for
    the same (cell, attempt, ruleset) is reported and ignored (the first, in
    path order, is kept -- ``verdict.json`` sorts before ``verdict.<rs>.json``).
    """
    out: list[dict[str, Any]] = []
    errors: list[str] = []
    root = Path(run_dir)
    in_run_layout = (root / "cells").is_dir()
    seen: dict[tuple[str, int, str], str] = {}
    for p in _verdict_files(root):
        rel = p.relative_to(root).as_posix()
        try:
            v = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            errors.append(f"{rel}: not JSON ({e})")
            continue
        problems = validate_verdict(v)
        if problems:
            errors.append(f"{rel}: invalid verdict ({'; '.join(problems[:3])})")
            continue
        m = VERDICT_FILE_RE.fullmatch(p.name)
        if m and m.group(1) and m.group(1) != v["ruleset"]:
            errors.append(f"{rel}: file name says ruleset {m.group(1)!r} but the verdict says {v['ruleset']!r}")
            continue
        if in_run_layout:
            want_cell, want_k = p.parent.parent.name, int(_ATTEMPT_DIR_RE.fullmatch(p.parent.name).group(1))
            if v["cell"]["cell_id"] != want_cell or v["attempt"] != want_k:
                errors.append(f"{rel}: verdict is for {v['cell']['cell_id']} attempt {v['attempt']}, "
                              f"not the directory it sits in")
                continue
        key = (v["cell"]["cell_id"], v["attempt"], v["ruleset"])
        if key in seen:
            errors.append(f"{rel}: duplicate verdict for {key[0]} attempt {key[1]} ruleset {key[2]} "
                          f"(kept {seen[key]})")
            continue
        seen[key] = rel
        out.append(v)
    return out, errors


def load_invalidated(run_dir: str | os.PathLike) -> dict[tuple[str, int], str]:
    """``{(cell_id, k): reason}`` for attempts excluded by ``attempts.jsonl``.

    Same rule as dbwm.core.attempts.RunStore.effective_attempts: an
    ``invalidate`` record excludes one attempt (by ``attempt_id``); a ``reset``
    record excludes every attempt of its cell logged before it. A torn last
    line is ignored. No log, no invalidations.
    """
    p = Path(run_dir) / "attempts.jsonl"
    out: dict[tuple[str, int], str] = {}
    if not p.is_file():
        return out
    logged: dict[str, list[tuple[str, int]]] = {}
    by_attempt_id: dict[str, tuple[str, int]] = {}
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        t = r.get("type", "attempt")
        if t == "attempt":
            cid, k = r.get("cell_id"), r.get("k")
            if isinstance(cid, str) and isinstance(k, int):
                logged.setdefault(cid, []).append((cid, k))
                by_attempt_id[str(r.get("attempt_id") or f"{cid}#a{k}")] = (cid, k)
        elif t == "reset":
            for key in logged.get(str(r.get("cell_id")), []):
                out.setdefault(key, f"reset: {r.get('reason')}")
        elif t == "invalidate":
            key = by_attempt_id.get(str(r.get("attempt_id")))
            if key is not None:
                out.setdefault(key, f"invalidate: {r.get('reason')}")
    return out


def load_manifest(run_dir: str | os.PathLike) -> dict[str, Any] | None:
    p = Path(run_dir) / "manifest.json"
    if not p.is_file():
        return None
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return m if isinstance(m, dict) else None


def _designed_from(designed: Iterable[Any]) -> list[tuple[str, str, str, int]]:
    out = []
    for d in designed:
        if isinstance(d, Cell):
            out.append((d.cell_id, d.instance, d.arm, d.rep))
        elif isinstance(d, Mapping):
            inst, arm, rep = str(d["instance"]), str(d["arm"]), int(d["rep"])
            out.append((d.get("cell_id") or f"{inst}__{arm}__r{rep}", inst, arm, rep))
        elif isinstance(d, str):
            parts = d.rsplit("__", 2)
            if len(parts) != 3 or not parts[2].startswith("r"):
                raise ValueError(f"cannot parse designed cell id {d!r}")
            out.append((d, parts[0], parts[1], int(parts[2][1:])))
        else:
            raise TypeError(f"designed cells must be Cell, mapping or cell_id, got {type(d).__name__}")
    return out


def select_cells(
    verdicts: Sequence[Mapping[str, Any]],
    designed: Iterable[Any] | None = None,
    invalidated: Mapping[tuple[str, int], str] | None = None,
) -> dict[str, CellRecord]:
    """Group one ruleset's verdicts by cell and apply the scored-attempt and exclusion rules.

    ``invalidated`` (from :func:`load_invalidated`) removes attempts that the
    attempt log reset or invalidated; they are kept in ``rec.invalidated``
    for the record but never selected.
    """
    invalidated = invalidated or {}
    cells: dict[str, CellRecord] = {}
    if designed is not None:
        for cid, inst, arm, rep in _designed_from(designed):
            cells[cid] = CellRecord(cid, inst, arm, rep)
    for v in verdicts:
        c = v["cell"]
        rec = cells.get(c["cell_id"])
        if rec is None:
            rec = cells[c["cell_id"]] = CellRecord(c["cell_id"], c["instance"], c["arm"], c["rep"])
        if (c["cell_id"], v["attempt"]) in invalidated:
            rec.invalidated.append(dict(v))
        else:
            rec.attempts.append(dict(v))
    for rec in cells.values():
        rec.attempts.sort(key=lambda v: v["attempt"])
        rec.invalidated.sort(key=lambda v: v["attempt"])
        rec.scored = next((v for v in rec.attempts if v["scored"]), None)
        if rec.scored is not None:
            audit = rec.scored.get("audit") or {}
            if audit.get("flagged"):
                rec.status = "excluded"
                rec.exclusion = [f"audit:{r}" for r in (audit.get("reasons") or ["flagged"])]
            else:
                rec.status = "scored"
        elif rec.attempts:
            rec.status = "unscored"
            rec.exclusion = [f"unscored:{rec.attempts[-1]['outcome']}"]
        elif rec.invalidated:
            rec.status = "missing"
            rec.exclusion = ["missing:every attempt reset or invalidated"]
        else:
            rec.status = "missing"
            rec.exclusion = ["missing:no attempt"]
    return cells


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------

def _rate(k: int, n: int) -> dict[str, Any]:
    p, lo, hi = stats.wilson(k, n)
    return {"k": k, "n": n, "rate": p, "ci95": [lo, hi]}


def _mean(xs: Iterable[Any]) -> float | None:
    vals = [float(x) for x in xs if x is not None]
    return statistics.fmean(vals) if vals else None


def summarize_arm(arm: str, recs: Sequence[CellRecord]) -> dict[str, Any]:
    designed = len(recs)
    excluded = [r for r in recs if r.status == "excluded"]
    scored = [r.scored for r in recs if r.status == "scored"]
    unscored = [r for r in recs if r.status in ("unscored", "missing")]
    succ = [v for v in scored if v["success"] is not None]
    k_succ = sum(1 for v in succ if v["success"])
    unscorable = len(scored) - len(succ)
    oc_all = [v for v in scored if v["overclaim"] is not None]
    oc_fail = [v for v in oc_all if v["success"] is False]
    outcomes: dict[str, int] = {}
    for v in scored:
        outcomes[v["outcome"]] = outcomes.get(v["outcome"], 0) + 1
    claims: dict[str, int] = {}
    for v in scored:
        claims[str(v["claims_parse_status"])] = claims.get(str(v["claims_parse_status"]), 0) + 1
    exclusions: dict[str, int] = {}
    for r in excluded + unscored:
        for reason in r.exclusion:
            exclusions[reason] = exclusions.get(reason, 0) + 1
    attempts = [v for r in recs for v in r.attempts]
    n_invalidated = sum(len(r.invalidated) for r in recs)
    infra_attempts: dict[str, int] = {}
    for v in attempts:
        if not v["scored"]:
            infra_attempts[v["outcome"]] = infra_attempts.get(v["outcome"], 0) + 1
    n_designed_valid = designed - len(excluded)
    return {
        "arm": arm,
        "designed_cells": designed,
        "scored_cells": len(scored),
        "excluded_cells": len(excluded),
        "unscored_cells": len(unscored),
        "unscorable_scored_cells": unscorable,
        "success_of_scored": _rate(k_succ, len(succ)),
        "success_of_designed": _rate(k_succ, n_designed_valid),
        "deliverable_present": _rate(sum(1 for v in scored if v["deliverable_present"]), len(scored)),
        "overclaim_of_all": _rate(sum(1 for v in oc_all if v["overclaim"]), len(oc_all)),
        "overclaim_of_fails": _rate(sum(1 for v in oc_fail if v["overclaim"]), len(oc_fail)),
        "claimed_success_strict": _rate(sum(1 for v in scored if v["claimed_success"] is True),
                                        sum(1 for v in scored if v["claimed_success"] is not None)),
        "claimed_success_paper": _rate(sum(1 for v in scored if v["claimed_success_paper"] is True),
                                       sum(1 for v in scored if v["claimed_success_paper"] is not None)),
        "claims_parse_status": dict(sorted(claims.items())),
        "scored_outcomes": dict(sorted(outcomes.items())),
        "budget_exhausted": outcomes.get(Outcome.BUDGET_EXHAUSTED.value, 0),
        "rails_hit": {o: outcomes.get(o, 0) for o in RAIL_OUTCOMES},
        "attempts_total": len(attempts),
        "attempts_invalidated": n_invalidated,
        "unscored_attempts_by_outcome": dict(sorted(infra_attempts.items())),
        "exclusions": dict(sorted(exclusions.items())),
        "process_means": {
            k: _mean((v.get("process") or {}).get(k) for v in scored)
            for k in ("turns", "assistant_messages", "tool_calls", "oracle_calls", "cost_usd", "duration_s")
        },
        "models_served": sorted({m for v in scored for m in v.get("models_served") or []}),
    }


def _conformance(manifest: Mapping[str, Any] | None) -> dict[str, Any]:
    m = manifest or {}
    regime = m.get("regime") or {}
    iso = m.get("isolation") or regime.get("isolation")
    conforming = m.get("conforming")
    if conforming is None and "conformance" in m:
        c = m["conformance"]
        conforming = c.get("conforming") if isinstance(c, Mapping) else (c in (True, "conforming"))
    reasons = [str(r) for r in (m.get("nonconforming_reasons") or [])]
    if iso == Isolation.HOST_UNSAFE.value:
        conforming = False
        if not any("host-unsafe" in r for r in reasons):
            reasons.append("isolation host-unsafe: bash on the host, answer keys on the same disk")
    if conforming is False and not reasons:
        reasons.append("manifest marks the run non-conforming")
    return {"isolation": iso, "conforming": conforming, "reasons": reasons}


def summarize(
    verdicts: Sequence[Mapping[str, Any]],
    *,
    designed_cells: Iterable[Any] | None = None,
    manifest: Mapping[str, Any] | None = None,
    load_errors: Sequence[str] = (),
    invalidated: Mapping[tuple[str, int], str] | None = None,
) -> dict[str, Any]:
    """The report as a JSON-able dict (one section per ruleset present)."""
    designed_list = None
    designed_source = "verdicts"
    if designed_cells is not None:
        designed_list = list(designed_cells)
        designed_source = "argument"
    elif manifest and isinstance(manifest.get("cells"), list):
        designed_list = list(manifest["cells"])
        designed_source = "manifest"

    rulesets = sorted({v["ruleset"] for v in verdicts}, key=lambda r: RULESETS.index(r) if r in RULESETS else 99)
    sections = []
    for rs in rulesets or ([manifest.get("regime", {}).get("ruleset")] if manifest else []):
        if rs is None:
            continue
        vs = [v for v in verdicts if v["ruleset"] == rs]
        cells = select_cells(vs, designed_list, invalidated)
        arms: dict[str, list[CellRecord]] = {}
        for rec in cells.values():
            arms.setdefault(rec.arm, []).append(rec)
        evals = sorted({v["eval"] for v in vs})
        sections.append({
            "ruleset": rs,
            "evals": evals,
            "arms": [summarize_arm(a, sorted(recs, key=lambda r: r.cell_id)) for a, recs in sorted(arms.items())],
            "excluded": sorted(
                ({"cell_id": r.cell_id, "reasons": r.exclusion} for r in cells.values()
                 if r.status in ("excluded", "unscored", "missing")),
                key=lambda d: d["cell_id"]),
        })
    m = manifest or {}
    return {
        "schema": REPORT_SCHEMA,
        "run_id": m.get("run_id"),
        "regime": m.get("regime"),
        "regime_hash": m.get("regime_hash"),
        "manifest_fields": {k: m[k] for k in sorted(m) if k not in ("regime", "cells")},
        "conformance": _conformance(manifest),
        "designed_cells_source": designed_source,
        "verdicts_read": len(verdicts),
        "attempts_invalidated_by_log": len(invalidated or {}),
        "load_errors": list(load_errors),
        "sections": sections,
    }


def arm_contrast(
    verdicts: Sequence[Mapping[str, Any]],
    arm_a: str | Sequence[str],
    arm_b: str | Sequence[str],
    *,
    ruleset: str,
    designed_cells: Iterable[Any] | None = None,
    denominator: str = "designed",
    B: int = 2000,
    seed: int,
    invalidated: Mapping[tuple[str, int], str] | None = None,
) -> dict[str, Any]:
    """World-clustered bootstrap of success(arm_a) - success(arm_b), clusters = instances.

    ``denominator="designed"`` counts unscored/missing designed cells as fails;
    ``"scored"`` uses scored cells only. Audit-excluded cells are dropped in
    both. Pooled arm groups (a list of arm names) are resampled per arm within
    each instance.
    """
    if denominator not in ("designed", "scored"):
        raise ValueError("denominator must be 'designed' or 'scored'")
    cells = select_cells([v for v in verdicts if v["ruleset"] == ruleset],
                         list(designed_cells) if designed_cells is not None else None, invalidated)
    group_a = [arm_a] if isinstance(arm_a, str) else list(arm_a)
    group_b = [arm_b] if isinstance(arm_b, str) else list(arm_b)

    def build(group: list[str]) -> dict[str, dict[str, list[bool]]]:
        out: dict[str, dict[str, list[bool]]] = {}
        for rec in cells.values():
            if rec.arm not in group or rec.status == "excluded":
                continue
            if rec.status == "scored" and rec.scored is not None:
                if rec.scored["success"] is None:
                    if denominator == "scored":
                        continue
                    val = False
                else:
                    val = bool(rec.scored["success"])
            elif denominator == "designed":
                val = False
            else:
                continue
            out.setdefault(rec.instance, {}).setdefault(rec.arm, []).append(val)
        return out

    a, b = build(group_a), build(group_b)
    point, lo, hi, dropped = stats.cluster_bootstrap_diff(a, b, B, seed)
    n_a = sum(len(x) for d in a.values() for x in d.values())
    n_b = sum(len(x) for d in b.values() for x in d.values())
    return {
        "arm_a": group_a, "arm_b": group_b, "ruleset": ruleset, "denominator": denominator,
        "n_a": n_a, "n_b": n_b, "clusters": len(set(a) | set(b)),
        "diff": point, "ci95": [lo, hi], "excludes_zero": stats.ci_excludes_zero(lo, hi),
        "dropped_resamples": dropped, "B": B, "seed": seed,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def _fmt_rate(r: Mapping[str, Any]) -> str:
    if not r["n"]:
        return f"{r['k']}/0"
    lo, hi = r["ci95"]
    return f"{r['k']}/{r['n']} = {_pct(r['rate'])} [{_pct(lo)}, {_pct(hi)}]"


def _fmt_num(x: float | None, digits: int = 1) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def render_markdown(summary: Mapping[str, Any], title: str | None = None) -> str:
    L: list[str] = [f"# {title or 'diff-bwm-1 run report'}", ""]
    conf = summary["conformance"]
    if conf.get("conforming") is False:
        L += ["> **NON-CONFORMING RUN.** " + "; ".join(conf.get("reasons") or []) +
              ". Do not report these numbers as benchmark results.", ""]
    L.append(f"- Isolation: `{conf.get('isolation')}`; conforming: `{conf.get('conforming')}`")
    mf = summary.get("manifest_fields") or {}
    if mf.get("cli_version") or mf.get("cli_version_verified") is not None:
        # Review M2: host isolation rests on CLI behaviour verified for specific versions.
        verified = mf.get("cli_version_verified")
        verified_txt = "n/a (not a Claude Code run)" if verified is None else verified
        L.append(f"- Claude Code: `{mf.get('cli_version')}`; verified version: `{verified_txt}`")
        if mf.get("cli_version_verified") is False:
            L.append("  - **Unverified CLI version**: the `--restricted`/`--tools` isolation was checked on other "
                     "versions only (`dbwm.core.adapters.claude_code.VERIFIED_CLI_VERSIONS`).")
    if summary.get("run_id"):
        L.append(f"- Run id: `{summary['run_id']}`")
    if summary.get("regime_hash"):
        L.append(f"- Regime hash: `{summary['regime_hash']}`")
    L.append(f"- Verdicts read: {summary['verdicts_read']}; designed cells from: {summary['designed_cells_source']}")
    if summary.get("load_errors"):
        L.append(f"- **Unreadable or invalid verdicts: {len(summary['load_errors'])}** (listed at the end)")
    L.append("")

    regime = summary.get("regime") or {}
    if regime:
        L += ["## Regime", "", "| field | value |", "|---|---|"]
        for k in sorted(regime):
            L.append(f"| {k} | `{json.dumps(regime[k], sort_keys=True)}` |")
        L.append("")
    other = summary.get("manifest_fields") or {}
    if other:
        L += ["## Run manifest", "", "| field | value |", "|---|---|"]
        for k in sorted(other):
            L.append(f"| {k} | `{json.dumps(other[k], sort_keys=True, default=str)}` |")
        L.append("")

    for sec in summary["sections"]:
        L += [f"## Ruleset `{sec['ruleset']}`" + (f" ({', '.join(sec['evals'])})" if sec["evals"] else ""), ""]
        L += ["Success under both denominators (Wilson 95%). *Scored*: cells whose first scored "
              "attempt exists and passed audit. *Designed*: every designed cell minus audit "
              "exclusions; cells with no scored attempt count as fails.", ""]
        L += ["| arm | designed | scored | excluded | unscored | success (scored) | success (designed) "
              "| deliverable present |", "|---|---|---|---|---|---|---|---|"]
        for a in sec["arms"]:
            L.append(f"| {a['arm']} | {a['designed_cells']} | {a['scored_cells']} | {a['excluded_cells']} | "
                     f"{a['unscored_cells']} | {_fmt_rate(a['success_of_scored'])} | "
                     f"{_fmt_rate(a['success_of_designed'])} | {_fmt_rate(a['deliverable_present'])} |")
        L += ["", "| arm | overclaim (of all scored) | overclaim (of fails) | claimed success strict "
              "| claimed success paper | claims parse status |", "|---|---|---|---|---|---|"]
        for a in sec["arms"]:
            cps = ", ".join(f"{k}: {v}" for k, v in a["claims_parse_status"].items()) or "n/a"
            L.append(f"| {a['arm']} | {_fmt_rate(a['overclaim_of_all'])} | {_fmt_rate(a['overclaim_of_fails'])} | "
                     f"{_fmt_rate(a['claimed_success_strict'])} | {_fmt_rate(a['claimed_success_paper'])} | {cps} |")
        L += ["", "| arm | budget exhausted | clock rail | USD rail | attempts | unscored attempts "
              "| invalidated attempts | mean turns | mean cost USD | mean oracle calls |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for a in sec["arms"]:
            un = ", ".join(f"{k}: {v}" for k, v in a["unscored_attempts_by_outcome"].items()) or "0"
            pm = a["process_means"]
            L.append(f"| {a['arm']} | {a['budget_exhausted']} | {a['rails_hit'][Outcome.CLOCK_STOP.value]} | "
                     f"{a['rails_hit'][Outcome.COST_STOP.value]} | {a['attempts_total']} | {un} | "
                     f"{a['attempts_invalidated']} | "
                     f"{_fmt_num(pm['turns'])} | {_fmt_num(pm['cost_usd'], 2)} | {_fmt_num(pm['oracle_calls'])} |")
        L.append("")
        if sec["excluded"]:
            L += ["### Excluded and unscored cells", "", "| cell | reasons |", "|---|---|"]
            for e in sec["excluded"]:
                L.append(f"| {e['cell_id']} | {', '.join(e['reasons'])} |")
            L.append("")
    if not summary["sections"]:
        L += ["No verdicts found.", ""]
    if summary.get("load_errors"):
        L += ["## Load errors", ""] + [f"- {e}" for e in summary["load_errors"]] + [""]
    return "\n".join(L)


def eval_report_markdown(eval_report: Any, eval_def: Any = None) -> str | None:
    """The eval's own section for ``report.md``, whatever shape its ``report()`` returned.

    Evals render their contrasts in different ways, so this accepts each shape
    in use rather than imposing one (first match wins):

    1. a string: already markdown;
    2. a mapping with a string ``markdown`` (evals 3, 4);
    3. a mapping that only says ``error`` / ``skipped`` (the CLI's own wrappers);
    4. an optional ``eval_def.report_markdown(eval_report) -> str`` hook;
    5. ``render_markdown(eval_report)`` in the eval package's ``report`` module (evals 2, 5, 6);
    6. per-ruleset ``rulesets.<rs>.summary_md`` strings (eval 1).

    Returns None when nothing renders (the numbers are still in report.json).
    A renderer that raises yields a visible error line, never a silent gap.
    """
    if eval_report is None:
        return None
    if isinstance(eval_report, str):
        return eval_report
    if not isinstance(eval_report, Mapping):
        return None
    md = eval_report.get("markdown")
    if isinstance(md, str):
        return md
    if set(eval_report) <= {"error", "skipped"} and eval_report:
        k = "error" if "error" in eval_report else "skipped"
        return f"_Eval-specific report {k}: {eval_report[k]}_"
    renderers: list[tuple[str, Any]] = []
    hook = getattr(eval_def, "report_markdown", None) if eval_def is not None else None
    if callable(hook):
        renderers.append(("report_markdown", hook))
    if eval_def is not None:
        mod_name = getattr(eval_def, "eval_module", None) or f"dbwm.{getattr(eval_def, 'eval_id', '')}"
        try:
            import importlib

            fn = getattr(importlib.import_module(f"{mod_name}.report"), "render_markdown", None)
        except Exception:  # noqa: BLE001 - no report module: fall through to the other shapes
            fn = None
        if callable(fn):
            renderers.append((f"{mod_name}.report.render_markdown", fn))
    for name, fn in renderers:
        try:
            out = fn(eval_report)
        except Exception as e:  # noqa: BLE001 - shown in the report, never silently dropped
            return f"_Eval-specific report could not be rendered ({name}: {type(e).__name__}: {e}); see report.json._"
        if isinstance(out, str):
            return out
    rs = eval_report.get("rulesets")
    if isinstance(rs, Mapping):
        parts = []
        for name in sorted(rs):
            sec = rs[name]
            if isinstance(sec, Mapping) and isinstance(sec.get("summary_md"), str):
                parts += [f"## Eval section, ruleset `{name}`", "", sec["summary_md"].rstrip(), ""]
            elif isinstance(sec, Mapping) and isinstance(sec.get("note"), str):
                parts += [f"## Eval section, ruleset `{name}`", "", sec["note"], ""]
        if parts:
            return "\n".join(parts)
    return None


def render_full_markdown(summary: Mapping[str, Any], title: str | None = None, eval_def: Any = None) -> str:
    """Generic tables (``render_markdown``) followed by the eval's own section, if any."""
    md = render_markdown(summary, title)
    section = eval_report_markdown(summary.get("eval_report"), eval_def)
    if section:
        md = md.rstrip("\n") + "\n\n---\n\n" + section.rstrip("\n") + "\n"
    elif summary.get("eval_report") is not None:
        md = md.rstrip("\n") + "\n\n---\n\n_The eval-specific report is in report.json (`eval_report`)._\n"
    return md


def build_report(run_dir: str | os.PathLike, *, designed_cells: Iterable[Any] | None = None) -> dict[str, Any]:
    verdicts, errors = load_verdicts(run_dir)
    return summarize(verdicts, designed_cells=designed_cells, manifest=load_manifest(run_dir), load_errors=errors,
                     invalidated=load_invalidated(run_dir))


def write_report(
    run_dir: str | os.PathLike,
    out_dir: str | os.PathLike | None = None,
    *,
    designed_cells: Iterable[Any] | None = None,
    title: str | None = None,
) -> tuple[Path, Path]:
    """Write report.json and report.md (default: into ``run_dir``). Returns their paths."""
    summary = build_report(run_dir, designed_cells=designed_cells)
    out = Path(out_dir) if out_dir is not None else Path(run_dir)
    jp = atomic_write_text(out / REPORT_JSON, dump_json(summary))
    mp = atomic_write_text(out / REPORT_MD, render_markdown(summary, title))
    return jp, mp


__all__ = [
    "CellRecord", "load_verdicts", "load_invalidated", "load_manifest", "select_cells", "summarize_arm", "summarize",
    "arm_contrast", "render_markdown", "eval_report_markdown", "render_full_markdown", "build_report", "write_report",
    "VerdictError", "SCORED_OUTCOMES",
]
