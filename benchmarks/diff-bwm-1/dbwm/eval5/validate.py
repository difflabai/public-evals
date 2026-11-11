"""Deterministic dataset validator for the eval-5 scenarios, plus the golden probe suite.

Ported from scripts/difflab_consulting/validate_dataset.py (checks 1-7 below),
which reported 0 errors / 0 warnings on all four scenarios while
SOLVARA-A-13 and the other score-text/op contradictions were present. The new lints (8-12) are registry-only, so they run on the frozen
v0.1 registry as well as on v1.0:

  1. registry parses; ops known to the ruleset's engine with the right args;
     file refs are deliverables; bands lo<=hi; every regex compiles
  2. anchors: value inside its own band; headline figures present
  3. traceability: anchor sources / files_needed exist (warnings)
  4. file_plan coverage of the agent-visible sources
  5. contradiction quotes appear in the named files
  6. leakage: meta tokens in agent-visible files (incl. CHECKLIST.md)
  7. cross-file: deliverables named in t1.md; auto files are deliverables
  8. ABSENCE SCOPING: a whole-file ``text_absent`` is an error unless the
     check carries ``house_style_ban: {source, quote}`` and the quote is in
     that source (the ban applies 'in any context')
  9. NON-VACUITY: every absence op sits in an ``all_of`` with a positive
     gate on the same file (``csv_none``/``structured_polarity`` gate
     themselves)
 10. SCORE-TEXT / OP PARITY: every [lo,hi] band written in score_1 is an op
     band; "at least N of the M" in the description is an op threshold;
     score text that fails 'partial' (or demands status=gap) has no op
     accepting 'partial'; a structured deliverable is not credited for a
     bare population token ('government') without a polarity leg
 11. PROBES: the golden probe suite (private/eval5/probes.json) grades
     exactly as pinned for this ruleset (v1: every probe correct; paper:
     wrong on exactly PAPER_KNOWN_MISGRADES)
 12. CHECKLIST.md is byte-identical to the rendering of registry v1.0 with
     PASSES WHEN lines (engine.render_checklist_v1); the leakage lint (6)
     also runs over the opt-out rendering (no pass conditions); CANARY.txt
     present; DISCLOSURE: matcher literals absent from the checklist text
     are counted per check both ways, description only and description +
     PASSES WHEN (information only: no arm receives the registry, so the
     count measures how far the checklist under-specifies the matcher, not
     an arm asymmetry)

Exit code 1 if any scenario has ERRORs (warnings do not fail).
Usage: python -m dbwm.eval5.validate [--ruleset v1|paper|as-run] [--scenario id] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from dbwm.core.types import CANARY_GUID

from . import dataset as D
from . import engine as E

FORBIDDEN = ("checks.jsonl", "answer_key", "answer key", "rubric",
             "score_1_criteria", "score_0_criteria", "grader", "ai agent",
             "llm judge", "eval harness", "this scenario is part", CANARY_GUID.lower())

OP_REQUIRED_ARGS = {
    "file_exists": {"file"},
    "structured_path": {"file", "path"},
    "structured_number": {"file", "path", "lo", "hi"},
    "structured_number_all": {"file", "path", "lo", "hi"},
    "structured_enum": {"file", "path", "required_values"},
    "structured_fields": {"file", "path", "required_fields"},
    "structured_text_absent": {"file", "path", "tokens"},
    "csv_required": {"file", "column", "required_values"},
    "csv_condition": {"file", "where", "require"},
    "csv_min_rows": {"file", "min_rows"},
    "text_number_near": {"file", "lo", "hi"},
    "text_contains_any": {"file", "tokens"},
    "text_absent": {"file", "tokens"},
    "text_regex_count": {"file", "regex", "min_count"},
    "all_of": {"of"},
    "any_of": {"of"},
    # v1-only ops
    "k_of": {"of", "min_pass"},
    "csv_none": {"file", "where", "require"},
    "text_amount_near": {"file", "lo", "hi"},
    "structured_polarity": {"file", "path", "mention_regex", "allow_regex"},
}
POSITIVE_GATES = ("file_exists", "structured_path", "structured_fields", "structured_enum", "structured_number",
                  "csv_required", "csv_min_rows", "csv_condition", "text_contains_any", "text_regex_count",
                  "text_number_near", "text_amount_near")
POLARITY_SENSITIVE = ("government", "medicare", "medicaid")

# The golden probes the oracle of record (ruleset "paper": paper engine + registry v0.1 with the
# lab's 2026-10-03 SOLVARA-A-13 fix) grades incorrectly. The lab fix corrects the two A-13
# rejection probes the as-run registry failed (a13-brief-wording-rejection, a13-folded-yaml-rejection);
# everything else the paper engine or the v0.1 specs cannot grade stays wrong: 30 of the 48 original
# probes, plus five of the eight A-13 probes added with the v1.0 token
# revision (the repaired spec passes adoption phrased as "medical benefit", "Part B" or "J-code", or
# placed in a step's actor/system). 35 of 57.
# Pinned so a change to either the probes or the record is noticed (tests/eval5/test_dataset.py).
PAPER_KNOWN_MISGRADES = (
    "a13-empty-yaml", "a13-adoption-without-tokens", "a13-segment-medical-benefit",
    "a13-benefit-type-medical-benefit", "a13-benefit-design-part-b", "a13-step-jcode",
    "a13-actor-buy-and-bill", "a13-system-buy-bill",
    "meridian-a04-explicit-exclusion-row", "meridian-a04-p62-scoped-in", "meridian-c01-sse-s3-digit",
    "meridian-a11-phase-2", "meridian-a11-cc21-critical", "meridian-a05-mercury-partial",
    "meridian-a09-empty-matrix",
    "castellan-a11-phase-1-million", "castellan-a09-3-years", "castellan-a05-four-of-five",
    "castellan-a05-rapid-is-not-api", "castellan-c02-empty-source-system", "castellan-a13-empty",
    "solvara-a12-15sps-q3", "solvara-c01-copay-to-government", "solvara-a15-180k-wac",
    "solvara-a12-table-sps", "solvara-a05-environment-iro", "solvara-a03-empty-segment",
    "solvara-a14-hyphenated",
    "bluestem-a05-street-price-reset", "bluestem-c07-virtual-kitchen", "bluestem-c07-empty",
    "bluestem-a03-incentive-rebates", "bluestem-c07-ozarks-territory", "bluestem-a07-generic-csv",
    "bluestem-a05-negative-elasticity")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def fuzzy_present(quote: str, text: str) -> str | None:
    """Substring after whitespace-normalization, else a 70% token-overlap window (paper rule)."""
    q, t = _norm(quote), _norm(text)
    if q in t:
        return "exact"
    qt = [w for w in re.findall(r"[a-z0-9$%.]+", q) if len(w) > 2]
    if not qt:
        return None
    hits = sum(1 for w in qt if w in t)
    return "fuzzy" if hits / len(qt) >= 0.7 else None


def _specs(auto: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    E.walk_auto(auto, out.append)
    return out


def _regexes(spec: dict[str, Any]) -> list[str]:
    rx = [spec.get(k) for k in ("regex", "context_regex", "follow_regex", "mention_regex", "allow_regex")]
    for k in ("where", "require", "unless"):
        if isinstance(spec.get(k), dict) and spec[k].get("regex"):
            rx.append(spec[k]["regex"])
    return [r for r in rx if r]


# ---------------------------------------------------------------------------
# registry-only lints (8-10)
# ---------------------------------------------------------------------------


def lint_absence_scoping(check: dict[str, Any], sdir: Path) -> list[str]:
    errs = []
    unscoped = [s for s in _specs(check["auto"]) if s.get("op") == "text_absent"]
    if not unscoped:
        return errs
    ban = check.get("house_style_ban")
    if not isinstance(ban, dict) or not ban.get("source") or not ban.get("quote"):
        errs.append(f"{check['check_id']}: unscoped whole-file text_absent {[s['tokens'] for s in unscoped]} "
                    "without a source-backed house_style_ban (a correct rejection that names the token fails)")
        return errs
    src = sdir / ban["source"]
    if not src.exists():
        errs.append(f"{check['check_id']}: house_style_ban source missing: {ban['source']}")
    elif fuzzy_present(ban["quote"], src.read_text(errors="replace")) != "exact":
        errs.append(f"{check['check_id']}: house_style_ban quote not found in {ban['source']}")
    return errs


def lint_non_vacuity(check: dict[str, Any]) -> list[str]:
    errs = []

    def visit(spec, siblings):
        op = spec.get("op")
        if op in ("text_absent", "structured_text_absent"):
            f = spec.get("file")
            gated = any(s is not spec and s.get("op") in POSITIVE_GATES and s.get("file") == f
                        and not (s.get("op") == "structured_path" and not s.get("min_count"))
                        for s in siblings)
            if not gated:
                errs.append(f"{check['check_id']}: absence op {op} on {f} has no positive gate on the same file "
                            "(passes on an empty or hollow deliverable)")
        if op in ("all_of",):
            for s in spec["of"]:
                visit(s, spec["of"])
        elif op in ("any_of", "k_of"):
            for s in spec["of"]:
                visit(s, [s])

    visit(check["auto"], [check["auto"]])
    return errs


_BAND_RE = re.compile(r"\[\s*(-?[\d.,]+)\s*,\s*(-?[\d.,]+)\s*\]")
_KOFM_RE = re.compile(r"at least (\d+|one|two|three|four|five|six) of (?:the )?(\d+|two|three|four|five|six|seven|eight|nine|ten)\b", re.I)
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _num(s: str) -> float:
    return float(_WORDS.get(s.lower(), s.replace(",", "")))


def lint_score_text_parity(check: dict[str, Any]) -> list[str]:
    errs = []
    specs = _specs(check["auto"])
    bands = [(float(s["lo"]), float(s["hi"])) for s in specs if "lo" in s and "hi" in s]
    s1 = check.get("score_1_criteria", "")
    s0 = check.get("score_0_criteria", "")
    desc = check.get("check_description", "")
    cid = check["check_id"]
    for m in _BAND_RE.finditer(s1):
        lo, hi = _num(m.group(1)), _num(m.group(2))
        ok = any(abs(lo * k - a) < 1e-6 and abs(hi * k - b) < 1e-6 for a, b in bands for k in (1, 1e3, 1e6))
        if not ok:
            errs.append(f"{cid}: score_1 band [{m.group(1)},{m.group(2)}] matches no op band {bands}")
    for m in _KOFM_RE.finditer(desc):
        k, n = _num(m.group(1)), _num(m.group(2))
        ok = any((s.get("op") == "k_of" and s.get("min_pass") == k and len(s["of"]) == n)
                 or (abs(s.get("min_fraction", -1) - k / n) < 1e-6) for s in specs)
        if not ok:
            errs.append(f"{cid}: description says 'at least {m.group(1)} of {m.group(2)}' but no op threshold is {int(k)}/{int(n)}")
    if re.search(r"marked partial|partial/implemented", s0, re.I) or re.search(r"status\s*=\s*gap\b(?! or partial)", s1, re.I):
        for s in specs:
            req = (s.get("require") or {}).get("in") or []
            if s.get("op") == "csv_condition" and "partial" in [str(x).lower() for x in req]:
                errs.append(f"{cid}: score text fails 'partial' (or demands status=gap) but the op accepts partial")
    has_polarity = any(s.get("op") == "structured_polarity" for s in specs)
    for s in specs:
        if s.get("op") == "text_contains_any" and str(s.get("file", "")).endswith((".yaml", ".yml", ".json", ".csv")):
            bare = [t for t in s.get("tokens", []) if t.lower() in POLARITY_SENSITIVE]
            if bare and not has_polarity:
                errs.append(f"{cid}: polarity-blind token(s) {bare} credit a structured deliverable that may ASSERT the "
                            "opposite (e.g. copay offered to government-insured)")
    return errs


def undisclosed_literals(check: dict[str, Any], with_pass_condition: bool = False) -> list[str]:
    """Matcher literals (tokens, required values, bands) absent from the CHECKLIST text of the check.

    ``with_pass_condition``: the text includes the check's PASSES WHEN line (its score_1_criteria),
    as new runs render it; otherwise only the id/title/description line (the as-run rendering).
    """
    prose = (check["check_id"] + " " + check.get("title", "") + " " + check.get("check_description", "")
             + (" " + check.get("score_1_criteria", "") if with_pass_condition else "")).lower()
    out = []
    for s in _specs(check["auto"]):
        for t in (s.get("tokens") or []) + (s.get("required_values") or []):
            if str(t).lower() not in prose:
                out.append(f"{s['op']}:{t}")
        for k in ("lo", "hi"):
            if k in s:
                v = s[k]
                forms = {str(v), f"{v:,}"}
                if float(v).is_integer():
                    forms |= {str(int(v)), f"{int(v):,}"}
                if not any(f.lower() in prose for f in forms):
                    out.append(f"{s['op']}:{k}={v}")
        for r in _regexes(s):
            out.append(f"{s['op']}:regex")
    return out


# ---------------------------------------------------------------------------
# per-scenario validation
# ---------------------------------------------------------------------------


def validate_scenario(sid: str, ruleset: str = E.RULESET_V1) -> tuple[list[str], list[str], dict[str, Any]]:
    sdir = D.scenario_dir(sid)
    pdir = D.private_scenario_dir(sid)
    meta = D.scenario_meta(sid)
    deliverables = set(meta["deliverables"])
    errors: list[str] = []
    warnings: list[str] = []
    info: dict[str, Any] = {}
    canonical = [sdir / "scenario_overview.md", sdir / "tasks" / "t1.md", D.registry_path(sid, ruleset),
                 pdir / "answer_key" / "blueprint.json", pdir / "answer_key" / "anchors.json",
                 pdir / "answer_key" / "contradictions.json"]
    for f in canonical:
        if not f.exists():
            errors.append(f"missing canonical file: {f.name}")
    if errors:
        return errors, warnings, info
    eng_ops = E.Engine(ruleset).ops
    bp = D.blueprint(sid)
    anchors = D.anchors(sid)
    contradictions = D.answer_key(sid, "contradictions")
    task_text = D.task_brief(sid)

    # -- 1. registry
    checks = []
    for i, line in enumerate(D.registry_path(sid, ruleset).read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            checks.append(json.loads(line))
        except Exception as e:  # noqa: BLE001
            errors.append(f"registry line {i}: unparseable ({e})")
    n_auto = 0
    for c in checks:
        cid = c.get("check_id", "?")
        for field in ("check_id", "task_id", "check_description", "score_1_criteria", "score_0_criteria", "scoring_type"):
            if not c.get(field):
                warnings.append(f"{cid}: missing rubric field {field}")
        if not c.get("auto"):
            continue
        n_auto += 1
        for spec in _specs(c["auto"]):
            op = spec.get("op")
            if op not in eng_ops:
                errors.append(f"{cid}: op {op!r} unknown to the {ruleset} engine")
                continue
            missing = OP_REQUIRED_ARGS.get(op, set()) - set(spec)
            if missing:
                errors.append(f"{cid}: op {op} missing args {sorted(missing)}")
            f = spec.get("file")
            if f and f not in deliverables:
                errors.append(f"{cid}: auto references non-deliverable file {f!r}")
            if "lo" in spec and "hi" in spec and spec["lo"] > spec["hi"]:
                errors.append(f"{cid}: band lo>hi")
            for rx in _regexes(spec):
                try:
                    re.compile(rx)
                except re.error as e:
                    errors.append(f"{cid}: bad regex {rx!r} ({e})")
        # -- 8-10. new lints
        errors += lint_absence_scoping(c, sdir)
        errors += lint_non_vacuity(c)
        errors += lint_score_text_parity(c)
    if n_auto < 11:
        errors.append(f"only {n_auto} auto checks (contract: >=11)")
    info["auto_checks"] = n_auto
    und = {c["check_id"]: undisclosed_literals(c) for c in checks if c.get("auto")}
    und_pc = {c["check_id"]: undisclosed_literals(c, with_pass_condition=True) for c in checks if c.get("auto")}
    info["undisclosed_matcher_literals"] = sum(len(v) for v in und.values())
    info["undisclosed_matcher_literals_with_pass_conditions"] = sum(len(v) for v in und_pc.values())
    info["fully_disclosed_checks"] = sum(1 for v in und.values() if not v)
    info["fully_disclosed_checks_with_pass_conditions"] = sum(1 for v in und_pc.values() if not v)
    info["undisclosed_by_check"] = {cid: [len(und[cid]), len(und_pc[cid])] for cid in und}

    # -- 2/3. anchors
    for aid, a in anchors.items():
        band = a.get("band")
        v = a.get("value")
        if not (isinstance(band, list) and len(band) == 2):
            errors.append(f"anchor {aid}: malformed band")
            continue
        if isinstance(v, (int, float)) and not (band[0] <= v <= band[1]):
            errors.append(f"anchor {aid}: value {v} outside its own band {band}")
        for src in a.get("sources", []):
            fname = re.split(r"\s+—|\s+-\s|:", src)[0].strip()
            if fname and not (sdir / fname).exists():
                warnings.append(f"anchor {aid}: source path not on disk: {fname}")
    for hid in bp.get("headline_figures", []):
        if hid not in anchors:
            errors.append(f"headline figure {hid} not in anchors.json")
    if list(bp.get("headline_figures", [])) != D.headline_figures(sid):
        errors.append("manifest headline_figures differ from blueprint.json")
    for c in checks:
        for f in c.get("files_needed", []):
            if not (sdir / f).exists():
                warnings.append(f"{c.get('check_id')}: files_needed missing on disk: {f}")

    # -- 4. file plan
    planned = {e["path"] for e in bp.get("file_plan", [])}
    on_disk = {str(p.relative_to(sdir)) for p in (sdir / "sources").rglob("*") if p.is_file()}
    for p in sorted(planned - on_disk):
        errors.append(f"planned source missing: {p}")
    for p in sorted(on_disk - planned):
        warnings.append(f"stray source not in file_plan: {p}")

    # -- 5. contradiction quotes
    for con in contradictions:
        for side in ("claim_a", "claim_b"):
            cl = con.get(side) or {}
            f, q = cl.get("file"), cl.get("quote")
            if not f or not q:
                warnings.append(f"contradiction {con.get('id')}: {side} missing file/quote")
                continue
            fp = sdir / f
            if not fp.exists():
                errors.append(f"contradiction {con.get('id')}: {side} file missing: {f}")
                continue
            mode = fuzzy_present(q, fp.read_text(errors="replace"))
            if mode is None:
                errors.append(f"contradiction {con.get('id')}: {side} quote NOT FOUND in {f}")
            elif mode == "fuzzy":
                warnings.append(f"contradiction {con.get('id')}: {side} quote only fuzzy-matches {f}")

    # -- 6. leakage (agent-visible files only)
    scan = [sdir / "scenario_overview.md", sdir / "tasks" / "t1.md", sdir / "CHECKLIST.md"] + D.source_files(sid)
    for p in scan:
        if not p.is_file():
            continue
        low = p.read_text(errors="replace").lower()
        for tok in FORBIDDEN:
            if tok in low:
                errors.append(f"leakage: {tok!r} in {p.relative_to(sdir)}")

    # -- 7. cross-file
    for d in (meta["prose_deliverable"], meta["structured_deliverable"]):
        if d not in task_text:
            errors.append(f"deliverable {d} not named in tasks/t1.md")

    # -- 12. checklist sync (agent-visible text is always rendered from v1.0: PASSES WHEN lines by
    #        default; the regime opt-out stages the as-run rendering, linted here too)
    reg_v1 = D.registry(sid, E.RULESET_V1)
    rendered = E.render_checklist_v1(reg_v1, pass_conditions=True)
    if (sdir / "CHECKLIST.md").read_text() != rendered:
        errors.append("CHECKLIST.md is out of sync with registry_v1.0.jsonl (re-render it with "
                      "engine.render_checklist_v1)")
    low = E.render_checklist_v1(reg_v1, pass_conditions=False).lower()
    for tok in FORBIDDEN:
        if tok in low:
            errors.append(f"leakage: {tok!r} in the opt-out CHECKLIST.md rendering (no pass conditions)")
    ids = [c["check_id"] for c in D.raw_checks(sid, E.RULESET_V1)]
    if ids != D.checklist_ids(sid):
        errors.append("manifest checklist_ids differ from registry_v1.0")
    return errors, warnings, info


# ---------------------------------------------------------------------------
# probes (11)
# ---------------------------------------------------------------------------


def load_probes() -> list[dict[str, Any]]:
    return json.loads(D.private("probes.json").read_text())["probes"]


def _leg(auto: dict[str, Any], path: list[int] | None) -> dict[str, Any]:
    spec = auto
    for i in path or []:
        spec = spec["of"][i]
    return spec


def _leg_path(p: dict[str, Any], ruleset: str) -> list[int] | None:
    """The probe's leg for this ruleset; as-run shares the paper registry layout (v0.1)."""
    legs = p.get("leg") or {}
    if ruleset in legs:
        return legs[ruleset]
    if ruleset == E.RULESET_AS_RUN:
        return legs.get(E.RULESET_PAPER)
    return None


