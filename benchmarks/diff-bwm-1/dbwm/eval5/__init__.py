"""Eval 5: the checklist-form manipulation on open-ended consulting work (decidable oracle).

Four authored, text-only consulting scenarios (solvara-market-access,
meridian-soc2, castellan-software, bluestem-demand). The agent reads ~30
deliberately contradictory sources and writes a prose deliverable, a
structured deliverable (YAML / CSV / JSON) and claims.json; a deterministic
DSL checker grades 63 machine-checkable items (15-16 per scenario). Arms vary
the FORM of the QA criteria: none (baseline), prose CHECKLIST.md, or the same
CHECKLIST.md plus a harness-side check that returns PASS/FAIL only (verdict)
or PASS/FAIL plus measured figures (executable). New runs' CHECKLIST.md states
each item's pass condition (``PASSES WHEN``) in all three checklist arms;
``regime.extra["eval5"]["checklist_pass_conditions"] = false`` stages the
as-run one-line-per-item rendering instead.

Rulesets: ``paper`` (paper engine + registry v0.1 with the lab's 2026-10-03
SOLVARA-A-13 fix), ``as-run`` (paper engine + registry v0.1 as the paper's
sessions were scored) and ``v1`` (release engine + registry v1.0).

Modules: engine (DSL, rulesets paper/as-run/v1), dataset, validate (lints + probes),
prompt, arms, oracle (sealed qa_check), score, stubs, report, elo/ (pairwise
judge + ladder).
Card: docs/evals/eval5.md. Changes vs the paper instrument:
change-ledger/eval5.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from dbwm.core.types import (CANARY_GUID, RULESET_AS_RUN, RULESET_PAPER, RULESET_V1, Cell, CheckResult,
                             RegimeConfig, ScoreResult, StagedFile, StagingManifest)

from . import dataset as D
from . import engine as E
from .arms import ARM_ORDER, ARMS, ARMS_WITH_CHECKLIST
from .oracle import handlers_for
from .prompt import FIRST_PROMPT, task_md_v1

# Scorer-only names: a tool_use input naming any of these as a path segment is an
# audit finding (dbwm.core.audit). The core defaults plus eval 5's own files.
SCORER_ONLY_NAMES = (
    "reference.json", "world.json", "ANSWER_KEY", "checks.jsonl", "answer_key", "grader", "private",
    "registry_v0.1.jsonl", "registry_v1.0.jsonl", "blueprint.json", "anchors.json", "contradictions.json",
    "probes.json", "as-run-pins.json", "CANARY.txt", "_qa_registry.json", "_qa_impl.py", "criteria_registry.jsonl",
    "registry_v0.1-fixed.jsonl",
)

# regime.extra["eval5"]["checklist_pass_conditions"]: default on (PLAN D-3 / E5-48).
REGIME_EXTRA_KEY = "eval5"
PASS_CONDITIONS_KEY = "checklist_pass_conditions"


def checklist_pass_conditions(regime: RegimeConfig | None) -> bool:
    """The regime's checklist setting: True (default) = CHECKLIST.md carries PASSES WHEN lines.

    A malformed setting raises RegimeError (a ValueError), which ``dbwm run`` reports as a usage error.
    """
    from dbwm.core.regime import RegimeError

    extra = (getattr(regime, "extra", None) or {}).get(REGIME_EXTRA_KEY) if regime is not None else None
    if extra is None:
        return True
    if not isinstance(extra, dict):
        raise RegimeError(f"eval5: regime extra {REGIME_EXTRA_KEY!r} must be an object, got {extra!r}")
    unknown = sorted(set(extra) - {PASS_CONDITIONS_KEY})
    if unknown:
        raise RegimeError(f"eval5: unknown regime extra key(s) {REGIME_EXTRA_KEY}.{unknown} "
                          f"(known: {PASS_CONDITIONS_KEY})")
    v = extra.get(PASS_CONDITIONS_KEY, True)
    if not isinstance(v, bool):
        raise RegimeError(f"eval5: {REGIME_EXTRA_KEY}.{PASS_CONDITIONS_KEY} must be true or false, got {v!r}")
    return v


def _all_deliverables() -> list[str]:
    names: list[str] = []
    for s in D.manifest()["scenarios"]:
        for d in s["deliverables"]:
            if d not in names:
                names.append(d)
    return names


class Eval5:
    eval_id = "eval5"
    title = "Consulting oracle: checklist form on open-ended consulting deliverables"
    arms = ARMS
    # paper = registry v0.1 with the SOLVARA-A-13 fix; as-run = the scorer that ran the paper's sessions.
    rulesets = (RULESET_PAPER, RULESET_V1, RULESET_AS_RUN)

    def __init__(self) -> None:
        # Union over scenarios (each has its own prose/structured names plus claims.json);
        # the runner collects per cell via deliverables_for(), so another scenario's names
        # are never recorded as missing.
        self.deliverables = tuple(_all_deliverables())
        # Set from the regime by cells() (the runner calls it before staging any cell);
        # stage() and disclosed_values_for() have no regime argument.
        self.pass_conditions = True

    def deliverables_for(self, cell: Cell) -> list[str]:
        """This scenario's deliverables only (core hook runner.deliverables_for)."""
        self._check_cell(cell)
        for s in D.manifest()["scenarios"]:
            if s["id"] == cell.instance:
                return list(s["deliverables"])
        raise ValueError(f"eval5: scenario {cell.instance!r} missing from the dataset manifest")

    # -- design ----------------------------------------------------------------------

    def instances(self) -> list[str]:
        return list(D.SCENARIOS)

    def default_regime(self) -> dict[str, Any]:
        try:
            from dbwm.core.regime import load_regime_json
            return load_regime_json(self.eval_id)
        except Exception:  # noqa: BLE001 - core regime helper optional here
            return json.loads((Path(__file__).with_name("regime.json")).read_text())

    def cells(self, regime: RegimeConfig) -> list[Cell]:
        self.pass_conditions = checklist_pass_conditions(regime)
        insts = list(regime.instances) if regime.instances else self.instances()
        arms = list(regime.arms) if regime.arms else list(ARM_ORDER)
        for i in insts:
            if i not in D.SCENARIOS:
                raise ValueError(f"eval5: unknown instance {i!r}; choose from {list(D.SCENARIOS)}")
        for a in arms:
            if a not in ARMS:
                raise ValueError(f"eval5: unknown arm {a!r}; choose from {list(ARM_ORDER)}")
        return [Cell(self.eval_id, i, a, r) for r in range(regime.reps) for i in insts for a in arms]

    def _check_cell(self, cell: Cell) -> None:
        if cell.instance not in D.SCENARIOS or cell.arm not in ARMS:
            raise ValueError(f"eval5: bad cell {cell}")

    # -- sandbox ---------------------------------------------------------------------

    def stage(self, cell: Cell) -> StagingManifest:
        """Agent-visible files only: shared/, engagement/, and CHECKLIST.md for checklist arms.

        CHECKLIST.md is the dataset file (registry v1.0 with PASSES WHEN lines) or, when the
        regime turned pass conditions off, the as-run rendering of registry v1.0; either way
        the three checklist arms receive the same bytes.
        """
        self._check_cell(cell)
        sdir = D.scenario_dir(cell.instance)
        files: list[StagedFile] = []
        for top in ("shared", "engagement"):
            root = sdir / "sources" / top
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    files.append(StagedFile(dst=f"{top}/{p.relative_to(root).as_posix()}", src=p))
        if cell.arm in ARMS_WITH_CHECKLIST:
            if self.pass_conditions:
                files.append(StagedFile(dst="CHECKLIST.md", src=sdir / "CHECKLIST.md"))
            else:
                text = E.render_checklist_v1(D.registry(cell.instance, RULESET_V1), pass_conditions=False)
                files.append(StagedFile(dst="CHECKLIST.md", content=text.encode("utf-8")))
        return StagingManifest(files=files, task_md=task_md_v1(cell.instance, cell.arm), writable_dirs=[])

    def disclosed_values_for(self, cell: Cell) -> list[str]:
        """Core hook: the PASSES WHEN texts a checklist arm's CHECKLIST.md shows on purpose.

        They are registry v1.0 score_1 texts, which answer_key_values() lists as scorer-only
        text; an occurrence inside one of them (in the sandbox, or echoed by the agent) is
        disclosure by design, not a leak. Nothing is disclosed to the baseline arm or when
        the regime turned pass conditions off.
        """
        self._check_cell(cell)
        if cell.arm not in ARMS_WITH_CHECKLIST or not self.pass_conditions:
            return []
        return list(dict.fromkeys(E.pass_conditions(D.registry(cell.instance, RULESET_V1))))

    def prompt(self, cell: Cell) -> str:
        self._check_cell(cell)
        return FIRST_PROMPT

    def oracle_handlers(self, cell: Cell) -> dict:
        self._check_cell(cell)
        return handlers_for(ARMS[cell.arm].oracle_mode)

    # -- scoring ---------------------------------------------------------------------

    def score(self, deliverables_dir: Path, cell: Cell, ruleset: str) -> ScoreResult:
        from .score import score
        self._check_cell(cell)
        return score(deliverables_dir, cell, ruleset)

    def answer_key_values(self, cell: Cell) -> list[str]:
        """Scorer-only strings for this cell's leak scans (score.answer_key_values).

        A checklist arm whose CHECKLIST.md shows pass conditions is shown every value
        that occurs inside one of those PASSES WHEN texts, so such values are left out
        for that cell (the agent reads them anyway; their appearance elsewhere in the
        same sandbox or transcript reveals nothing more). The baseline arm, and every
        arm when the regime turns pass conditions off, keeps the full list. The shown
        texts are also reported through disclosed_values_for() (sandbox.json records them).
        """
        from .score import answer_key_values
        self._check_cell(cell)
        vals = answer_key_values(cell.instance)
        shown = self.disclosed_values_for(cell)
        if shown:
            vals = [v for v in vals if not any(v in d for d in shown)]
        return vals

    def stub_agent(self, cell: Cell, sandbox: Path, call_oracle: Callable[[str, dict], Any]) -> None:
        from .stubs import stub_agent
        self._check_cell(cell)
        stub_agent(cell, Path(sandbox), call_oracle)

    def report(self, run_dir: Path) -> dict[str, Any]:
        from .report import report
        return report(run_dir)

    # -- self test -------------------------------------------------------------------

    def selftest(self) -> list[CheckResult]:
        """Fast offline checks; no silent skips (missing PyYAML is reported as SKIPPED)."""
        from . import validate
        from .oracle import check_submission
        from .score import answer_key_values
        from .stubs import render
        import tempfile

        out: list[CheckResult] = []
        try:
            import yaml  # noqa: F401
            have_yaml = True
            out.append(CheckResult("pyyaml", True, "PyYAML importable (needed for pa_workflow.yaml)"))
        except ImportError:
            have_yaml = False
            out.append(CheckResult("pyyaml", False, "PyYAML missing: install the eval5 extra "
                                   "(pip install 'diff-bwm-1[eval5]'); YAML-dependent checks skipped", skipped=True))
        canary = D.private("CANARY.txt")
        out.append(CheckResult("canary", canary.exists() and CANARY_GUID in canary.read_text(),
                               "private/eval5/CANARY.txt carries the canary GUID"))
        for rs in (RULESET_V1, RULESET_AS_RUN, RULESET_PAPER):
            if not have_yaml:
                out.append(CheckResult(f"validate-{rs}", False, "needs PyYAML (probes parse YAML)", skipped=True))
                continue
            rep = validate.validate_all(rs)
            errs = {s: len(r["errors"]) for s, r in rep["scenarios"].items()}
            if rs == RULESET_V1:
                out.append(CheckResult("validate-v1.0", rep["ok"],
                                       f"errors {errs}; probes misgraded {rep['probes']['misgraded']}"))
            elif rs == RULESET_AS_RUN:
                named = ("SOLVARA-A-13", "MERIDIAN-A-04", "CASTELLAN-A-05", "MERIDIAN-A-05", "SOLVARA-C-01")
                flat = " ".join(e for r in rep["scenarios"].values() for e in r["errors"])
                missing = [n for n in named if n not in flat]
                out.append(CheckResult("validate-v0.1-flags-known-defects", not missing,
                                       f"v0.1 (as-run) lint errors {errs}; required ids missing from errors: "
                                       f"{missing or 'none'}"))
            else:
                p = rep["probes"]
                got = sorted(p["misgraded"])
                want = sorted(validate.PAPER_KNOWN_MISGRADES)
                out.append(CheckResult("validate-paper-known-misgrades", got == want and not p["pinned_mismatch"],
                                       f"oracle of record (v0.1 + lab A-13 fix) misgrades {len(got)} of {p['n']} "
                                       f"probes, exactly validate.PAPER_KNOWN_MISGRADES"
                                       if got == want else f"misgrades differ from PAPER_KNOWN_MISGRADES: "
                                       f"extra {sorted(set(got) - set(want))}, missing {sorted(set(want) - set(got))}"))
        for sid in D.SCENARIOS:
            with tempfile.TemporaryDirectory(prefix="dbwm_e5self_") as td:
                if not have_yaml and sid == "solvara-market-access":
                    out.append(CheckResult(f"strong-stub-{sid}", False, "needs PyYAML", skipped=True))
                    continue
                render(sid, "strong", td, RULESET_V1)
                res, _ = check_submission(sid, td)
                s = res["summary"]
                out.append(CheckResult(f"strong-stub-{sid}", s["passed"] == s["total"],
                                       f"v1 strong stub passes {s['passed']}/{s['total']} (every auto check satisfiable)"))
        from dbwm.core import canary as core_canary
        from . import validate as V
        leaks = []
        n_disclosed = 0
        saved = self.pass_conditions
        try:
            for pc in (True, False):
                self.pass_conditions = pc
                for sid in D.SCENARIOS:
                    vals = answer_key_values(sid)
                    for arm in ARM_ORDER:
                        c = Cell(self.eval_id, sid, arm, 0)
                        m = self.stage(c)
                        disclosed = self.disclosed_values_for(c)
                        n_disclosed += len(disclosed)
                        blobs = [(f.dst, f.src.read_bytes() if f.src else f.content) for f in m.files]
                        blobs.append(("TASK.md", m.task_md.encode()))
                        for dst, b in blobs:
                            t = b.decode("utf-8", errors="replace")
                            low = t.lower()
                            if (CANARY_GUID in t or core_canary.scan_text_for_values(t, vals, disclosed=disclosed)
                                    or (dst == "CHECKLIST.md" and any(tok in low for tok in V.FORBIDDEN))):
                                leaks.append(f"{sid}/{arm}:{dst} (pass conditions {'on' if pc else 'off'})")
                            if Path(dst).name in SCORER_ONLY_NAMES:
                                leaks.append(f"{sid}/{arm}:{dst} (scorer-only name)")
        finally:
            self.pass_conditions = saved
        out.append(CheckResult("staging-leaks", not leaks,
                               f"32 arm x instance manifests clean (pass conditions on and off; with them on, "
                               f"{n_disclosed} PASSES WHEN texts over the 12 checklist-arm cells exempted as "
                               "disclosed)" if not leaks else f"leaks: {leaks[:5]}"))
        return out


