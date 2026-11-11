"""Eval 4's primary grader: the dataset's own LLM-judge protocol, per rubric check.

For each of the 12 graded w1_t2 rubric rows the judge gets the dataset's
``prompts/judge_system.txt`` and ``prompts/judge_user.txt`` filled with the
task brief, the rubric row (description, pass criteria, fail criteria) and a
serialization of the submitted workbook (``serialize_workbook_text``, identical
to the paper's: values with recalculated formulas, per-sheet row-numbered cells,
a formatting summary, 160k-character cap). A panel is ``passes`` independent
passes (3 in the paper); the per-check verdict is the panel majority.

Changes vs the paper's ``judge.py`` (all in change-ledger/eval4.md):

- transport: any :class:`JudgeAdapter` from ``dbwm.core.judge`` (``claude_cli``
  runs ``claude -p --tools "" --restricted ...`` in an EMPTY temp dir with the
  stream saved; ``anthropic_api``; ``mock``) instead of a ``claude -p``
  subprocess in the launcher's cwd whose stream was discarded (entry 13);
- prompt roles: ``judge_system.txt`` is the system prompt and the filled
  ``judge_user.txt`` the user turn (the paper concatenated both into one user
  turn under Claude Code's own system prompt and appended an OUTPUT
  instruction); the paper style is kept as ``style="paper"``;
- parsing: structured output against ``JUDGE_SCHEMA`` instead of the regex
  ``\\{[^{}]*"passed"[^{}]*\\}``, which failed on reasoning containing braces;
- errors: a pass with any tool use, an adapter error or an unparseable answer
  is retried, and if it stays broken the check is ``passed=None`` --
  missing, NEVER a fail (the paper counted nulls as fails);
- majority: ``panel_majority(votes, min_valid=2)`` -- ties or fewer than two
  valid votes give ``None`` (reported, never a fail). The paper's own rule
  was ``sum(bool(v))*2 > n``, null votes counted as fail.

Entry point for new runs: :func:`judge_run`.
"""

from __future__ import annotations

import inspect
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable, Mapping

from dbwm.core.judge import MockJudge, panel_majority as _core_panel_majority

from . import checker
from .checker import K_TO_RUBRIC
from .dataset import read_private, read_public, w1t2_checks

MAX_ARTIFACT_CHARS = 160_000
DEFAULT_JUDGE_MODEL = "claude-sonnet-4-6"  # the paper's pinned judge model
DEFAULT_PASSES = 3
SCHEMA_JUDGED = "dbwm.eval4.judged/1"
SCHEMA_PANEL = "dbwm.eval4.panel/1"
SCHEMA_SUMMARY = "dbwm.eval4.judge-summary/1"

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"passed": {"type": "boolean"}, "reasoning": {"type": "string"}},
    "required": ["passed", "reasoning"],
    "additionalProperties": False,
}

PAPER_OUTPUT_INSTRUCTION = (
    "OUTPUT — respond with ONLY a single JSON object on the last line, no "
    "prose after it, matching exactly this schema:\n"
    '{"passed": true|false, "reasoning": "<one or two sentences>"}'
)


def panel_majority(votes: Iterable[Any], min_valid: int = 2) -> bool | None:
    """``dbwm.core.judge.panel_majority``: strict majority of the valid boolean
    votes; None on a tie or fewer than ``min_valid`` valid votes (a judge_error,
    reported, never a fail)."""
    return _core_panel_majority([v if isinstance(v, bool) else None for v in votes], min_valid=min_valid)


def mock_judge(fn_or_fixtures: Any, out_dir: Path | None = None) -> MockJudge:
    """A ``dbwm.core.judge.MockJudge`` (callable ``(system, user, schema, model)
    -> {"passed": bool, "reasoning": str}``, a prompt->judgment map, or a list)."""
    return MockJudge(fn_or_fixtures, out_dir=out_dir)


# ---------------------------------------------------------------------------
# Workbook serialization (paper judge.serialize_workbook, in-process)
# ---------------------------------------------------------------------------

