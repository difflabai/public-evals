"""``dbwm selftest --eval 4``: offline checks of the eval-4 instrument.

No LLM, no network. A check that cannot run because an optional piece is absent
(the fetched dataset, the ``formulas`` recalc package) is SKIPPED with the
reason -- never reported ok. A missing vendored fixture is a FAILED check.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from dbwm.core.types import CANARY_GUID, Cell, CheckResult, Isolation, OracleContext, RULESET_PAPER, RULESET_V1

from . import _paths, checker, dataset, oracle, stub
from .arms import ARM_ORDER, ARMS, ARMS_WITH_CHECKLIST, ARMS_WITH_TOOL, BUILTINS

# sha256 of CHECKLIST.md as the paper staged it (stdout of the paper harness's
# `checker.py --mode prose`, computed from the frozen harness snapshot).
PAPER_CHECKLIST_SHA256 = "71c8e823175e889616aa40279ebcbd80331c298fee3afffcd61f27e5f5f13c6b"

# Substrings that would mean checker SOURCE (or its literal token lists and
# thresholds) reached the sandbox. CHECKLIST.md legitimately states the numeric
# anchors; none of these appear in it.
CHECKER_SOURCE_FINGERPRINTS = (
    "def k_scenarios", "RECOMMENDATION_TOKENS", "UK_SPELLINGS", "GENERIC_SCENARIO", "K_TO_RUBRIC",
    "formula_count >= 50", "ratio >= 0.25", "src_cites >= 8", "vintage >= 8", "_qa_impl",
    "verdict_lock_source", "find_number(", "class Corpus",
)
FORBIDDEN_IN_TASK = ("private/", "ANSWER_KEY", "reference.json", "checks.jsonl", "grading_rubric",
                     "judge_system", "aa-briefcase-lite", "AA-Briefcase")


def _ok(name: str, fn: Callable[[], tuple[bool, str]]) -> CheckResult:
    try:
        ok, detail = fn()
        return CheckResult(name, bool(ok), detail)
    except Exception as e:  # noqa: BLE001 - a crash is a failure, never a skip
        return CheckResult(name, False, f"{type(e).__name__}: {e}")


def _skip(name: str, why: str) -> CheckResult:
    return CheckResult(name, False, f"SKIPPED: {why}", skipped=True)


def recalc_check() -> CheckResult:
    """The ``formulas`` line of the selftest: a recalculation SMOKE, not an import
    check (review M4). An install whose scipy cannot load imports fine but fails on
    every workbook that uses a function; that is reported as INSTALLED BUT UNUSABLE
    with the cause, never as ok (and, like any unusable optional extra, SKIPPED
    rather than FAIL: BUILD-SPEC item 7)."""
    name = "formula recalculation works ('formulas')"
    status, err = checker.recalc_smoke()
    if status == checker.SMOKE_OK:
        return CheckResult(name, True, "recalculated =SUM(A1,A1)*3 on a 2-cell workbook")
    if status == checker.SMOKE_MISSING:
        return _skip(name, "not installed: openpyxl-written workbooks carry no cached values, so the checker cannot "
                           "see their computed outputs (v1 marks them unscorable); pip install 'diff-bwm-1[recalc]'")
    return _skip(name, f"INSTALLED BUT UNUSABLE: a 2-cell recalculation failed ({err}). Fix the environment "
                       "(scipy must import; e.g. reinstall scipy). Until then v1 marks such workbooks unscorable, "
                       "qa_check says computed figures were not checked, and the paper re-scores that need "
                       "recalculation are skipped")


def _ctx(cell: Cell, sandbox: Path) -> OracleContext:
    arm = ARMS[cell.arm]
    return OracleContext(cell=cell, arm=arm, mode=arm.oracle_mode, sandbox=sandbox, state={})


def direct_call_oracle(cell: Cell, sandbox: Path) -> Callable[[str, dict], Any]:
    """call_oracle wired straight to this eval's handlers (no server), for tests.

    Mirrors the server's contract: unknown op -> error; payload keys never
    reach the mode (the handler reads ctx.mode only)."""
    handlers = oracle.handlers_for(ARMS[cell.arm].oracle_mode)
    ctx = _ctx(cell, sandbox)

    def call(op: str, payload: dict | None = None) -> Any:
        if op not in handlers:
            raise KeyError(f"unknown tool: {op}")
        return handlers[op](dict(payload or {}), ctx)

    call.ctx = ctx  # type: ignore[attr-defined]
    return call


def staging_problems(eval_def, cell: Cell) -> list[str]:
    """Leak checks over a cell's staging manifest + prompt (no sandbox needed)."""
    from dbwm.core import canary

    probs: list[str] = []
    man = eval_def.stage(cell)
    dsts = [f.dst for f in man.files]
    srcs = sorted(d for d in dsts if d.startswith(("shared/", "week/")))
    if len(srcs) != 147:
        probs.append(f"{len(srcs)} source files staged, expected 147")
    extra = sorted(set(dsts) - set(srcs))
    want_extra = ["CHECKLIST.md"] if cell.arm in ARMS_WITH_CHECKLIST else []
    if extra != want_extra:
        probs.append(f"non-source staged files {extra}, expected {want_extra}")
    names = {n.lower() for n in eval_def_scorer_only(eval_def)}
    for d in dsts:
        for seg in Path(d).parts:
            stem = seg.lower()
            if stem in names or stem.rsplit(".", 1)[0] in names:
                probs.append(f"scorer-only name staged: {d}")
    values = eval_def.answer_key_values(cell)
    texts = {"TASK.md": man.task_md, "prompt": eval_def.prompt(cell)}
    for f in man.files:
        if f.content is not None:
            texts[f.dst] = f.content.decode("utf-8", "replace")
    for label, t in texts.items():
        if CANARY_GUID in t:
            probs.append(f"canary in {label}")
        for fp in CHECKER_SOURCE_FINGERPRINTS:
            if fp in t:
                probs.append(f"checker-source fingerprint {fp!r} in {label}")
        for v in canary.scan_text_for_values(t, values):
            probs.append(f"answer-key value {v!r} in {label}")
    from dbwm.core.sandbox import check_text

    for label in ("TASK.md", "prompt"):
        t = texts[label]
        probs += check_text(t, label)  # the sandbox pre-check's own rules
        for bad in FORBIDDEN_IN_TASK:
            if bad in t:
                probs.append(f"{bad!r} in {label}")
    for f in man.files:
        if f.src is not None:
            if canary.scan_for_guid(Path(f.src)):
                probs.append(f"canary in {f.dst}")
            for _lbl, v in canary.scan_for_values(Path(f.src), values):
                probs.append(f"answer-key value {v!r} in {f.dst}")
            data = Path(f.src).read_bytes()
            for fp in CHECKER_SOURCE_FINGERPRINTS:
                if fp.encode() in data:
                    probs.append(f"checker-source fingerprint {fp!r} in {f.dst}")
    return probs