EVAL = Eval5()


# ---------------------------------------------------------------------------
# Extra CLI subcommands (dbwm eval5-validate, dbwm eval5-elo)
# ---------------------------------------------------------------------------


def _validate_parser(p: argparse.ArgumentParser) -> None:
    p.add_argument("--ruleset", default=RULESET_V1, choices=list(E.RULESETS))
    p.add_argument("--scenario")
    p.add_argument("--json")


def _validate_cmd(args: argparse.Namespace) -> int:
    from . import validate
    argv = ["--ruleset", args.ruleset] + (["--scenario", args.scenario] if args.scenario else []) + \
        (["--json", args.json] if args.json else [])
    return validate.main(argv)


def _elo_parser(p: argparse.ArgumentParser) -> None:
    p.add_argument("--batch", required=True, help="a dbwm run dir")
    p.add_argument("--out", required=True, help="output dir for comparisons.jsonl and the ladder")
    p.add_argument("--competitor-key", default="condition", choices=["condition", "agent_model", "competitor", "dir"])
    p.add_argument("--judge", default="claude_cli", choices=["claude_cli", "anthropic_api", "mock-checker"])
    p.add_argument("--model", default=None, help="pinned judge model (default claude-sonnet-4-6)")
    p.add_argument("--passes", type=int, default=1)
    p.add_argument("--scenario")
    p.add_argument("--bootstrap", type=int, default=2000)
    p.add_argument("--force", action="store_true")