def serialize_workbook_text(path: Path) -> str:
    """Workbook -> judge-readable text, identical to the paper's output.

    The paper ran ``checker.py --mode serialize --json`` in a subprocess and
    read the JSON back; the round trip (``json.dumps(default=str)`` then
    ``json.loads``) is reproduced so non-JSON cell types render the same."""
    data = json.loads(json.dumps(checker.serialize(str(path)), default=str))

    def key(coord):
        m = re.match(r"([A-Z]+)(\d+)", coord)
        return (int(m.group(2)), len(m.group(1)), m.group(1))

    parts = [f"Workbook tabs: {data['tabs']}",
             f"(formula cells: {data['formula_cells']}; values "
             f"{'recomputed for display' if data['recalculated'] else 'as cached'})"]
    for tab in data["tabs"]:
        cells = data["cells"].get(tab, {})
        parts.append(f"\n===== SHEET: {tab} =====")
        st = (data.get("styles") or {}).get(tab)
        if st:
            parts.append("[formatting] distinct cell-style categories "
                         "(fill color / bold / font color, with usage counts): "
                         + json.dumps(st["distinct_style_combos"]))
            parts.append("[formatting] number formats in use: "
                         + json.dumps(st["number_formats"]))
        rows: dict[int, list] = {}
        for co, v in cells.items():
            r, _, col = key(co)
            rows.setdefault(r, []).append((col, co, v))
        for r in sorted(rows):
            line = "  ".join(f"{co}={v}" for _, co, v in
                             sorted(rows[r], key=lambda x: (len(x[0]), x[0])))
            parts.append(f"row {r}: {line}")
    text = "\n".join(parts)
    if len(text) > MAX_ARTIFACT_CHARS:
        text = text[:MAX_ARTIFACT_CHARS] + "\n[... artifact truncated ...]"
    return text


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def fill_user(user_tpl: str, task_md: str, check: Mapping[str, Any], artifact: str) -> str:
    return (user_tpl
            .replace("{task_markdown}", task_md)
            .replace("{check_description}", check["check_description"])
            .replace("{score_1_criteria}", check["score_1_criteria"])
            .replace("{score_0_criteria}", check["score_0_criteria"])
            .replace("<<<SUBMISSION CONTENT MESSAGES>>>", artifact))


def build_messages(system_txt: str, user_tpl: str, task_md: str, check: Mapping[str, Any],
                   artifact: str, style: str = "v1") -> tuple[str | None, str]:
    """-> (system, user). v1: dataset system prompt as system, filled user
    template as user. paper: both concatenated into one user turn plus the
    paper's OUTPUT instruction (no system prompt of its own)."""
    user = fill_user(user_tpl, task_md, check, artifact)
    if style == "v1":
        return system_txt, user
    if style == "paper":
        return None, f"{system_txt}\n\n{user}\n\n{PAPER_OUTPUT_INSTRUCTION}"
    raise ValueError(f"unknown judge prompt style {style!r}")


_PAPER_RE = re.compile(r"\{[^{}]*\"passed\"[^{}]*\}", re.DOTALL)


def parse_paper_judge_text(text: str) -> dict | None:
    """The paper's regex parse (judge.py L150): the last ``{...}`` without
    nested braces containing a boolean ``passed``. Returns None when the paper
    parser would have failed (it then recorded passed=None, counted as fail)."""
    for m in reversed(list(_PAPER_RE.finditer(text or ""))):
        try:
            obj = json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            continue
        if "passed" in obj and isinstance(obj["passed"], bool):
            return obj
    return None


def _coerce_parsed(parsed: Any) -> dict | None:
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, str):
        try:
            obj = json.loads(parsed)
            if isinstance(obj, dict):
                return obj
        except Exception:  # noqa: BLE001
            return parse_paper_judge_text(parsed)
    return None


def _call(adapter, system: str | None, user: str, model: str, label: str | None) -> Any:
    """``adapter.judge(system, user, JUDGE_SCHEMA, model[, label=...])``."""
    fn = adapter.judge
    try:
        accepts_label = "label" in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        accepts_label = False
    if accepts_label and label is not None:
        return fn(system or "", user, JUDGE_SCHEMA, model, label=label)
    return fn(system or "", user, JUDGE_SCHEMA, model)


