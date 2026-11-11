"""Run an eval: designed cells -> attempts -> verdicts, append-only and resumable.

Why one runner: the paper had five runners with five retry policies, five
stopping rules and five ideas of which attempt counts (ledger G02, G04, A10b,
A10c, A11c, N12). Here, for every eval and adapter:

  * cells come from ``EVAL.cells(regime)`` and are launched in a seeded
    interleave: within each rep, instances in a seeded order and, inside each
    instance, the arms in a seeded order. Any prefix of a rep's block that is
    at least one instance long covers every arm, and per-arm counts in any
    prefix differ by at most one, so a run cut short by a usage cap is not
    biased toward the arms launched first (ledger A10b);
  * each attempt gets a fresh sandbox, a fresh oracle server (HOST) or sidecar
    (CONTAINER), its own immutable directory ``cells/<cell_id>/attempt-<k>/``
    and one append-only line in ``attempts.jsonl`` (A11c, N12);
  * an attempt is classified from structured fields only (outcomes.classify);
    scored outcomes (completed, turn budget, clock rail, USD rail) are final
    and never retried (G02); only infra outcomes are retried, at most
    ``max_attempts_per_cell`` per cell. Account-level infra outcomes
    (INFRA_AUTH, INFRA_CAP) do not consume a cell's attempts: they stop or
    pause the whole run instead, so a cap can never drop a cell by launch
    order;
  * the run halts on an auth failure (needs a human; run_confirmatory.py:578-585),
    on a usage cap (``cap_policy`` "halt", the default) or sleeps until the
    reported reset ("wait"; reset parsing ported from run_confirmatory.py:162-215),
    and after 3 consecutive INFRA_INVALID_REGIME attempts (a broken regime
    must not burn the whole design);
  * resume = run again with the same ``--run-id``: cells with a scored attempt
    are skipped, nothing is overwritten, and the regime hash must match;
  * ``manifest.json`` is always written (start, after every attempt, at the
    end, and on SIGTERM/SIGINT): regime, regime hash, engine hash, harness
    version, python, platform, CLI version, isolation and conformance, the
    designed cells and the launch order. The first SIGTERM/SIGINT stops new
    launches and lets in-flight attempts finish and record (ledger G05); a
    second one kills them (reason "interrupt", a retryable CRASH).

Per attempt: build sandbox -> pre-check (any finding = INFRA_INVALID_REGIME,
no agent launched) -> start oracle server (host) or sidecar (container) ->
adapter.run -> classify -> collect deliverables -> delete sandbox ->
EVAL.score(ruleset) (scored outcomes only) -> verdict.json (dbwm.core.verdict)
-> audit.json (dbwm.core.audit), whose summary is merged into the verdict.
"""

from __future__ import annotations

import concurrent.futures
import datetime
import hashlib
import importlib
import json
import os
import platform
import random
import secrets
import signal
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import dbwm
from dbwm.core import attempts as attempts_mod
from dbwm.core import container, outcomes, paths, sandbox
from dbwm.core import regime as regime_mod
from dbwm.core.adapters import registry
from dbwm.core.adapters.base import AgentAdapter, agent_result_to_json, write_json
from dbwm.core.env import bash_timeout_env, build_agent_env, credential_values, relay_agent_env
from dbwm.core.oracle.server import OracleServer, summarize_calls
from dbwm.core.types import (
    SCHEMA_MANIFEST,
    SCORED_OUTCOMES,
    AgentRequest,
    AgentResult,
    ArmSpec,
    Budget,
    Cell,
    EvalDef,
    Isolation,
    OracleContext,
    Outcome,
    RegimeConfig,
    ScoreResult,
    disclosed_values_for,
)

HARNESS_VERSION = getattr(dbwm, "__version__", "0")
MANIFEST = "manifest.json"
CREDENTIAL_REDACTIONS = "credential_redactions.json"
HALT_AFTER_INVALID = 3  # consecutive INFRA_INVALID_REGIME (or harness-error) attempts
ACCOUNT_OUTCOMES = frozenset({Outcome.INFRA_AUTH, Outcome.INFRA_CAP})
CAP_POLICIES = ("halt", "wait")
DEFAULT_MAX_CAP_WAIT_S = 6 * 3600.0
CAP_WAIT_BUFFER_S = 120.0
# Attempt-level keys the runner writes into verdict.details (the rest come from
# the eval's ScoreResult.details); `dbwm score` carries these over to re-scorings.
RUN_DETAIL_KEYS = ("run_id", "adapter", "isolation", "conforming", "killed_by_harness", "result_subtype",
                   "assertion_failures", "sandbox_precheck", "deliverables_missing", "deliverables_rejected",
                   "oracle", "permission_denials", "infra_error", "command_timeout_s", "agent_init")


class RunnerError(RuntimeError):
    """The run cannot start (bad regime, missing CLI/image, resumed with a different regime)."""


# -- ordering ----------------------------------------------------------------------------


def _seed_int(seed: int, rep: int) -> int:
    """A PYTHONHASHSEED-independent integer seed for one rep's shuffle."""
    return int.from_bytes(hashlib.sha256(f"dbwm-order:{seed}:{rep}".encode()).digest()[:8], "big")


def interleave(cells: Iterable[Cell], seed: int) -> list[Cell]:
    """Seeded launch order: rep by rep; instances shuffled; arms shuffled inside each instance.

    Every prefix of a rep's block that covers one instance covers every arm,
    and per-arm counts in any prefix differ by at most one (on a full
    instance x arm grid).
    """
    by_rep: dict[int, list[Cell]] = {}
    for c in cells:
        by_rep.setdefault(c.rep, []).append(c)
    out: list[Cell] = []
    for rep in sorted(by_rep):
        rng = random.Random(_seed_int(seed, rep))
        by_inst: dict[str, list[Cell]] = {}
        for c in sorted(by_rep[rep], key=lambda c: (c.instance, c.arm)):
            by_inst.setdefault(c.instance, []).append(c)
        insts = sorted(by_inst)
        rng.shuffle(insts)
        for i in insts:
            block = list(by_inst[i])
            rng.shuffle(block)
            out.extend(block)
    return out