def run_probe(p: dict[str, Any], ruleset: str) -> bool:
    reg = {c["id"]: c for c in D.registry(p["scenario"], ruleset)}
    spec = _leg(reg[p["check_id"]]["auto"], _leg_path(p, ruleset))
    with tempfile.TemporaryDirectory(prefix="dbwm_e5probe_") as td:
        for name, body in p["files"].items():
            (Path(td) / name).write_text(body)
        ok, _m = E.Engine(ruleset).run_op(E.Submission(td), spec)
    return bool(ok)


def run_probes(ruleset: str) -> list[dict[str, Any]]:
    out = []
    for p in load_probes():
        got = run_probe(p, ruleset)
        out.append({"id": p["id"], "check_id": p["check_id"], "got": got, "correct": p["correct"],
                    "expected": p["expect"][ruleset], "origin": p.get("origin")})
    return out


def validate_all(ruleset: str = E.RULESET_V1, scenario: str | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {"ruleset": ruleset, "scenarios": {}}
    for sid in ([scenario] if scenario else D.SCENARIOS):
        e, w, info = validate_scenario(sid, ruleset)
        report["scenarios"][sid] = {"errors": e, "warnings": w, "info": info}
    probes = run_probes(ruleset)
    drift = [r for r in probes if r["got"] != r["expected"]]
    wrong = [r for r in probes if r["got"] != r["correct"]]
    report["probes"] = {"n": len(probes), "pinned_mismatch": [r["id"] for r in drift],
                        "misgraded": [r["id"] for r in wrong],
                        "misgraded_map20": sum(1 for r in wrong if r["origin"] == "map-2026-10-01"),
                        "map20": sum(1 for r in probes if r["origin"] == "map-2026-10-01")}
    canary = D.private("CANARY.txt")
    report["canary_ok"] = canary.exists() and CANARY_GUID in canary.read_text()
    report["ok"] = (not any(v["errors"] for v in report["scenarios"].values()) and not drift
                    and report["canary_ok"] and (ruleset != E.RULESET_V1 or not wrong))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ruleset", default=E.RULESET_V1, choices=list(E.RULESETS))
    ap.add_argument("--scenario")
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    rep = validate_all(args.ruleset, args.scenario)
    for sid, r in rep["scenarios"].items():
        st = "FAIL" if r["errors"] else "OK"
        info = r["info"]
        print(f"== {sid} [registry {D.REGISTRY_VERSION[args.ruleset]}]: {st} ({len(r['errors'])} errors, "
              f"{len(r['warnings'])} warnings; matcher literals not in the checklist text: "
              f"{info.get('undisclosed_matcher_literals', 0)} with descriptions only, "
              f"{info.get('undisclosed_matcher_literals_with_pass_conditions', 0)} with PASSES WHEN lines)")
        for e in r["errors"]:
            print(f"   ERROR {e}")
        for w in r["warnings"]:
            print(f"   warn  {w}")
    p = rep["probes"]
    print(f"== probes: {p['n']} run; {len(p['misgraded'])} graded incorrectly under {args.ruleset} "
          f"({p['misgraded_map20']} of the {p['map20']} mapping-audit probes); pinned-grade drift: {p['pinned_mismatch'] or 'none'}")
    if not rep["canary_ok"]:
        print("   ERROR private/eval5/CANARY.txt missing or without the canary GUID")
    if args.json:
        Path(args.json).write_text(json.dumps(rep, indent=2) + "\n")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