def eval_def_scorer_only(eval_def) -> tuple[str, ...]:
    import dbwm.eval4 as pkg

    return tuple(getattr(pkg, "SCORER_ONLY_NAMES", ()))


def run_stub_cell(eval_def, cell: Cell, root: Path) -> tuple[Any, Any, dict]:
    """stub_agent into ``root/sandbox`` with call_oracle wired to the handlers,
    copy the deliverables, score both rulesets. -> (v1 score, paper score, oracle state)."""
    sb = root / "sandbox"
    sb.mkdir(parents=True, exist_ok=True)
    call = direct_call_oracle(cell, sb)
    eval_def.stub_agent(cell, sb, call)
    deliv = root / "deliverables"
    deliv.mkdir(exist_ok=True)
    for name in eval_def.deliverables:
        if (sb / name).is_file():
            shutil.copy2(sb / name, deliv / name)
    return (eval_def.score(deliv, cell, RULESET_V1), eval_def.score(deliv, cell, RULESET_PAPER),
            call.ctx.state)  # type: ignore[attr-defined]


def selftest(eval_def) -> list[CheckResult]:
    out: list[CheckResult] = []
    have_data = dataset.is_fetched("data")
    have_private = dataset.is_fetched("private")

    def arms_check():
        bad = []
        for name in ARM_ORDER:
            a = ARMS[name]
            if a.tools.builtin != BUILTINS:
                bad.append(f"{name} builtins {sorted(a.tools.builtin)}")
            if a.min_isolation != Isolation.CONTAINER:
                bad.append(f"{name} min_isolation {a.min_isolation}")
            ops = [t.name for t in a.tools.oracle_tools]
            if ops != (["qa_check"] if name in ARMS_WITH_TOOL else []):
                bad.append(f"{name} oracle tools {ops}")
            if a.oracle_mode != {"checklist-verdict": "verdict", "checklist-executable": "figures"}.get(name):
                bad.append(f"{name} oracle_mode {a.oracle_mode}")
        return not bad, "; ".join(bad) or "4 arms: bash+file tools, CONTAINER, qa_check sealed verdict/figures"
    out.append(_ok("arms and tool policies", arms_check))

    def registry():
        ids = checker.CHECK_IDS
        sha = hashlib.sha256(checker.checklist_md_bytes()).hexdigest()
        ok = len(ids) == 12 and len(set(ids)) == 12 and sha == PAPER_CHECKLIST_SHA256
        return ok, f"12 K-checks; CHECKLIST.md sha256 {sha[:12]}... (paper {PAPER_CHECKLIST_SHA256[:12]}...)"
    out.append(_ok("checker registry and CHECKLIST.md golden", registry))

    if have_data or have_private:
        def verify():
            v = dataset.verify(tuple(s for s, h in (("data", have_data), ("private", have_private)) if h))
            return v["complete"], f"{v['ok']} files verified at {dataset.revision()[:12]}; " \
                                  f"missing {len(v['missing'])}, mismatched {len(v['mismatched'])}"
        out.append(_ok("dataset files match the pinned sha256 manifest", verify))
    else:
        out.append(_skip("dataset files match the pinned sha256 manifest", "SKIPPED-NO-DATASET: run `dbwm fetch eval4`"))

    if have_data:
        def staging():
            probs = []
            for arm in ARM_ORDER:
                probs += [f"{arm}: {p}" for p in staging_problems(eval_def, Cell("eval4", "w1_t2", arm, 0))]
            return not probs, "; ".join(probs[:5]) or "4 arms: 147 sources (+CHECKLIST.md), no checker source, " \
                                                      "no scorer-only names, no answer-key values, no canary"
        out.append(_ok("staging manifests leak-free (every arm)", staging))
    else:
        out.append(_skip("staging manifests leak-free (every arm)", "SKIPPED-NO-DATASET: run `dbwm fetch eval4`"))

    def oracle_modes():
        tmp = Path(tempfile.mkdtemp(prefix="dbwm_eval4_st_"))
        try:
            stub.build_workbook(tmp / "market_model.xlsx", {"uk_spelling"})
            cv = Cell("eval4", "w1_t2", "checklist-verdict", 0)
            ce = Cell("eval4", "w1_t2", "checklist-executable", 0)
            tv = oracle.qa_check({"mode": "figures", "path": "/etc/passwd"}, _ctx(cv, tmp))
            tf = oracle.qa_check({}, _ctx(ce, tmp))
            ok = ("CHECKLIST RESULT: 11/12 PASS" in tv and "criteria:" not in tv and "uk_spellings" not in tv
                  and "criteria:" in tf and "uk_spellings" in tf and "FAIL  K-LANG" in tv)
            (tmp / "market_model.xlsx").unlink()
            (tmp / "market_model.xlsx").symlink_to(tmp / "elsewhere.xlsx")
            try:
                oracle.qa_check({}, _ctx(cv, tmp))
                ok = False
            except oracle.QACheckError:
                pass
            return ok, "verdict mode prints PASS/FAIL only (payload mode ignored); figures adds measurements; " \
                       "symlinked workbook refused"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    out.append(_ok("qa_check oracle: sealed modes", oracle_modes))

    def scorer_goldens():
        tmp = Path(tempfile.mkdtemp(prefix="dbwm_eval4_st_"))
        try:
            bad = []
            for defects in [set()] + [{d} for d in stub.DEFECT_ORDER]:
                wb = tmp / "market_model.xlsx"
                stub.build_workbook(wb, defects)
                res, _ = checker.evaluate(wb)
                want = {stub.DEFECTS[d] for d in defects}
                if set(res["summary"]["failed_ids"]) != want:
                    bad.append(f"{sorted(defects)} -> {res['summary']['failed_ids']}")
            return not bad, "; ".join(bad) or f"clean workbook 12/12; each of {len(stub.DEFECT_ORDER)} defects " \
                                               "fails exactly its K-check"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    out.append(_ok("checker known-good / known-bad workbooks", scorer_goldens))

    def stub_e2e():
        rows = []
        bad = []
        for arm in ARM_ORDER:
            cell = Cell("eval4", "w1_t2", arm, 0)
            tmp = Path(tempfile.mkdtemp(prefix="dbwm_eval4_st_"))
            try:
                s1, sp, state = run_stub_cell(eval_def, cell, tmp)
                calls = (state.get("qa_check") or {}).get("calls", 0)
                rows.append(f"{arm}: success={s1.success} overclaim={s1.overclaim} qa_check={calls}")
                if arm in ARMS_WITH_TOOL and (calls != 2 or s1.success is not True or s1.overclaim):
                    bad.append(arm)
                if arm == "baseline" and (s1.success is not False or s1.overclaim is not True):
                    bad.append(arm)
                if s1.ruleset != RULESET_V1 or sp.ruleset != RULESET_PAPER:
                    bad.append(f"{arm} ruleset")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        return not bad, "; ".join(rows)
    out.append(_ok("stub agent end to end (direct oracle) for every arm", stub_e2e))

    if have_private:
        def judge_mock():
            from . import judge as j
            tmp = Path(tempfile.mkdtemp(prefix="dbwm_eval4_st_"))
            try:
                stub.build_workbook(tmp / "market_model.xlsx", set())
                mj = j.mock_judge(lambda s, u, sc, m: {"passed": "A-07" not in u.split("</description>")[0],
                                                       "reasoning": "mock"})
                r = j.judge_workbook(tmp / "market_model.xlsx", mj, "mock-model", 0, retries=0)
                return r["total"] == 12 and r["errors"] == 0, f"{r['passed']}/{r['total']} (mock), " \
                                                               f"{r['artifact_chars']} chars serialized"
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        out.append(_ok("judge pipeline with a mock judge (12 rubric rows)", judge_mock))
    else:
        out.append(_skip("judge pipeline with a mock judge (12 rubric rows)", "SKIPPED-NO-DATASET: run `dbwm fetch eval4`"))

    out.append(recalc_check())

    def canary_file():
        p = _paths.private("CANARY.txt")
        from dbwm.core.types import CANARY_STRING
        return p.is_file() and CANARY_STRING in p.read_text(encoding="utf-8"), str(p.relative_to(_paths.PKG_ROOT))
    out.append(_ok("private/eval4/CANARY.txt carries the canary", canary_file))
    return out


def main() -> int:
    from . import EVAL

    fails = 0
    for c in selftest(EVAL):
        tag = "SKIPPED" if c.skipped else ("ok" if c.ok else "FAIL")
        fails += (not c.ok and not c.skipped)
        print(f"[{tag}] {c.name}: {c.detail}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