# -- provenance ----------------------------------------------------------------------------


def engine_sources(eval_def: EvalDef) -> list[Path]:
    """The files whose bytes define the eval's scorer (hashed into every verdict).

    An eval may define ``engine_sources() -> list[Path]``; the default is every
    ``.py`` file of the eval package (or module) plus dbwm/core/claims.py.
    """
    fn = getattr(eval_def, "engine_sources", None)
    if callable(fn):
        files = [Path(p) for p in fn()]
    else:
        mod = importlib.import_module(regime_mod.eval_module(eval_def))
        f = Path(getattr(mod, "__file__", "") or "")
        if f.name == "__init__.py":
            files = sorted(p for p in f.parent.rglob("*.py") if "__pycache__" not in p.parts)
        elif f.is_file():
            files = [f]
        else:
            files = []
        claims = paths.PACKAGE_DIR / "core" / "claims.py"
        if claims.is_file():
            files.append(claims)
    return sorted({p.resolve() for p in files})


def deliverables_for(eval_def: EvalDef, cell: Cell) -> list[str]:
    """The deliverable paths the collector copies for one cell.

    An eval whose deliverable names differ by instance (eval 5: one set of
    file names per scenario) may define the optional hook
    ``deliverables_for(cell) -> Sequence[str]``; otherwise, or when the hook
    returns None, the eval-wide ``EVAL.deliverables`` applies. Only declared
    paths are collected, so a per-cell list also keeps another instance's
    names out of ``deliverables.json``'s "missing".
    """
    hook = getattr(eval_def, "deliverables_for", None)
    if callable(hook):
        got = hook(cell)
        if got is not None:
            if isinstance(got, (str, bytes)) or not all(isinstance(x, str) and x for x in got):
                raise RunnerError(f"{eval_def.eval_id}.deliverables_for({cell.cell_id}) must return a "
                                  f"sequence of relative paths, got {got!r}")
            return list(got)
    return list(eval_def.deliverables)


def command_timeout_for(eval_def: EvalDef, cell: Cell) -> float | None:
    """Per-command limit for a bash arm, from the optional ``EVAL.command_timeout_s(cell)``.

    None when the eval defines no hook, the hook returns None, or the arm has
    no bash. The runner enforces it (Claude Code: BASH_DEFAULT_TIMEOUT_MS =
    BASH_MAX_TIMEOUT_MS = 1000 x limit, background tasks off; openai_compat:
    ``bash_timeout_s``) because the eval's prompt states it.
    """
    hook = getattr(eval_def, "command_timeout_s", None)
    if not callable(hook) or not eval_def.arms[cell.arm].tools.needs_bash:
        return None
    t = hook(cell)
    if t is None:
        return None
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not t > 0:
        raise RunnerError(f"{eval_def.eval_id}.command_timeout_s({cell.cell_id}) must be a positive number "
                          f"or None, got {t!r}")
    return t


def compute_engine_hash(eval_def: EvalDef) -> str:
    from dbwm.core.verdict import engine_hash

    files = engine_sources(eval_def)
    if not files:
        return hashlib.sha256(b"no engine sources").hexdigest()
    root = Path(os.path.commonpath([str(p.parent) for p in files]))
    return engine_hash(files, root=root)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id(eval_id: str) -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{eval_id}-{stamp}-{secrets.token_hex(2)}"


def _atomic_json(path: Path, obj: Any) -> None:
    write_json(path, obj)


# -- options / result -----------------------------------------------------------------------


@dataclass
class RunOptions:
    run_id: str | None = None
    runs_root: Path | None = None
    keep_sandbox: bool = False
    cap_policy: str = "halt"  # "halt" | "wait"
    max_cap_wait_s: float = DEFAULT_MAX_CAP_WAIT_S
    cap_wait_buffer_s: float = CAP_WAIT_BUFFER_S
    image: str = container.IMAGE
    # Bind mounts added to the agent container (host, target, mode). Test hook
    # (the fake claude); recorded in the manifest.
    container_extra_mounts: list[tuple[str, str, str]] = field(default_factory=list)
    adapter_kwargs: dict[str, Any] = field(default_factory=dict)
    preflight: bool = True
    # Run-level USD rail (review m4): no new attempt starts once the attempts of
    # this run (all invocations) have cost this much. An operational guard, not
    # part of the hashed regime; recorded in the manifest. None: no rail.
    max_total_usd: float | None = None
    install_signal_handlers: bool = True
    log: Callable[[str], None] = field(default=lambda m: print(m, file=sys.stderr, flush=True))


@dataclass
class RunResult:
    run_id: str
    run_dir: Path
    status: str  # complete | halted | interrupted
    halt_reason: str | None
    outcomes: dict[str, int]
    cells_total: int
    cells_scored: int
    cells_exhausted: list[str]
    attempts_this_invocation: int


# -- the runner ----------------------------------------------------------------------------


