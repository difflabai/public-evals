"""``dbwm`` command line (also ``python -m dbwm``).

Subcommands:
    selftest [--eval N]            core checks + each eval's selftest (no LLM, no network)
    run --eval N --model M ...     run an eval (see ``dbwm run --help``)
    score --run DIR [--ruleset R]  re-score a run's collected deliverables under another ruleset
    report --run DIR               report a run (report.json, report.md)
    audit --run DIR                (re-)audit every attempt of a run
    sandbox-preview --eval N --instance I --arm A   build a sandbox, print listing + TASK.md, no agent
    fetch EVAL [args]              fetch external data an eval needs (e.g. ``dbwm fetch eval4``)
    hygiene                        scan the package tree for paths, secrets and oversize files
    canary [--write]               check (or write) private/<evalN>/CANARY.txt
    image build|clean              build the agent image / remove leftover trial containers

Evals are discovered as ``dbwm.eval1`` .. ``dbwm.eval6``; a missing or broken
eval module never breaks the others. ``--eval`` also accepts any importable
module that exposes ``EVAL`` (third-party evals, the core tests' toy eval).
An eval package may add subcommands with
``EXTRA_COMMANDS = {"name": (configure_parser, handler)}``.

Exit codes: 0 ok; 1 a check/test failed; 2 bad usage or refused to start;
3 a run stopped before every cell was scored (halted, interrupted, incomplete).
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from dbwm.core import paths
from dbwm.core.types import RULESETS, CheckResult, EvalDef, Isolation, ruleset_problem

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_INCOMPLETE = 0, 1, 2, 3


# -- eval resolution ---------------------------------------------------------------------


def resolve_eval(name: str | int) -> EvalDef:
    """``2`` / ``eval2`` -> dbwm.eval2.EVAL; any other importable module exposing EVAL."""
    from dbwm.core import regime as regime_mod

    n = paths.eval_name(name)
    if n in regime_mod.EVAL_IDS:
        return regime_mod.load_eval(n)
    for mod_name in (f"dbwm.{n}", n):
        try:
            mod = importlib.import_module(mod_name)
        except ModuleNotFoundError as e:
            if e.name and (mod_name == e.name or mod_name.startswith(e.name + ".")):
                continue
            raise
        ev = getattr(mod, "EVAL", None)
        if ev is not None:
            return ev
    raise SystemExit(f"dbwm: unknown eval {name!r} (expected 1-6, evalN, or a module exposing EVAL)")


def eval_ids(arg: str | None) -> list[str]:
    from dbwm.core import regime as regime_mod

    if not arg:
        return list(regime_mod.EVAL_IDS)
    return [paths.eval_name(a.strip()) for a in str(arg).split(",") if a.strip()]


def _one_line(s: str, limit: int = 200) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= limit else s[: limit - 3] + "..."


def _print_checks(title: str, checks: Sequence[CheckResult]) -> tuple[int, int, int]:
    ok = fail = skip = 0
    print(f"== {title}")
    for c in checks:
        if c.skipped:
            skip += 1
            print(f"  SKIPPED  {c.name}: {_one_line(c.detail or 'no reason given')}")
        elif c.ok:
            ok += 1
            print(f"  ok       {c.name}" + (f": {_one_line(c.detail)}" if c.detail else ""))
        else:
            fail += 1
            print(f"  FAIL     {c.name}: {_one_line(c.detail, 1000)}")
    return ok, fail, skip


# -- selftest ------------------------------------------------------------------------------


def cmd_selftest(args: argparse.Namespace) -> int:
    from dbwm.core import selfcheck

    totals = [0, 0, 0]
    if not args.evals_only:
        r = _print_checks("core", selfcheck.run_core_checks())
        totals = [a + b for a, b in zip(totals, r)]
    if not args.core_only:
        for e in eval_ids(args.eval):
            try:
                ev = resolve_eval(e)
                checks = list(ev.selftest())
                if not checks:
                    checks = [CheckResult(f"{e} selftest", False, "selftest() returned no checks")]
            except NotImplementedError as ex:
                checks = [CheckResult(f"{e} selftest", False, f"not implemented: {ex}")]
            except BaseException as ex:  # noqa: BLE001 - one broken eval must not stop the rest
                if isinstance(ex, KeyboardInterrupt):
                    raise
                checks = [CheckResult(f"{e} import/selftest", False,
                                      f"{type(ex).__name__}: {ex}".strip()[:500])]
            r = _print_checks(e, checks)
            totals = [a + b for a, b in zip(totals, r)]
    print(f"== total: {totals[0]} ok, {totals[1]} FAIL, {totals[2]} SKIPPED")
    return EXIT_FAIL if totals[1] else EXIT_OK


# -- run -----------------------------------------------------------------------------------


def _csv(v: str | None) -> list[str] | None:
    if v is None:
        return None
    return [x.strip() for x in v.split(",") if x.strip()]


def configure_run(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--eval", required=True, help="1-6, evalN, or a module exposing EVAL")
    ap.add_argument("--model", help="model to pin (required for live adapters; e.g. claude-sonnet-4-6)")
    ap.add_argument("--arms", help="comma list (default: the eval's canonical arms)")
    ap.add_argument("--instances", help="comma list (default: the eval's default set)")
    ap.add_argument("--reps", type=int)
    ap.add_argument("--adapter", choices=["claude_code", "command", "stub", "replay", "openai_compat"])
    ap.add_argument("--isolation", choices=[i.value for i in Isolation])
    ap.add_argument("--auth", dest="auth_mode",
                    choices=["session", "api_key", "oauth_token", "bedrock", "vertex", "none"])
    ap.add_argument("--max-turns", type=int)
    ap.add_argument("--wall-clock-s", type=int, help="wall-clock rail per attempt (scored, never retried)")
    ap.add_argument("--usd-rail", type=float, help="USD rail per attempt (scored, never retried)")
    ap.add_argument("--effort", help="passed as --effort to Claude Code")
    ap.add_argument("--ruleset", choices=list(RULESETS),
                    help="scorer for the run's verdicts (default: the eval's regime.json); the eval must define it "
                         "(as-run: eval 5 only)")
    ap.add_argument("--run-id", help="name of the run directory; reuse it to resume")
    ap.add_argument("--concurrency", type=int)
    ap.add_argument("--order-seed", type=int)
    ap.add_argument("--max-attempts", dest="max_attempts_per_cell", type=int,
                    help="infra retries per cell (default 3)")
    ap.add_argument("--keep-sandbox", action="store_true", help="do not delete sandboxes (debugging)")
    ap.add_argument("--on-cap", choices=["halt", "wait"], default="halt",
                    help="usage cap: stop the run (default) or sleep until the reported reset")
    ap.add_argument("--egress-allow", help="extra host[:port] the container proxy allows (comma list)")
    ap.add_argument("--command", help="agent command for --adapter command (run with cwd = sandbox)")
    ap.add_argument("--replay-dir", help="fixture root for --adapter replay")
    ap.add_argument("--claude-bin", help="claude executable (default: claude, or $DBWM_CLAUDE_BIN)")
    ap.add_argument("--runs-dir", help="parent of run directories (default: $DBWM_RUNS_DIR or PKG/runs)")
    ap.add_argument("--image", help="agent image for --isolation container (default dbwm-agent:1.0.0)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan (cells, launch order, regime hash, isolation/conformance, USD exposure) "
                         "and exit without launching anything")
    ap.add_argument("--max-total-usd", type=float,
                    help="run-level USD rail: start no new attempt once this run's attempts cost this much")
    ap.add_argument("--claude-bare", action="store_true",
                    help="claude_code + --auth api_key only: add --bare (off by default: on Claude Code 2.1.287 "
                         "it drops Write/Glob/Grep, so most arms fail the init check)")
    ap.add_argument("--regime-extra", metavar="FILE.json",
                    help="JSON object merged into regime.extra (recorded in the manifest and the regime hash), "
                         "e.g. {\"openai_compat\": {\"base_url\": ..., \"api_key_env\": ..., \"pricing\": "
                         "{...}}}; keys starting with '_' are comments; never put a secret in it")
    ap.add_argument("--openai-base-url", metavar="URL",
                    help="--adapter openai_compat: endpoint base URL (regime.extra.openai_compat.base_url)")
    ap.add_argument("--openai-key-env", metavar="VAR",
                    help="--adapter openai_compat: name of the env var holding the API key "
                         "(regime.extra.openai_compat.api_key_env; the key itself is never recorded)")


IN_PROCESS_MODELS = {"stub": "dbwm-stub", "replay": "dbwm-replay"}


# Key names that look like credentials: refused in --regime-extra (the regime is written to the manifest).
_SECRET_KEY_RE = re.compile(r"(^|_)(api_key|apikey|key|token|secret|password|authorization)$", re.I)


def _secret_like_keys(obj: Any, prefix: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if _SECRET_KEY_RE.search(str(k)):
                out.append(path)
            out.extend(_secret_like_keys(v, path))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_secret_like_keys(v, f"{prefix}[{i}]"))
    return out


def read_regime_extra(path: str | Path) -> dict[str, Any]:
    """Parse a ``--regime-extra`` file: a JSON object whose keys go into regime.extra.

    Top-level keys starting with "_" are comments. RegimeConfig field names are refused (they have
    their own flags), and so are credential-looking key names: the regime is written verbatim into
    manifest.json, so secrets must stay in environment variables (e.g. openai_compat.api_key_env).
    """
    import dataclasses

    from dbwm.core import regime as regime_mod
    from dbwm.core.types import RegimeConfig

    p = Path(path)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise regime_mod.RegimeError(f"--regime-extra {path}: {e}") from e
    if not isinstance(d, dict):
        raise regime_mod.RegimeError(f"--regime-extra {path}: must be a JSON object")
    d = {k: v for k, v in d.items() if not str(k).startswith("_")}
    fields = sorted(set(d) & {f.name for f in dataclasses.fields(RegimeConfig)})
    if fields:
        raise regime_mod.RegimeError(f"--regime-extra {path}: {', '.join(fields)} are regime fields, "
                                     f"not extra keys; use their command-line flags")
    secrets = _secret_like_keys(d)
    if secrets:
        raise regime_mod.RegimeError(f"--regime-extra {path}: credential-like keys {', '.join(secrets)} "
                                     f"would be written into manifest.json; pass secrets through an "
                                     f"environment variable (e.g. openai_compat.api_key_env)")
    return d


def regime_overrides(args: argparse.Namespace) -> dict[str, Any]:
    ov: dict[str, Any] = {
        "model": args.model, "arms": _csv(args.arms), "instances": _csv(args.instances), "reps": args.reps,
        "adapter": args.adapter, "isolation": args.isolation, "auth_mode": args.auth_mode,
        "max_turns": args.max_turns, "wall_clock_s": args.wall_clock_s, "usd_rail": args.usd_rail,
        "effort": args.effort, "ruleset": args.ruleset, "concurrency": args.concurrency,
        "order_seed": args.order_seed, "max_attempts_per_cell": args.max_attempts_per_cell,
    }
    extra: dict[str, Any] = {}
    if getattr(args, "regime_extra", None):
        extra.update(read_regime_extra(args.regime_extra))
    oc_flags = {"base_url": getattr(args, "openai_base_url", None),
                "api_key_env": getattr(args, "openai_key_env", None)}
    if any(v is not None for v in oc_flags.values()):
        oc = extra.get("openai_compat")
        oc = dict(oc) if isinstance(oc, dict) else {}
        oc.update({k: v for k, v in oc_flags.items() if v is not None})
        extra["openai_compat"] = oc
    if args.egress_allow:
        extra["egress_allow"] = _csv(args.egress_allow)
    if args.command:
        extra["command"] = args.command
    if args.replay_dir:
        extra["replay_dir"] = str(Path(args.replay_dir).resolve())
    if getattr(args, "claude_bare", False):
        extra["claude_bare"] = True
    if extra:
        ov["extra"] = extra
    adapter = args.adapter
    if adapter in IN_PROCESS_MODELS:
        ov["model"] = ov["model"] or IN_PROCESS_MODELS[adapter]
        ov["auth_mode"] = ov["auth_mode"] or "none"
    elif adapter == "command":
        ov["auth_mode"] = ov["auth_mode"] or "none"
    return ov


def render_plan(plan: dict[str, Any]) -> str:
    """Human summary of Runner.plan() (the JSON goes to stdout)."""
    def usd(x: Any) -> str:
        return "unbounded (no --usd-rail)" if x is None else f"${x:,.2f}"

    lines = [f"DRY RUN (nothing launched): eval {plan['eval']}, model {plan['model']}, adapter {plan['adapter']}, "
             f"auth {plan['auth_mode']}, isolation {plan['isolation']}, ruleset {plan['ruleset']}",
             f"  regime hash {plan['regime_hash']}",
             f"  conforming: {plan['conforming']}" + (
                 f" -- NON-CONFORMING: {'; '.join(plan['nonconforming_reasons'])}" if not plan["conforming"] else ""),
             f"  cells: {plan['cells']} ({', '.join(f'{a}: {n}' for a, n in plan['cells_by_arm'].items())}); "
             f"reps {plan['reps']}; up to {plan['max_attempts_per_cell']} attempts per cell; "
             f"concurrency {plan['concurrency']}",
             f"  per attempt: max_turns {plan['max_turns']}, wall clock {plan['wall_clock_s']} s, "
             f"USD rail {usd(plan['usd_rail'])}",
             f"  USD exposure: cells x rail = {usd(plan['usd_bound_scored'])}; with every retry = "
             f"{usd(plan['usd_bound_all_attempts'])}; run-level rail (--max-total-usd): "
             f"{'none' if plan['max_total_usd'] is None else usd(plan['max_total_usd'])}",
             f"  first launches: {', '.join(plan['launch_order_head'][:6])}"
             + (" ..." if plan["cells"] > 6 else "")]
    return "\n".join(lines)


def cmd_run(args: argparse.Namespace) -> int:
    from dbwm.core import regime as regime_mod
    from dbwm.core import runner

    ev = resolve_eval(args.eval)
    try:
        regime = regime_mod.build_regime(ev, regime_overrides(args))
    except regime_mod.RegimeError as e:
        print(f"dbwm run: {e}", file=sys.stderr)
        return EXIT_USAGE
    akw: dict[str, Any] = {}
    if regime.adapter == "claude_code" and args.claude_bin:
        akw["claude_bin"] = args.claude_bin
    if regime.adapter == "command":
        cmd = regime.extra.get("command")
        if not cmd:
            print("dbwm run: --adapter command needs --command", file=sys.stderr)
            return EXIT_USAGE
        akw["command"] = cmd
    if regime.adapter == "replay":
        if not regime.extra.get("replay_dir"):
            print("dbwm run: --adapter replay needs --replay-dir", file=sys.stderr)
            return EXIT_USAGE
        akw["replay_dir"] = regime.extra["replay_dir"]
    opts = runner.RunOptions(run_id=args.run_id, runs_root=Path(args.runs_dir) if args.runs_dir else None,
                             keep_sandbox=args.keep_sandbox, cap_policy=args.on_cap, adapter_kwargs=akw,
                             max_total_usd=args.max_total_usd)
    if args.image:
        opts.image = args.image
    if args.max_total_usd is not None and not args.max_total_usd > 0:
        print("dbwm run: --max-total-usd must be a positive number", file=sys.stderr)
        return EXIT_USAGE
    if args.dry_run:
        try:
            plan = runner.Runner(ev, regime, opts).plan()
        except (runner.RunnerError, regime_mod.RegimeError) as e:
            print(f"dbwm run: {e}", file=sys.stderr)
            return EXIT_USAGE
        print(render_plan(plan), file=sys.stderr)
        print(json.dumps(plan, indent=2, sort_keys=True))
        return EXIT_OK
    try:
        res = runner.Runner(ev, regime, opts).run()
    except (runner.RunnerError, regime_mod.RegimeError) as e:
        print(f"dbwm run: {e}", file=sys.stderr)
        return EXIT_USAGE
    print(json.dumps({"run_id": res.run_id, "run_dir": str(res.run_dir), "status": res.status,
                      "halt_reason": res.halt_reason, "cells_total": res.cells_total,
                      "cells_scored": res.cells_scored, "cells_exhausted": res.cells_exhausted,
                      "outcomes": res.outcomes}, indent=2, sort_keys=True))
    return EXIT_OK if res.status == "complete" else EXIT_INCOMPLETE


# -- score / report / audit ------------------------------------------------------------------


def _run_eval(run_dir: Path) -> tuple[dict[str, Any], EvalDef]:
    from dbwm.core.runner import load_manifest

    m = load_manifest(run_dir)
    return m, resolve_eval(m.get("eval_module") or m.get("eval"))


def cmd_score(args: argparse.Namespace) -> int:
    from dbwm.core import verdict as verdict_mod
    from dbwm.core.runner import RUN_DETAIL_KEYS, compute_engine_hash
    from dbwm.core.types import Cell, ScoreResult

    run_dir = Path(args.run).resolve()
    m, ev = _run_eval(run_dir)
    ruleset = args.ruleset or (m.get("regime") or {}).get("ruleset")
    try:
        problem = ruleset_problem(ev, ruleset)
    except ValueError as e:  # the eval declares a bad ``rulesets`` (contract bug)
        problem = str(e)
    if problem:
        print(f"dbwm score: {problem}", file=sys.stderr)
        return EXIT_USAGE
    ehash = compute_engine_hash(ev)
    n = skipped = 0
    for vpath in sorted((run_dir / "cells").glob("*/attempt-*/verdict.json")):
        adir = vpath.parent
        primary = verdict_mod.read_verdict(vpath)
        if primary["ruleset"] == ruleset and not args.force:
            skipped += 1
            continue
        c = primary["cell"]
        cell = Cell(primary["eval"], c["instance"], c["arm"], int(c["rep"]))
        score: ScoreResult | None = None
        if primary["scored"]:
            try:
                score = ev.score(adir / "deliverables", cell, ruleset)
            except Exception as e:  # noqa: BLE001
                present = any((adir / "deliverables").rglob("*"))
                score = ScoreResult(present, None if present else False, None, None, "error", None, ruleset,
                                    {"score_error": f"{type(e).__name__}: {e}"})
        # Keep the attempt-level details the runner wrote; the eval's own
        # details come from the new score only.
        old = primary.get("details") or {}
        details = {k: old[k] for k in RUN_DETAIL_KEYS if k in old}
        details["rescored_from"] = {"ruleset": primary["ruleset"], "engine_hash": primary["engine_hash"]}
        v = verdict_mod.build_verdict(
            cell=cell, attempt=int(primary["attempt"]), outcome=primary["outcome"], score=score,
            regime_hash=primary["regime_hash"], engine_hash=ehash, ruleset=ruleset,
            model_requested=primary.get("model_requested"), models_served=primary.get("models_served") or [],
            oracle_calls=(primary.get("process") or {}).get("oracle_calls"),
            oracle_override_attempts=(primary.get("process") or {}).get("oracle_override_attempts"),
            audit=primary.get("audit") or {}, details=details)
        v["process"] = dict(primary.get("process") or v["process"])
        verdict_mod.write_verdict(adir / f"verdict.{ruleset}.json", v)
        n += 1
    print(json.dumps({"run_dir": str(run_dir), "ruleset": ruleset, "rescored": n,
                      "skipped_same_ruleset": skipped}, sort_keys=True))
    return EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    from dbwm.core import report as report_mod
    from dbwm.core.verdict import atomic_write_text, dump_json

    run_dir = Path(args.run).resolve()
    _, ev = _run_eval(run_dir)
    # verdict.json plus re-scorings verdict.<ruleset>.json, attempts.jsonl
    # invalidations honoured; one section per ruleset.
    summary = report_mod.build_report(run_dir)
    try:
        summary["eval_report"] = ev.report(run_dir)
    except NotImplementedError as e:
        summary["eval_report"] = {"skipped": f"no eval-specific report: {e}"}
    except Exception as e:  # noqa: BLE001
        summary["eval_report"] = {"error": f"{type(e).__name__}: {e}"}
    out = Path(args.out).resolve() if args.out else run_dir
    atomic_write_text(out / report_mod.REPORT_JSON, dump_json(summary))
    # Generic tables, then the eval's own section (contrasts, judged tables).
    md = report_mod.render_full_markdown(summary, getattr(ev, "title", None), ev)
    atomic_write_text(out / report_mod.REPORT_MD, md)
    print(md)
    return EXIT_OK


def cmd_audit(args: argparse.Namespace) -> int:
    from dbwm.core import audit as audit_mod

    run_dir = Path(args.run).resolve()
    _, ev = _run_eval(run_dir)
    summary = audit_mod.audit_run(run_dir, ev)
    print(json.dumps({k: summary[k] for k in ("attempts", "by_reason", "errors")}, indent=2, sort_keys=True))
    print(f"flagged attempts: {len(summary['flagged'])}")
    return EXIT_FAIL if summary.get("errors") else EXIT_OK


# -- sandbox preview -----------------------------------------------------------------------------


def cmd_sandbox_preview(args: argparse.Namespace) -> int:
    from dbwm.core import sandbox
    from dbwm.core.types import Cell, disclosed_values_for

    ev = resolve_eval(args.eval)
    if args.arm not in ev.arms:
        print(f"dbwm sandbox-preview: unknown arm {args.arm!r} (arms: {', '.join(ev.arms)})", file=sys.stderr)
        return EXIT_USAGE
    cell = Cell(ev.eval_id, args.instance, args.arm, args.rep)
    arm = ev.arms[args.arm]
    h = sandbox.build_sandbox(ev.stage(cell), cell, arm.tools, arm.tools.oracle_tools)
    try:
        prompt = ev.prompt(cell)
        disclosed = disclosed_values_for(ev, cell)
        problems = sandbox.precheck(h, answer_values=ev.answer_key_values(cell), prompt=prompt,
                                    disclosed_values=disclosed)
        print(f"# sandbox for {cell.cell_id} (arm: {arm.description})")
        if disclosed:
            print(f"# disclosed on purpose (exempt from the answer-key scan): {len(disclosed)} value(s)")
        print(f"# tools: {sorted(arm.tools.builtin)} + oracle {[s.name for s in arm.tools.oracle_tools]} "
              f"via {arm.tools.transport}; min isolation {arm.min_isolation.value}")
        print(f"# manifest sha256 {h.manifest_sha256}")
        print("## listing")
        for rel in h.listing:
            print(f"  {rel}")
        print("## TASK.md")
        print((h.path / sandbox.TASK_FILENAME).read_text(encoding="utf-8"))
        print("## prompt")
        print(prompt)
        print("## pre-check")
        print("  clean" if not problems else "\n".join(f"  PROBLEM: {p}" for p in problems))
        if args.keep:
            print(f"## kept at {h.path}")
    finally:
        sandbox.cleanup(h, keep=args.keep)
    return EXIT_FAIL if problems else EXIT_OK


# -- fetch / hygiene / canary / image -------------------------------------------------------------


def cmd_fetch(args: argparse.Namespace, rest: list[str]) -> int:
    e = paths.eval_name(args.target)
    try:
        mod = importlib.import_module(f"dbwm.{e}")
    except Exception as ex:  # noqa: BLE001
        print(f"dbwm fetch: cannot import dbwm.{e}: {ex}", file=sys.stderr)
        return EXIT_USAGE
    extra = getattr(mod, "EXTRA_COMMANDS", {}) or {}
    if "fetch" in extra:
        configure, handler = extra["fetch"]
        ap = argparse.ArgumentParser(prog=f"dbwm fetch {e}")
        configure(ap)
        return int(handler(ap.parse_args(rest)) or 0)
    fn = getattr(mod, "fetch", None)
    if callable(fn):
        return int(fn(rest) or 0)
    print(f"dbwm fetch: {e} has nothing to fetch", file=sys.stderr)
    return EXIT_USAGE


def cmd_hygiene(args: argparse.Namespace) -> int:
    from dbwm.core import hygiene

    return int(hygiene.run_from_args(args) or 0)


def cmd_canary(args: argparse.Namespace) -> int:
    from dbwm.core import canary

    if args.write:
        for p in canary.write_canaries():
            print(f"wrote {p.relative_to(paths.PKG_ROOT) if paths.PKG_ROOT in p.parents else p}")
        return EXIT_OK
    missing = canary.missing_canaries()
    for d in missing:
        print(f"missing or wrong CANARY.txt: {d}")
    if not missing:
        print("all private/<evalN>/ directories carry CANARY.txt")
    return EXIT_FAIL if missing else EXIT_OK


def cmd_image(args: argparse.Namespace) -> int:
    from dbwm.core import container

    if args.action == "build":
        return container.build_image(args.tag)
    if args.action == "clean":
        n = container.remove_stale()
        print(f"removed {n} leftover dbwm containers/networks")
        return EXIT_OK
    return EXIT_USAGE


# -- parser ---------------------------------------------------------------------------------------


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, Callable[..., int]]]:
    ap = argparse.ArgumentParser(prog="dbwm", description="diff-bwm-1 benchmark harness")
    ap.add_argument("--version", action="version", version=f"dbwm {_version()}")
    sub = ap.add_subparsers(dest="cmd", metavar="COMMAND")
    handlers: dict[str, Callable[..., int]] = {}

    p = sub.add_parser("selftest", help="core checks + eval selftests (no LLM, no network)")
    p.add_argument("--eval", help="comma list of evals (default: all)")
    p.add_argument("--core-only", action="store_true")
    p.add_argument("--evals-only", action="store_true")
    handlers["selftest"] = cmd_selftest

    p = sub.add_parser("run", help="run an eval")
    configure_run(p)
    handlers["run"] = cmd_run

    p = sub.add_parser("score", help="re-score a run under a ruleset (writes verdict.<ruleset>.json)")
    p.add_argument("--run", required=True)
    p.add_argument("--ruleset", choices=list(RULESETS),
                   help="default: the run's regime ruleset; refused unless the run's eval defines it")
    p.add_argument("--force", action="store_true", help="also re-score attempts already under this ruleset")
    handlers["score"] = cmd_score

    p = sub.add_parser("report", help="report a run (report.json, report.md)")
    p.add_argument("--run", required=True)
    p.add_argument("--out", help="directory for report.json/report.md (default: the run dir)")
    handlers["report"] = cmd_report

    p = sub.add_parser("audit", help="audit every attempt of a run")
    p.add_argument("--run", required=True)
    handlers["audit"] = cmd_audit

    p = sub.add_parser("sandbox-preview", help="build one sandbox and print it (no agent)")
    p.add_argument("--eval", required=True)
    p.add_argument("--instance", required=True)
    p.add_argument("--arm", required=True)
    p.add_argument("--rep", type=int, default=0)
    p.add_argument("--keep", action="store_true", help="keep the sandbox directory")
    handlers["sandbox-preview"] = cmd_sandbox_preview

    p = sub.add_parser("fetch", help="fetch external data for an eval (e.g. eval4)",
                       description="Fetch an eval's external data. Options after the target go to the eval's "
                                   "fetch command: `dbwm fetch eval4 --help`.")
    p.add_argument("target", help="eval with external data (eval4: AA-Briefcase-Lite at a pinned revision)")
    handlers["fetch"] = cmd_fetch

    p = sub.add_parser("hygiene", help="release hygiene scan of the package tree")
    try:
        from dbwm.core import hygiene

        hygiene.configure_parser(p)
    except Exception:  # noqa: BLE001 - reported when the command runs
        pass
    handlers["hygiene"] = cmd_hygiene

    p = sub.add_parser("canary", help="check or write private/<evalN>/CANARY.txt")
    p.add_argument("--write", action="store_true")
    handlers["canary"] = cmd_canary

    p = sub.add_parser("image", help="agent container image")
    p.add_argument("action", choices=["build", "clean"])
    p.add_argument("--tag", default="dbwm-agent:1.0.0")
    handlers["image"] = cmd_image

    _add_eval_commands(sub, handlers)
    return ap, handlers


def _add_eval_commands(sub: Any, handlers: dict[str, Callable[..., int]]) -> None:
    from dbwm.core import regime as regime_mod

    for e in regime_mod.EVAL_IDS:
        try:
            mod = importlib.import_module(f"dbwm.{e}")
            extra = getattr(mod, "EXTRA_COMMANDS", None) or {}
        except Exception:  # noqa: BLE001 - a broken eval never breaks the CLI
            continue
        for name, spec in extra.items():
            if name in handlers or name == "fetch":
                continue
            try:
                configure, handler = spec
                p = sub.add_parser(name, help=f"({e})")
                configure(p)
                handlers[name] = handler
            except Exception:  # noqa: BLE001
                continue


def _version() -> str:
    import dbwm

    return getattr(dbwm, "__version__", "0")


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap, handlers = build_parser()
    if argv[:1] == ["fetch"]:
        # Everything after the target belongs to the eval's own fetch parser
        # (including --help, which the core subparser would otherwise swallow).
        if len(argv) >= 2 and not argv[1].startswith("-"):
            return cmd_fetch(argparse.Namespace(cmd="fetch", target=argv[1]), argv[2:])
        ns, rest = ap.parse_known_args(argv)  # no target: usage error / core help
        return cmd_fetch(ns, rest)
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return EXIT_USAGE
    missing = paths.missing_roots()
    overridden = [f"{paths._ENV[k]}={os.environ[paths._ENV[k]]}" for k in missing if os.environ.get(paths._ENV[k])]
    if overridden:
        sys.stderr.write(f"dbwm: {', '.join(overridden)}: no such directory\n")
        return EXIT_USAGE
    if missing:
        sys.stderr.write(
            f"dbwm: no {', '.join(missing)} director{'y' if len(missing) == 1 else 'ies'} next to the package "
            f"({paths.PKG_ROOT}). diff-bwm-1 runs from a checkout of its repository: install it there with "
            "`python -m pip install -e '.[all,dev]'` (a non-editable `pip install .` copies only the code), "
            "or point DBWM_DATA_DIR and DBWM_PRIVATE_DIR at a checkout's data/ and private/.\n")
        return EXIT_USAGE
    return int(handlers[args.cmd](args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