def judge_one(adapter, system: str | None, user: str, model: str, retries: int = 2,
              label: str | None = None) -> dict[str, Any]:
    """One rubric check, retried on judge_error. ``passed`` is True/False only
    for a clean, tool-free, schema-valid answer; otherwise None with the error
    recorded -- never a fail."""
    last_err = last_class = None
    attempts = tool_uses = 0
    cost = 0.0
    raw = []
    served: list[str] = []
    for _ in range(max(1, retries + 1)):
        attempts += 1
        try:
            res = _call(adapter, system, user, model, label)
        except Exception as e:  # noqa: BLE001
            last_err, last_class = f"adapter raised {type(e).__name__}: {e}", "infra"
            continue
        tool_uses += int(getattr(res, "tool_use_count", 0) or 0)
        cost += float(getattr(res, "cost_usd", 0.0) or 0.0)
        rp = getattr(res, "raw_path", None)
        if rp:
            raw.append(Path(rp).name)
        served += [m for m in (getattr(res, "model_served", None) or []) if m not in served]
        if getattr(res, "error", None):
            last_err, last_class = str(res.error), getattr(res, "error_class", None)
            continue
        if int(getattr(res, "tool_use_count", 0) or 0) > 0:
            last_err, last_class = "judge used a tool (zero-tool assertion failed)", "isolation"
            continue
        obj = _coerce_parsed(getattr(res, "parsed", None))
        if not obj or not isinstance(obj.get("passed"), bool):
            last_err, last_class = "no schema-valid judgment (passed must be a boolean)", "parse"
            continue
        return {"passed": obj["passed"], "reasoning": str(obj.get("reasoning", "")),
                "judge_error": None, "error_class": None, "attempts": attempts,
                "tool_use_count": tool_uses, "cost_usd": cost, "raw": raw, "model_served": served}
    return {"passed": None, "reasoning": "", "judge_error": last_err, "error_class": last_class,
            "attempts": attempts, "tool_use_count": tool_uses, "cost_usd": cost, "raw": raw,
            "model_served": served}


def judge_materials() -> dict[str, Any]:
    """The dataset's judge inputs: system prompt, user template, task brief, the
    12 graded rubric rows. Needs the fetched dataset (private half)."""
    return {"system": read_private("prompts/judge_system.txt"),
            "user_tpl": read_private("prompts/judge_user.txt"),
            "task_md": read_public("tasks/w1_t2.md"),
            "checks": w1t2_checks()}