class Runner:
    def __init__(self, eval_def: EvalDef, regime: RegimeConfig, options: RunOptions | None = None,
                 *, adapter: AgentAdapter | None = None):
        self.ev = eval_def
        self.regime = regime
        self.opt = options or RunOptions()
        if self.opt.cap_policy not in CAP_POLICIES:
            raise RunnerError(f"cap_policy must be one of {CAP_POLICIES}")
        self.adapter_name = regime.adapter
        kw = dict(self.opt.adapter_kwargs)
        if self.adapter_name == "stub":
            kw.setdefault("eval_def", eval_def)
        self.adapter = adapter or registry.get_adapter(self.adapter_name, **kw)
        self.in_process = self.adapter_name in registry.IN_PROCESS
        self.use_container = regime.isolation == Isolation.CONTAINER and not self.in_process
        self.run_id = self.opt.run_id or new_run_id(eval_def.eval_id)
        root = Path(self.opt.runs_root) if self.opt.runs_root else paths.runs_root()
        self.run_dir = (root / self.run_id).resolve()
        self.store: attempts_mod.RunStore | None = None
        self.regime_hash = regime.regime_hash()
        self.engine_hash = ""
        self.cli_version: str | None = None
        self._warned_cli = False
        self.relay_auth = self._relay_auth()
        self.egress_allow = self._egress_allow()
        self._lock = threading.Lock()
        # Re-entrant: the signal handler (main thread) may write the manifest
        # while the main thread is itself inside _write_manifest.
        self._manifest_lock = threading.RLock()
        self._stop = threading.Event()  # no new attempts (first signal / halt)
        self._abort = threading.Event()  # kill in-flight agents (second signal)
        self._halt_reason: str | None = None
        self._consecutive_invalid = 0
        self._paused_until = 0.0
        self._outcome_counts: dict[str, int] = {}
        self._attempts_run = 0
        self._exhausted: list[str] = []
        self._manifest: dict[str, Any] = {}
        self._order: list[Cell] = []
        self._designed: list[Cell] = []
        self._image_id: str | None = None

    # -- setup ---------------------------------------------------------------------------

    def _relay_auth(self) -> tuple[str, str] | None:
        """(relay auth header, credential variable) when the sidecar API relay is used, else None.

        Red-team finding RT-E: the CLI's Bash tool inherits the auth variable, so
        at CONTAINER isolation the claude_code agent gets only a placeholder and
        ANTHROPIC_BASE_URL pointing at the sidecar's relay (dbwm.core.api_relay),
        which holds the real credential. ``regime.extra["api_relay"] = false``
        turns it off (the run is then recorded as non-conforming).
        """
        return regime_mod.relay_auth(self.regime, self.adapter_name)

    def _egress_allow(self) -> list[str]:
        from dbwm.core.egress_proxy import DEFAULT_ALLOW, auth_mode_hosts

        extra = self.regime.extra.get("egress_allow") or []
        if isinstance(extra, str):
            extra = [e.strip() for e in extra.split(",") if e.strip()]
        auth_hosts = auth_mode_hosts(self.regime.auth_mode, os.environ)  # review m7
        # With the API relay the agent reaches the API only through the sidecar:
        # the CONNECT tunnel to the API host is not opened (RT-E).
        base = [] if self.relay_auth else list(DEFAULT_ALLOW)
        return list(dict.fromkeys([*base, *auth_hosts, *map(str, extra)]))

    def _log(self, msg: str) -> None:
        try:
            self.opt.log(msg)
        except Exception:  # noqa: BLE001 - logging must never break a run
            pass

    def preflight(self) -> None:
        """Refuse early, with an actionable message, instead of failing every attempt."""
        problems: list[str] = []
        try:
            paths.assert_outside_repos(paths.sandbox_root())
        except paths.InsideRepoError as e:
            problems.append(str(e))
        if self.use_container:
            if not container.docker_available():
                problems.append("isolation container needs a running Docker daemon (docker info failed)")
            elif not container.image_exists(self.opt.image):
                problems.append(f"docker image {self.opt.image} not found; build it with `dbwm image build`")
        if self.adapter_name == "claude_code" and not problems:
            self.cli_version = self._claude_version()
            if self.cli_version is None:
                where = f"in image {self.opt.image}" if self.use_container else "on PATH"
                problems.append(f"`claude --version` failed {where}; install Claude Code "
                                "(npm i -g @anthropic-ai/claude-code) or set DBWM_CLAUDE_BIN")
        if problems:
            raise RunnerError("cannot start run:\n  - " + "\n  - ".join(problems))

    def _claude_version(self) -> str | None:
        bin_ = getattr(self.adapter, "claude_bin", "claude")
        if self.use_container:
            probe = container.ContainerTrial(
                trial_id="version", image=self.opt.image, network="none", sidecar="", agent="",
                sandbox=Path("."), harness_out=Path("."), tools_dir=Path("."), oracle_port=None,
                proxy_port=0, user=container.host_user(), extra_mounts=list(self.opt.container_extra_mounts))
            rc, out, _ = probe.run_once([bin_, "--version"], timeout=120)
            return out.strip() if rc == 0 and out.strip() else None
        import subprocess

        try:
            p = subprocess.run([bin_, "--version"], capture_output=True, text=True, timeout=60,
                               env=build_agent_env("none", Isolation.HOST))
        except (OSError, subprocess.TimeoutExpired):
            return None
        return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None

    def _cli_version_verified(self) -> bool | None:
        """claude_code only: is the recorded ``claude --version`` a verified one (review M2)?"""
        if self.adapter_name != "claude_code":
            return None
        from dbwm.core.adapters.claude_code import cli_version_verified

        return cli_version_verified(self.cli_version)

    def _warn_unverified_cli(self) -> None:
        if self._cli_version_verified() is False and not self._warned_cli:
            from dbwm.core.adapters.claude_code import VERIFIED_CLI_VERSIONS

            self._warned_cli = True
            self._log(f"[dbwm] WARNING: Claude Code {self.cli_version!r} is not a verified version "
                      f"(verified: {', '.join(VERIFIED_CLI_VERSIONS)}). The isolation of no-bash arms at "
                      "`host` rests on --restricted/--tools behaviour checked on those versions only; the run "
                      "continues and manifest, verdicts and report record cli_version_verified=false.")

    def _open_store(self) -> None:
        mpath = self.run_dir / MANIFEST
        if mpath.is_file():
            old = json.loads(mpath.read_text(encoding="utf-8"))
            if old.get("regime_hash") != self.regime_hash:
                diff = {}
                try:
                    diff = regime_mod.regime_diff(RegimeConfig.from_json(old["regime"]), self.regime)
                except Exception:  # noqa: BLE001
                    pass
                raise RunnerError(
                    f"run {self.run_id} exists with a different regime (hash {old.get('regime_hash')} "
                    f"!= {self.regime_hash}); differences: {diff}. Resume with the same regime or "
                    "start a new --run-id.")
            self._manifest = old
            self._manifest.setdefault("invocations", [])
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.store = attempts_mod.RunStore(self.run_dir)

    def _write_manifest(self, status: str) -> None:
        with self._manifest_lock:
            self._write_manifest_locked(status)

    def _write_manifest_locked(self, status: str) -> None:
        nonconf = regime_mod.nonconforming_reasons(self.regime, self.ev)
        m = self._manifest
        m.update({
            "schema": SCHEMA_MANIFEST,
            "run_id": self.run_id,
            "eval": self.ev.eval_id,
            "eval_title": getattr(self.ev, "title", None),
            "eval_module": regime_mod.eval_module(self.ev),
            "regime": self.regime.to_json(),
            "regime_hash": self.regime_hash,
            "engine_hash": self.engine_hash,
            "harness_version": HARNESS_VERSION,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "adapter": self.adapter_name,
            "cli_version": self.cli_version,
            "cli_version_verified": self._cli_version_verified(),
            "isolation": self.regime.isolation.value,
            "effective_isolation": "in-process" if self.in_process else self.regime.isolation.value,
            "conforming": not nonconf,
            "nonconforming_reasons": nonconf,
            "egress_allow": self.egress_allow if self.use_container else None,
            "api_relay": bool(self.relay_auth) if self.use_container else None,
            "container_image": self.opt.image if self.use_container else None,
            "container_image_id": self._image_id,
            "container_extra_mounts": [list(t) for t in self.opt.container_extra_mounts] or None,
            "claude_bin": getattr(self.adapter, "claude_bin", None),
            "command_timeout_s": self._command_timeouts(),
            "max_total_usd": self.opt.max_total_usd,
            "spent_usd": self._spent_usd() if self.store is not None else None,
            "cells": [{"instance": c.instance, "arm": c.arm, "rep": c.rep, "cell_id": c.cell_id}
                      for c in self._designed],
            "order": [c.cell_id for c in self._order],
            "status": status,
            "halt_reason": self._halt_reason,
            "updated": _now_iso(),
            "attempts_file": attempts_mod.ATTEMPTS_FILE,
        })
        m.setdefault("started", _now_iso())
        _atomic_json(self.run_dir / MANIFEST, m)

    def _command_timeouts(self) -> dict[str, list[float]] | None:
        """{arm: sorted distinct per-command limits} over the designed cells (None: no limits)."""
        out: dict[str, set[float]] = {}
        for c in self._designed:
            try:
                t = command_timeout_for(self.ev, c)
            except Exception:  # noqa: BLE001 - the attempt itself records the hook error
                continue
            if t is not None:
                out.setdefault(c.arm, set()).add(t)
        return {a: sorted(v) for a, v in sorted(out.items())} or None

    def _spent_usd(self) -> float:
        """Sum of reported cost over every attempt of this run (all invocations)."""
        assert self.store is not None
        total = 0.0
        for r in self.store.records():
            if r.get("type") == "attempt" and isinstance(r.get("cost_usd"), (int, float)):
                total += float(r["cost_usd"])
        return round(total, 6)

    def _check_spend(self) -> None:
        lim = self.opt.max_total_usd
        if lim is None or self.store is None:
            return
        spent = self._spent_usd()
        if spent >= lim:
            self.halt(f"run-level USD rail: ${spent:.2f} spent >= --max-total-usd {lim:g}; no new attempts "
                      f"(in-flight ones finish). Resume with --run-id {self.run_id} and a higher limit")

    def plan(self) -> dict[str, Any]:
        """What ``run()`` would launch, without launching or writing anything (``dbwm run --dry-run``).

        Review m4: default designs are large (eval 2: 240 cells), so the cost
        exposure is printed before any money is spent: ``usd_bound_scored`` =
        cells x usd_rail (every cell scored on its first attempt, each hitting the
        rail), ``usd_bound_all_attempts`` = cells x max_attempts_per_cell x usd_rail
        (every attempt hits the rail). Both are None without a USD rail.
        """
        regime_mod.validate_regime(self.ev, self.regime)
        designed = list(self.ev.cells(self.regime))
        order = interleave(designed, self.regime.order_seed)
        by_arm: dict[str, int] = {}
        for c in designed:
            by_arm[c.arm] = by_arm.get(c.arm, 0) + 1
        self._designed = designed
        rail = self.regime.usd_rail
        n = len(designed)
        nonconf = regime_mod.nonconforming_reasons(self.regime, self.ev)
        return {
            "eval": self.ev.eval_id, "run_id": self.opt.run_id, "model": self.regime.model,
            "adapter": self.adapter_name, "isolation": self.regime.isolation.value,
            "auth_mode": self.regime.auth_mode, "ruleset": self.regime.ruleset,
            "regime_hash": self.regime_hash, "conforming": not nonconf, "nonconforming_reasons": nonconf,
            "cells": n, "cells_by_arm": dict(sorted(by_arm.items())), "reps": self.regime.reps,
            "max_turns": self.regime.max_turns, "wall_clock_s": self.regime.wall_clock_s, "usd_rail": rail,
            "max_attempts_per_cell": self.regime.max_attempts_per_cell, "concurrency": self.regime.concurrency,
            "usd_bound_scored": None if rail is None else round(n * rail, 2),
            "usd_bound_all_attempts": None if rail is None else round(n * rail * self.regime.max_attempts_per_cell, 2),
            "max_total_usd": self.opt.max_total_usd,
            "command_timeout_s": self._command_timeouts(),
            "egress_allow": self.egress_allow if self.use_container else None,
            "api_relay": bool(self.relay_auth) if self.use_container else None,
            "launch_order_head": [c.cell_id for c in order[:12]],
            "regime_extra": self.regime.extra,
        }

    # -- control ----------------------------------------------------------------------------

    def halt(self, reason: str) -> None:
        with self._lock:
            if self._halt_reason is None:
                self._halt_reason = reason
                self._log(f"[dbwm] HALT: {reason}")
        self._stop.set()

    def _install_signals(self) -> dict[int, Any]:
        if not self.opt.install_signal_handlers or threading.current_thread() is not threading.main_thread():
            return {}
        prev: dict[int, Any] = {}

        def handler(signum: int, frame: Any) -> None:  # noqa: ARG001
            if not self._stop.is_set():
                self._log("[dbwm] signal received: no new attempts; in-flight attempts will finish and "
                          "record. Send again to kill them (they will be retried on resume).")
                with self._lock:
                    if self._halt_reason is None:
                        self._halt_reason = f"interrupted by signal {signum}"
                self._stop.set()
                try:
                    self._write_manifest("interrupted")
                except Exception:  # noqa: BLE001
                    pass
            else:
                self._log("[dbwm] second signal: killing in-flight agents")
                self._abort.set()

        for s in (signal.SIGINT, signal.SIGTERM):
            try:
                prev[s] = signal.signal(s, handler)
            except (ValueError, OSError):
                pass
        return prev

    @staticmethod
    def _restore_signals(prev: Mapping[int, Any]) -> None:
        for s, h in prev.items():
            try:
                signal.signal(s, h)
            except (ValueError, OSError):
                pass

    # -- main loop ------------------------------------------------------------------------------

    def run(self) -> RunResult:
        regime_mod.validate_regime(self.ev, self.regime)
        if self.opt.preflight:
            self.preflight()
        if self.use_container:
            self._image_id = container.image_id(self.opt.image)
        self.engine_hash = compute_engine_hash(self.ev)
        self._designed = list(self.ev.cells(self.regime))
        ids = [c.cell_id for c in self._designed]
        if len(set(ids)) != len(ids):
            raise RunnerError(f"{self.ev.eval_id}.cells() returned duplicate cell ids")
        for c in self._designed:
            if c.arm not in self.ev.arms:
                raise RunnerError(f"cell {c.cell_id} names unknown arm {c.arm!r}")
        self._order = interleave(self._designed, self.regime.order_seed)
        self._open_store()
        assert self.store is not None
        self._manifest.setdefault("invocations", []).append({
            "ts": _now_iso(), "harness_version": HARNESS_VERSION, "cli_version": self.cli_version,
            "python": sys.version.split()[0], "pid": os.getpid()})
        self._write_manifest("running")
        nonconf = regime_mod.nonconforming_reasons(self.regime, self.ev)
        if nonconf:
            self._log("[dbwm] NON-CONFORMING RUN: " + "; ".join(nonconf))
        self._warn_unverified_cli()
        self._check_spend()
        self._log(f"[dbwm] run {self.run_id}: {len(self._order)} cells, adapter {self.adapter_name}, "
                  f"isolation {self.regime.isolation.value}, model {self.regime.model}, dir {self.run_dir}")
        prev = self._install_signals()
        try:
            workers = max(1, int(self.regime.concurrency))
            if workers == 1:
                for c in self._order:
                    if self._stop.is_set():
                        break
                    self._run_cell(c)
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers,
                                                           thread_name_prefix="dbwm-cell") as pool:
                    futs = [pool.submit(self._run_cell, c) for c in self._order]
                    for f in futs:
                        f.result()
        finally:
            self._restore_signals(prev)
            status = "complete"
            if self._halt_reason is not None:
                status = "interrupted" if self._halt_reason.startswith("interrupted") else "halted"
            elif self._pending_cells():
                status = "incomplete"
            self._write_manifest(status)
        return self._result(status)

    def _pending_cells(self) -> list[str]:
        assert self.store is not None
        out = []
        for c in self._designed:
            if self.store.scored_attempt(c.cell_id) is None and c.cell_id not in self._exhausted:
                out.append(c.cell_id)
        return out

    def _result(self, status: str) -> RunResult:
        assert self.store is not None
        scored = sum(1 for c in self._designed if self.store.scored_attempt(c.cell_id) is not None)
        return RunResult(run_id=self.run_id, run_dir=self.run_dir, status=status,
                         halt_reason=self._halt_reason, outcomes=dict(self._outcome_counts),
                         cells_total=len(self._designed), cells_scored=scored,
                         cells_exhausted=list(self._exhausted), attempts_this_invocation=self._attempts_run)

    def counted_attempts(self, cell_id: str) -> int:
        """Attempts that consume the cell's budget: valid ones, minus account-level infra outcomes."""
        assert self.store is not None
        acct = {o.value for o in ACCOUNT_OUTCOMES}
        return sum(1 for a in self.store.effective_attempts(cell_id)
                   if a["invalidated_by"] is None and a.get("outcome") not in acct)

    def _wait_if_paused(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                remaining = self._paused_until - time.time()
            if remaining <= 0:
                return
            self._stop.wait(min(remaining, 30.0))

    def _run_cell(self, cell: Cell) -> None:
        assert self.store is not None
        if self._stop.is_set():
            return
        try:
            with self.store.cell_lock(cell):
                while not self._stop.is_set():
                    if self.store.scored_attempt(cell.cell_id) is not None:
                        return
                    if self.counted_attempts(cell.cell_id) >= self.regime.max_attempts_per_cell:
                        with self._lock:
                            if cell.cell_id not in self._exhausted:
                                self._exhausted.append(cell.cell_id)
                        self._log(f"[dbwm] {cell.cell_id}: no scored attempt after "
                                  f"{self.regime.max_attempts_per_cell} attempts; left unscored")
                        return
                    self._wait_if_paused()
                    if self._stop.is_set():
                        return
                    outcome = self.run_attempt(cell)
                    self._after_attempt(cell, outcome)
                    if outcome in SCORED_OUTCOMES:
                        return
        except attempts_mod.CellBusy:
            self._log(f"[dbwm] {cell.cell_id}: locked by another process; skipped")

    def _after_attempt(self, cell: Cell, outcome: Outcome) -> None:
        with self._lock:
            self._outcome_counts[outcome.value] = self._outcome_counts.get(outcome.value, 0) + 1
            self._attempts_run += 1
            if outcome == Outcome.INFRA_INVALID_REGIME:
                self._consecutive_invalid += 1
            else:
                self._consecutive_invalid = 0
            n_invalid = self._consecutive_invalid
        try:
            self._write_manifest("running" if not self._stop.is_set() else "stopping")
        except Exception:  # noqa: BLE001
            pass
        self._check_spend()
        if n_invalid >= HALT_AFTER_INVALID:
            self.halt(f"{n_invalid} consecutive infra_invalid_regime attempts (last: {cell.cell_id}); "
                      "the regime as launched is not the one requested -- see the attempt's "
                      "verdict details.assertion_failures")
        elif outcome == Outcome.INFRA_AUTH:
            self.halt(f"authentication failed ({cell.cell_id}): fix the login or credentials, then "
                      f"resume with --run-id {self.run_id}; nothing is lost")
        elif outcome == Outcome.INFRA_CAP:
            if self.opt.cap_policy == "halt":
                self.halt(f"usage cap reached ({cell.cell_id}); resume with --run-id {self.run_id} "
                          "after the reset (or use --on-cap wait)")
            else:
                self._pause_for_cap(cell)

    def _pause_for_cap(self, cell: Cell) -> None:
        assert self.store is not None
        secs = None
        rec = self.store.attempt_records(cell.cell_id)
        if rec:
            res_path = self.run_dir / "cells" / cell.cell_id / f"attempt-{rec[-1]['k']}" / "agent_result.json"
            try:
                from dbwm.core.adapters.base import agent_result_from_json

                res = agent_result_from_json(json.loads(res_path.read_text(encoding="utf-8")))
                secs = outcomes.cap_reset_seconds(res)
            except Exception:  # noqa: BLE001
                secs = None
        wait = (min(secs, self.opt.max_cap_wait_s) if secs is not None else 1800.0) + self.opt.cap_wait_buffer_s
        with self._lock:
            self._paused_until = max(self._paused_until, time.time() + wait)
        at = datetime.datetime.now() + datetime.timedelta(seconds=wait)
        self._log(f"[dbwm] usage cap: pausing launches for {int(wait)}s (until ~{at:%Y-%m-%d %H:%M:%S})")

    # -- one attempt ----------------------------------------------------------------------------

    def run_attempt(self, cell: Cell) -> Outcome:
        assert self.store is not None
        k, adir = self.store.new_attempt_dir(cell)
        started = time.time()
        aid = attempts_mod.attempt_id(cell.cell_id, k)
        self.store.append({"type": "start", "attempt_id": aid, "run_id": self.run_id, "cell_id": cell.cell_id,
                           "k": k, "ts": started})
        arm = self.ev.arms[cell.arm]
        handle: sandbox.SandboxHandle | None = None
        problems: list[str] = []
        answer_values: list[str] = []
        disclosed: list[str] = []
        harness_error: str | None = None
        sandbox_seen: str | None = None
        result: AgentResult | None = None
        declared: list[str] = list(self.ev.deliverables)
        try:
            declared = deliverables_for(self.ev, cell)
            manifest = self.ev.stage(cell)
            prompt = self.ev.prompt(cell)
            answer_values = [str(v) for v in (self.ev.answer_key_values(cell) or [])]
            disclosed = disclosed_values_for(self.ev, cell)
            handle = sandbox.build_sandbox(manifest, cell, arm.tools, arm.tools.oracle_tools)
            sandbox_seen = container.AGENT_WORKDIR if self.use_container else str(handle.path)
            problems = sandbox.precheck(handle, answer_values=answer_values, prompt=prompt,
                                        disclosed_values=disclosed)
            (adir / "prompt.txt").write_text(prompt, encoding="utf-8")
            write_json(adir / "sandbox.json", {**handle.to_json(), "precheck": problems,
                                               "seen_as": sandbox_seen,
                                               **({"disclosed_values": disclosed} if disclosed else {})})
            if problems:
                self._log(f"[dbwm] {cell.cell_id} a{k}: sandbox pre-check failed: {problems[:3]}")
                result = AgentResult(transcript_path=None, stderr_path=None, returncode=None,
                                     model_requested=self.regime.model,
                                     assertion_failures=[f"sandbox pre-check: {p}" for p in problems])
            else:
                result = self._launch(cell, k, arm, handle, prompt, adir)
        except Exception as e:  # noqa: BLE001 - a harness/eval bug is recorded, never scored
            harness_error = f"{type(e).__name__}: {e}"
            (adir / "harness_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            self._log(f"[dbwm] {cell.cell_id} a{k}: harness error: {harness_error}")
            result = AgentResult(transcript_path=None, stderr_path=None, returncode=None,
                                 model_requested=self.regime.model, error_text=f"harness error: {harness_error}")
        outcome = outcomes.classify(result)
        if harness_error is not None and outcome != Outcome.CRASH:
            outcome = Outcome.CRASH
        result.outcome = outcome
        # Collect, then delete the sandbox (even when the agent never ran).
        deliv: dict[str, Any] = {"declared": list(declared), "present": {}, "missing": [],
                                 "files": [], "rejected": []}
        if handle is not None:
            try:
                deliv = sandbox.collect(handle, declared, adir / "deliverables")
            except Exception as e:  # noqa: BLE001
                deliv["error"] = f"{type(e).__name__}: {e}"
            finally:
                sandbox.cleanup(handle, keep=self.opt.keep_sandbox)
        else:
            (adir / "deliverables").mkdir(exist_ok=True)
        write_json(adir / "deliverables.json", deliv)
        write_json(adir / "agent_result.json", agent_result_to_json(result))
        # Credential values never stay in the attempt record, whatever the agent printed or
        # wrote (red-team RT-E). A hit is recorded for the audit (note credential_exposed).
        redacted = sandbox.redact_secret_values(adir, credential_values(self.regime.auth_mode))
        # The CLI's own state dirs (isolated HOME / CLAUDE_CONFIG_DIR at HOST) are redacted
        # too, but a hit there is the CLI's bookkeeping, not something the agent exposed.
        exposed = [f for f in redacted if not f.startswith(("claude-config/", "home/"))]
        if redacted:
            self._log(f"[dbwm] {cell.cell_id} a{k}: credential value redacted from {len(redacted)} file(s)")
        if exposed:
            write_json(adir / CREDENTIAL_REDACTIONS, {"files": exposed})
        if outcome == Outcome.INFRA_INVALID_REGIME and not problems:
            self._log(f"[dbwm] {cell.cell_id} a{k}: invalid regime: {result.assertion_failures[:3]}")
        score = self._score(cell, adir, outcome, deliv)
        verdict_path = self._write_verdict(cell, k, adir, outcome, result, score, deliv, problems,
                                           harness_error)
        audit_sum = self._audit(cell, adir, verdict_path, sandbox_seen, answer_values, disclosed)
        ended = time.time()
        self.store.append({
            "type": "attempt", "attempt_id": aid, "run_id": self.run_id, "cell_id": cell.cell_id,
            "cell": {"instance": cell.instance, "arm": cell.arm, "rep": cell.rep}, "k": k,
            "started": started, "ended": ended, "duration_s": round(ended - started, 3),
            "outcome": outcome.value, "scored": outcome in SCORED_OUTCOMES,
            "regime_hash": self.regime_hash, "engine_hash": self.engine_hash,
            "sandbox_sha256": handle.manifest_sha256 if handle is not None else None,
            "killed_by_harness": result.killed_by_harness, "assertion_failures": result.assertion_failures,
            "success": score.success if (score is not None and outcome in SCORED_OUTCOMES) else None,
            "deliverable_present": bool(deliv.get("files")),
            "audit_flagged": (audit_sum or {}).get("flagged"),
            "cost_usd": result.cost_usd, "turns": result.turns, "error": harness_error or (
                result.error_text if outcome not in SCORED_OUTCOMES else None),
        })
        self._log(f"[dbwm] {cell.cell_id} a{k}: {outcome.value}"
                  + (f" success={score.success}" if score is not None and outcome in SCORED_OUTCOMES else "")
                  + (f" (killed: {result.killed_by_harness})" if result.killed_by_harness else ""))
        return outcome

    def _launch(self, cell: Cell, k: int, arm: ArmSpec, handle: sandbox.SandboxHandle, prompt: str,
                adir: Path) -> AgentResult:
        specs = arm.tools.oracle_tools
        token = secrets.token_urlsafe(24) if specs else None
        server: OracleServer | None = None
        trial: container.ContainerTrial | None = None
        address: str | None = None
        proxy: str | None = None
        relay_cfg: dict[str, Any] | None = None
        relay_secret: str | None = None
        if self.use_container and self.relay_auth:
            header, var = self.relay_auth
            relay_secret = os.environ.get(var)
            relay_cfg = {"auth_header": header, "pin": self.regime.model,
                         "extra_models": list(self.regime.extra.get("api_relay_models") or []),
                         "upstream": self.regime.extra.get("api_relay_upstream") or "https://api.anthropic.com",
                         "placeholder": "dbwm-relay-" + secrets.token_urlsafe(24)}
        extra: dict[str, Any] = {"abort_event": self._abort, "eval_def": self.ev,
                                 "regime_extra": dict(self.regime.extra)}
        try:
            if specs and not self.use_container:
                ctx = OracleContext(cell=cell, arm=arm, mode=arm.oracle_mode, sandbox=handle.path, state={})
                server = OracleServer(self.ev.oracle_handlers(cell), ctx, token or "", adir / "oracle_calls.jsonl",
                                      arm.query_budget, specs=specs, scrub_paths=[str(handle.path)])
                address = server.start()
            if self.use_container:
                ocfg = None
                if specs:
                    ocfg = {"eval_id": self.ev.eval_id, "eval_module": regime_mod.eval_module(self.ev),
                            "eval_module_path": getattr(self.ev, "eval_module_path", None),
                            "cell": {"eval_id": cell.eval_id, "instance": cell.instance, "arm": cell.arm,
                                     "rep": cell.rep},
                            "mode": arm.oracle_mode, "token": token, "query_budget": arm.query_budget}
                trial = container.start_trial(
                    trial_id=f"{cell.cell_id}-a{k}", sandbox=handle.path, harness_out=adir, oracle_config=ocfg,
                    egress_allow=self.egress_allow, image=self.opt.image,
                    extra_mounts=self.opt.container_extra_mounts,
                    publish_oracle=bool(specs) and self.adapter_name == "openai_compat",
                    relay=relay_cfg, relay_credential=relay_secret)
                address = trial.oracle_address
                proxy = trial.proxy_url
                extra["container"] = trial
                if trial.host_oracle_address:
                    extra["oracle_host_address"] = trial.host_oracle_address
            cmd_timeout = command_timeout_for(self.ev, cell)
            if cmd_timeout is not None:
                extra["command_timeout_s"] = cmd_timeout
            env = {} if self.in_process else build_agent_env(self.regime.auth_mode, self.regime.isolation,
                                                             harness_out=adir, proxy_url=proxy,
                                                             extra=bash_timeout_env(cmd_timeout))
            if relay_cfg is not None and trial is not None and trial.relay_url:
                # RT-E: the agent holds a placeholder; the sidecar relay holds the key.
                env = relay_agent_env(env, self.relay_auth[1], relay_cfg["placeholder"], trial.relay_url)
            req = AgentRequest(
                cell=cell, prompt=prompt, sandbox=handle.path, tools=arm.tools,
                budget=Budget(max_turns=self.regime.max_turns, wall_clock_s=self.regime.wall_clock_s,
                              usd_rail=self.regime.usd_rail),
                model=self.regime.model, effort=self.regime.effort, isolation=self.regime.isolation,
                harness_out=adir, oracle_address=address, oracle_token=token,
                auth_mode=self.regime.auth_mode, env=env, extra=extra)
            res = self.adapter.run(req)
            if self.cli_version is None and res.cli_version:
                self.cli_version = res.cli_version
                self._warn_unverified_cli()
            return res
        finally:
            if server is not None:
                server.stop()
            if trial is not None:
                trial.teardown()
            # The token is dead now; do not leave it in runs/ (review m8).
            sandbox.redact_token_files(adir, token)

    def _score(self, cell: Cell, adir: Path, outcome: Outcome, deliv: Mapping[str, Any]) -> ScoreResult | None:
        if outcome not in SCORED_OUTCOMES:
            return None
        try:
            return self.ev.score(adir / "deliverables", cell, self.regime.ruleset)
        except Exception as e:  # noqa: BLE001 - a scorer crash is recorded as unscorable, not as a fail
            (adir / "score_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            self._log(f"[dbwm] {cell.cell_id}: scorer error: {type(e).__name__}: {e}")
            present = bool(deliv.get("files"))
            return ScoreResult(deliverable_present=present, success=None if present else False,
                               claimed_success=None, claimed_success_paper=None, claims_parse_status="error",
                               overclaim=None, ruleset=self.regime.ruleset,
                               details={"score_error": f"{type(e).__name__}: {e}"})

    def _write_verdict(self, cell: Cell, k: int, adir: Path, outcome: Outcome, result: AgentResult,
                       score: ScoreResult | None, deliv: Mapping[str, Any], problems: Sequence[str],
                       harness_error: str | None) -> Path:
        from dbwm.core import verdict as verdict_mod

        calls = summarize_calls(adir / "oracle_calls.jsonl")
        details = {
            "run_id": self.run_id,
            "adapter": self.adapter_name,
            "isolation": self.regime.isolation.value,
            "conforming": regime_mod.is_conforming(self.regime, self.ev),
            "killed_by_harness": result.killed_by_harness,
            "result_subtype": result.result_subtype,
            "assertion_failures": list(result.assertion_failures),
            "sandbox_precheck": list(problems),
            "deliverables_missing": list(deliv.get("missing") or []),
            "deliverables_rejected": list(deliv.get("rejected") or []),
            "oracle": calls,
            "permission_denials": len(result.permission_denials or []),
        }
        if self.adapter_name == "claude_code":
            from dbwm.core.adapters.claude_code import cli_version_verified, init_record

            details["cli_version_verified"] = cli_version_verified(result.cli_version or self.cli_version)
            # permissionMode and the auto-memory reading (CR-41), path-free.
            details["agent_init"] = init_record(result)
        try:
            details["command_timeout_s"] = command_timeout_for(self.ev, cell)
        except Exception as e:  # noqa: BLE001 - the launch already failed on it (harness error)
            details["command_timeout_s"] = f"error: {type(e).__name__}: {e}"
        if outcome not in SCORED_OUTCOMES:
            details["infra_error"] = harness_error or result.error_text
        v = verdict_mod.build_verdict(
            cell=cell, attempt=k, outcome=outcome, score=score, regime_hash=self.regime_hash,
            engine_hash=self.engine_hash, ruleset=self.regime.ruleset, agent_result=result,
            oracle_calls=calls["oracle_calls"], oracle_override_attempts=calls["oracle_override_attempts"],
            details=details)
        path = adir / "verdict.json"
        verdict_mod.write_verdict(path, v)
        return path

    def _audit(self, cell: Cell, adir: Path, verdict_path: Path, sandbox_seen: str | None,
               answer_values: Sequence[str], disclosed: Sequence[str] = ()) -> dict[str, Any] | None:
        from dbwm.core import audit as audit_mod
        from dbwm.core import verdict as verdict_mod

        arm = self.ev.arms[cell.arm]
        # Adapter-native names: openai_compat lists its own function names
        # (read_file, run_bash, <op>), Claude Code (and the in-process
        # adapters that emulate it) lists Read/Write/mcp__dbwm__<op>.
        expected = sorted(audit_mod.expected_tool_names(arm.tools, self.adapter_name, self.regime.isolation))
        try:
            a = audit_mod.audit_attempt(adir, self.ev, cell, sandbox=sandbox_seen,
                                        isolation=self.regime.isolation, expected_tools=expected,
                                        model_pin=self.regime.model, answer_key_values=list(answer_values),
                                        disclosed_values=list(disclosed))
            summary = audit_mod.audit_summary(a)
        except Exception as e:  # noqa: BLE001 - an audit crash excludes the cell, visibly
            (adir / "audit_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            self._log(f"[dbwm] {cell.cell_id}: audit error: {type(e).__name__}: {e}")
            summary = {"flagged": True, "reasons": ["audit_error"], "error": f"{type(e).__name__}: {e}"}
        try:
            verdict_mod.update_audit(verdict_path, summary)
        except Exception as e:  # noqa: BLE001
            (adir / "audit_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            self._log(f"[dbwm] {cell.cell_id}: could not merge audit into verdict: {e}")
        return summary


# -- convenience ----------------------------------------------------------------------------


def run_eval(eval_def: EvalDef, overrides: Mapping[str, Any] | None = None, options: RunOptions | None = None,
             *, adapter: AgentAdapter | None = None) -> RunResult:
    """Build + validate the regime and run it (the ``dbwm run`` entry point)."""
    regime = regime_mod.build_regime(eval_def, overrides)
    return Runner(eval_def, regime, options, adapter=adapter).run()


def load_manifest(run_dir: Path) -> dict[str, Any]:
    p = Path(run_dir) / MANIFEST
    if not p.is_file():
        raise RunnerError(f"{run_dir} has no {MANIFEST}")
    return json.loads(p.read_text(encoding="utf-8"))


__all__ = ["Runner", "RunOptions", "RunResult", "RunnerError", "interleave", "run_eval", "engine_sources",
           "deliverables_for", "command_timeout_for",
           "compute_engine_hash", "new_run_id", "load_manifest", "HALT_AFTER_INVALID"]