def _elo_cmd(args: argparse.Namespace) -> int:
    from .elo import judge as J
    from .elo import ladder as L
    disc = J.discover_trials(args.batch, args.competitor_key, args.scenario)
    if disc.n < 2:
        print(f"error: found {disc.n} trials under {args.batch}; need >= 2", file=sys.stderr)
        return 2
    model = args.model or J.DEFAULT_JUDGE_MODEL
    out = Path(args.out)
    if args.judge == "mock-checker":
        runner = J.PairwiseRunner(mock_checker=True, model="mock", passes=args.passes)
    else:
        from dbwm.core.judge import get_judge
        adapter = get_judge(args.judge, out / "judge_raw")
        runner = J.PairwiseRunner(adapter, model, passes=args.passes)
    res = runner.run(disc, out, force=args.force)
    rows = L.load_comparisons(out / "comparisons.jsonl")
    if rows:
        txt, obj = L.analyze(rows, n_boot=args.bootstrap)
        (out / "elo_report.json").write_text(L.report_json_text(obj))
        rel = L.release_summary(rows, n_boot=args.bootstrap)
        (out / "elo_release.json").write_text(json.dumps(rel, indent=2) + "\n")
        print(txt)
        print(f"both-order agreement {rel['both_order_agreement']} "
              f"({'POSITION-UNSTABLE: do not trust this ladder' if rel['position_unstable'] else 'ok'})")
    print(json.dumps(res))
    return 0 if res["failed"] == 0 and rows else 1


EXTRA_COMMANDS = {
    "eval5-validate": (_validate_parser, _validate_cmd),
    "eval5-elo": (_elo_parser, _elo_cmd),
}