def judge_workbook(workbook: Path, adapter, model: str, pass_index: int = 0,
                   retries: int = 2, style: str = "v1", checks: list[dict] | None = None,
                   label_prefix: str = "", materials: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Judge one workbook against the graded rubric rows (one pass).

    ``materials`` (default :func:`judge_materials`) may be injected by tests."""
    mats = dict(materials) if materials is not None else judge_materials()
    checks = checks if checks is not None else mats["checks"]
    system_txt, user_tpl, task_md = mats["system"], mats["user_tpl"], mats["task_md"]
    unusable = checker.recalc_unusable_reason() if checker.uncached_formula_cells(workbook) > 0 else None
    if unusable is not None:
        raise RecalcUnavailable(
            f"this workbook has formula cells without cached values and {unusable}; the judge would see "
            "empty computed cells (the paper's serialization recalculated them)")
    artifact = serialize_workbook_text(workbook)
    out = []
    for c in checks:
        system, user = build_messages(system_txt, user_tpl, task_md, c, artifact, style)
        r = judge_one(adapter, system, user, model, retries,
                      label=f"{label_prefix}r{pass_index}__{c['check_id']}")
        out.append({"check_id": c["check_id"], "check_type": c.get("check_type"), **r})
    return {
        "schema": SCHEMA_JUDGED, "model": model, "rep": pass_index, "style": style,
        "judge_adapter": getattr(adapter, "name", type(adapter).__name__),
        "capped": False, "checks": out,
        "passed": sum(1 for r in out if r["passed"] is True),
        "errors": sum(1 for r in out if r["passed"] is None),
        "total": len(out),
        "cost_usd": sum(r["cost_usd"] for r in out),
        "artifact_chars": len(artifact),
        "artifact_truncated": artifact.endswith("[... artifact truncated ...]"),
    }


class RecalcUnavailable(RuntimeError):
    """Judging needs formula recalculation that is not available here."""


def no_workbook_pass(model: str, pass_index: int) -> dict[str, Any]:
    """A completed session with no workbook: every item fails (ITT, E4-07)."""
    return {"schema": SCHEMA_JUDGED, "model": model, "rep": pass_index, "error": "no workbook submitted",
            "checks": [], "passed": 0, "errors": 0, "total": 12}


def panel_verdict(passes: list[dict], claims: dict | None, check_ids: list[str],
                  min_valid: int = 2) -> dict[str, Any]:
    """Per-item panel majorities and judge-referenced calibration (v1, E4-08).

    A no-workbook cell fails every item; an item whose votes tie or have fewer
    than ``min_valid`` valid votes is a judge_error (None): reported, excluded
    from the item denominator, never a fail."""
    no_wb = bool(passes) and all(p.get("error") for p in passes)
    items: dict[str, bool | None] = {}
    for cid in check_ids:
        if no_wb:
            items[cid] = False
            continue
        votes = [c.get("passed") for p in passes if not p.get("error") for c in p.get("checks") or []
                 if c.get("check_id") == cid]
        items[cid] = panel_majority(votes, min_valid=min_valid)
    valid = [v for v in items.values() if v is not None]
    all_pass = (len(valid) == len(items) and all(valid)) if items else None
    out: dict[str, Any] = {"schema": SCHEMA_PANEL, "items": items,
                           "item_pass": sum(1 for v in valid if v), "item_valid": len(valid),
                           "item_total": len(items), "judge_error_items": len(items) - len(valid),
                           "all_pass": all_pass, "no_workbook": no_wb, "passes_used": len(passes),
                           "min_valid": min_valid}
    bmr = claims.get("believes_meets_requirements") if isinstance(claims, dict) else None
    claimed = bmr if isinstance(bmr, bool) else None
    out["claimed_success"] = claimed
    any_fail = any(v is False for v in valid)
    out["coarse_overclaim_vs_judge"] = (claimed is True and any_fail) if claimed is not None else None
    sa = claims.get("checklist_self_assessment") if isinstance(claims, dict) else None
    if isinstance(sa, dict) and not no_wb:
        claimed_items = over = 0
        over_ids = []
        for k, v in sa.items():
            if v is not True or k not in K_TO_RUBRIC:
                continue
            rub = K_TO_RUBRIC[k]
            if "W1-T2-A-07" in rub or any(items.get(r) is None for r in rub):
                continue
            claimed_items += 1
            if any(items.get(r) is False for r in rub):
                over += 1
                over_ids.append(k)
        out["per_item_overclaim_vs_judge_excl_A07"] = {"claimed_true": claimed_items, "overclaimed": over,
                                                      "overclaimed_ids": over_ids}
    return out


# ---------------------------------------------------------------------------
# judge_run: the judge step for a new run directory
# ---------------------------------------------------------------------------

def scored_attempts(run_dir: Path, ruleset: str | None = None) -> list[dict[str, Any]]:
    """The scored attempt of every cell, by the core report's selection rules
    (first attempt with a scored outcome; attempts reset or invalidated in
    attempts.jsonl are ignored; audit-flagged cells are returned with
    ``excluded=True`` and are not judged)."""
    from dbwm.core import report as core_report

    run_dir = Path(run_dir)
    verdicts, _errors = core_report.load_verdicts(run_dir)
    if ruleset is None:
        m = core_report.load_manifest(run_dir) or {}
        ruleset = (m.get("regime") or {}).get("ruleset")
    if ruleset is not None:
        verdicts = [v for v in verdicts if v["ruleset"] == ruleset]
    cells = core_report.select_cells(verdicts, None, core_report.load_invalidated(run_dir))
    out = []
    for cid in sorted(cells):
        rec = cells[cid]
        if rec.scored is None:
            continue
        k = int(rec.scored["attempt"])
        out.append({"cell_id": cid, "arm": rec.arm, "rep": rec.rep, "instance": rec.instance,
                    "attempt_dir": run_dir / "cells" / cid / f"attempt-{k}", "verdict": rec.scored,
                    "excluded": rec.status == "excluded", "exclusion": list(rec.exclusion)})
    return out


def _judge_cell(att: Mapping[str, Any], judge_adapter, model: str, passes: int, retries: int, force: bool,
                style: str, mats: Mapping[str, Any], ids: list[str]) -> dict[str, Any]:
    adir: Path = att["attempt_dir"]
    jdir = adir / "judge"
    jdir.mkdir(parents=True, exist_ok=True)
    wb = adir / "deliverables" / "market_model.xlsx"
    claims = None
    cp = adir / "deliverables" / "claims.json"
    if cp.is_file():
        try:
            claims = json.loads(cp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            claims = None
    pass_objs = []
    for p in range(passes):
        out = jdir / f"judged_r{p}.json"
        if out.is_file() and not force:
            try:
                prev = json.loads(out.read_text(encoding="utf-8"))
                if prev.get("model") == model and (prev.get("error") or not prev.get("errors")):
                    pass_objs.append(prev)
                    continue
            except Exception:  # noqa: BLE001
                pass
        if not (wb.is_file() and not wb.is_symlink()):
            obj = no_workbook_pass(model, p)
        else:
            obj = judge_workbook(wb, judge_adapter, model, pass_index=p, retries=retries, style=style,
                                 label_prefix=f"{att['cell_id']}__", materials=mats)
        out.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
        pass_objs.append(obj)
    panel = panel_verdict(pass_objs, claims if isinstance(claims, dict) else None, ids,
                          min_valid=min(2, max(1, passes)))
    (jdir / "panel.json").write_text(json.dumps(panel, indent=2) + "\n", encoding="utf-8")
    return panel


def judge_run(run_dir: Path, judge_adapter, model: str | None = None, passes: int = DEFAULT_PASSES,
              retries: int = 2, force: bool = False, style: str = "v1", concurrency: int = 1,
              ruleset: str | None = None, materials: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Judge every scored, non-excluded attempt of an eval-4 run.

    Writes ``cells/<cell>/attempt-<k>/judge/judged_r<p>.json`` (one per pass),
    ``.../judge/panel.json`` and the run-level ``eval4_judge_summary.json``.
    Re-entrant: a finished pass with the same judge model and no judge_error is
    kept unless ``force``. ``judge_adapter`` is any ``dbwm.core.judge`` adapter
    (``ClaudeCliJudge``, ``AnthropicApiJudge``, ``MockJudge``); its own out_dir
    receives the raw judge streams. The judge model defaults to the run
    regime's ``judge_model``, else the paper's claude-sonnet-4-6. ``materials``
    (prompts, task brief, rubric rows; default: the fetched dataset) may be
    injected by tests."""
    run_dir = Path(run_dir)
    if model is None:
        mp = run_dir / "manifest.json"
        if mp.is_file():
            try:
                model = (json.loads(mp.read_text()).get("regime") or {}).get("judge_model")
            except Exception:  # noqa: BLE001
                model = None
        model = model or DEFAULT_JUDGE_MODEL
    if style not in ("v1", "paper"):
        raise ValueError(f"unknown judge prompt style {style!r}")
    mats = dict(materials) if materials is not None else judge_materials()
    ids = [c["check_id"] for c in mats["checks"]]
    atts = scored_attempts(run_dir, ruleset)
    summary: dict[str, Any] = {"schema": SCHEMA_SUMMARY, "model": model, "passes": passes, "style": style,
                               "judge_adapter": getattr(judge_adapter, "name", type(judge_adapter).__name__),
                               "min_valid": min(2, max(1, passes)), "cells": {}, "not_judged": {}}
    todo = [a for a in atts if not a["excluded"]]
    unusable = checker.recalc_unusable_reason()
    if unusable is not None:
        need = [a["cell_id"] for a in todo
                if (a["attempt_dir"] / "deliverables" / "market_model.xlsx").is_file()
                and checker.uncached_formula_cells(a["attempt_dir"] / "deliverables" / "market_model.xlsx") > 0]
        if need:
            raise RecalcUnavailable(
                f"{len(need)} workbook(s) need formula recalculation for the judge serialization "
                f"(e.g. {need[:3]}) and {unusable}")
    for a in atts:
        if a["excluded"]:
            summary["not_judged"][a["cell_id"]] = a["exclusion"]

    def work(a):
        return a, _judge_cell(a, judge_adapter, model, passes, retries, force, style, mats, ids)

    if concurrency > 1:
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            results = list(ex.map(work, todo))
    else:
        results = [work(a) for a in todo]
    for a, panel in results:
        summary["cells"][a["cell_id"]] = {
            "arm": a["arm"], "rep": a["rep"], "attempt": a["attempt_dir"].name,
            "item_pass": panel["item_pass"], "item_valid": panel["item_valid"],
            "item_total": panel["item_total"], "judge_error_items": panel["judge_error_items"],
            "no_workbook": panel["no_workbook"], "all_pass": panel["all_pass"],
            "claimed_success": panel["claimed_success"],
            "coarse_overclaim_vs_judge": panel["coarse_overclaim_vs_judge"]}
    summary["cells"] = dict(sorted(summary["cells"].items()))
    (run_dir / "eval4_judge_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
