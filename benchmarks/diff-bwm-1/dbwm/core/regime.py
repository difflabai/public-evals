"""Run regime: per-eval defaults + CLI overrides -> a validated RegimeConfig.

Why: the paper's stopping rules lived in five places with different defaults
(``--max-budget-usd`` 2/3/4/8, timeouts 900/1200/1500/2400/3600),
the model was unpinned and named by five different environment variables, and nothing recorded which regime a trial actually ran under. Here
every run is defined by one RegimeConfig (types.py), built from the eval's
``dbwm/<evalN>/regime.json`` and explicit overrides, validated, hashed, and
written into the run manifest.

Precedence: RegimeConfig field defaults < regime.json < overrides (non-None).
Keys in regime.json that are not RegimeConfig fields go into ``extra``; keys
starting with "_" are comments and ignored.

Isolation rules:
    CONTAINER    satisfies every arm.
    HOST         satisfies only arms whose min_isolation is HOST (no bash).
    HOST_UNSAFE  satisfies HOST arms and bash arms, but the run is marked
                 NON-CONFORMING everywhere (answer keys share the agent's disk).
"""

from __future__ import annotations

import dataclasses
import importlib
import json
from pathlib import Path
from typing import Any, Mapping

from dbwm.core import paths
from dbwm.core.env import AUTH_MODES, auth_problems
from dbwm.core.types import ArmSpec, EvalDef, Isolation, RegimeConfig, ruleset_problem

ADAPTER_NAMES = ("claude_code", "command", "stub", "replay", "openai_compat")
EVAL_IDS = tuple(f"eval{i}" for i in range(1, 7))
_FIELDS = {f.name for f in dataclasses.fields(RegimeConfig)}
# Adapters that launch a real agent process and therefore need auth + isolation.
LIVE_ADAPTERS = ("claude_code", "command", "openai_compat")


class RegimeError(ValueError):
    pass


# -- eval discovery -------------------------------------------------------------------


def load_eval(eval_id: str | int) -> EvalDef:
    """Import ``dbwm.<evalN>`` and return its EVAL (raises on failure)."""
    name = paths.eval_name(eval_id)
    mod = importlib.import_module(f"dbwm.{name}")
    ev = getattr(mod, "EVAL", None)
    if ev is None:
        raise RegimeError(f"dbwm.{name} does not expose EVAL")
    return ev


def eval_module(eval_def: EvalDef) -> str:
    """Dotted module that exposes this EVAL (what the sidecar imports)."""
    return getattr(eval_def, "eval_module", None) or f"dbwm.{eval_def.eval_id}"


def discover_evals() -> dict[str, EvalDef | Exception]:
    """eval1..eval6 -> EVAL, or the exception that importing it raised.

    A missing or broken eval module never breaks the others.
    """
    out: dict[str, EvalDef | Exception] = {}
    for e in EVAL_IDS:
        try:
            out[e] = load_eval(e)
        except Exception as exc:  # noqa: BLE001 - reported, not fatal
            out[e] = exc
    return out


# -- regime.json -----------------------------------------------------------------------


def read_regime_file(path: Path | str) -> dict[str, Any]:
    """Parse a regime.json (comments = keys starting with '_' are dropped)."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(d, dict):
        raise RegimeError(f"{path}: regime.json must be a JSON object")
    return {k: v for k, v in d.items() if not str(k).startswith("_")}


def load_regime_json(eval_id: str | int) -> dict[str, Any]:
    """``dbwm/<evalN>/regime.json`` ({} if absent). Eval modules may call this from default_regime()."""
    p = paths.eval_package_dir(eval_id) / "regime.json"
    return read_regime_file(p) if p.is_file() else {}


def eval_defaults(eval_def: EvalDef) -> dict[str, Any]:
    try:
        d = eval_def.default_regime()
    except NotImplementedError:
        d = None
    if d is None:
        d = load_regime_json(eval_def.eval_id)
    return {k: v for k, v in dict(d).items() if not str(k).startswith("_")}


# -- isolation -----------------------------------------------------------------------


def isolation_satisfies(isolation: Isolation, arm: ArmSpec) -> bool:
    if isolation == Isolation.CONTAINER:
        return True
    if isolation == Isolation.HOST:
        return arm.min_isolation == Isolation.HOST and not arm.tools.needs_bash
    if isolation == Isolation.HOST_UNSAFE:
        return True
    return False


RELAY_AUTH = {"api_key": ("x-api-key", "ANTHROPIC_API_KEY"),
              "oauth_token": ("bearer", "CLAUDE_CODE_OAUTH_TOKEN")}


def relay_auth(regime: RegimeConfig, adapter: str | None = None) -> tuple[str, str] | None:
    """(relay auth header, credential variable) if the sidecar API relay applies, else None.

    It applies to the claude_code adapter at CONTAINER isolation with api_key or
    oauth_token auth (red-team finding RT-E; dbwm.core.api_relay), unless
    ``regime.extra["api_relay"]`` is false.
    """
    adapter = adapter or regime.adapter
    if regime.isolation != Isolation.CONTAINER or adapter != "claude_code":
        return None
    if regime.extra.get("api_relay", True) is False:
        return None
    return RELAY_AUTH.get(regime.auth_mode)


def nonconforming_reasons(regime: RegimeConfig, eval_def: EvalDef | None = None) -> list[str]:
    reasons: list[str] = []
    if (regime.isolation == Isolation.CONTAINER and regime.adapter == "claude_code"
            and regime.auth_mode in RELAY_AUTH and regime.extra.get("api_relay", True) is False):
        reasons.append("api_relay disabled: the agent container holds the real API credential, which its "
                       "Bash tool can read and use past the model pin and the USD rail (RT-E)")
    if (regime.isolation == Isolation.CONTAINER and regime.adapter == "command"
            and regime.auth_mode in ("api_key", "oauth_token", "bedrock", "vertex")):
        # The relay covers claude_code only; a command agent in the container is handed the real
        # credential and an allowlisted route to the provider, so it can call any model past the pin and the
        # USD rail -- the same reason a claude_code run with the relay disabled is non-conforming (PR #6 review).
        reasons.append(f"adapter command at isolation container with auth {regime.auth_mode}: the agent "
                       "container holds the real credential (the API relay covers claude_code only), so it "
                       "can call other models past the pin and the USD rail")
    if regime.isolation == Isolation.HOST_UNSAFE:
        reasons.append("isolation host-unsafe: the agent runs on the host with bash; "
                       "scorer-only files share its disk")
    if regime.adapter == "command" and regime.isolation == Isolation.HOST:
        # Claude Code is confined at HOST by --restricted + --tools; openai_compat implements its own
        # sandbox-confined file tools. An external command gets only cwd and DBWM_TOOLS: nothing stops
        # it from reading scorer-only files or running code, so its runs are not comparable.
        reasons.append("adapter command at isolation host: nothing confines an external command to the "
                       "sandbox or to the arm's tool policy (DBWM_TOOLS is advisory); run third-party "
                       "agents at isolation container")
    return reasons


def is_conforming(regime: RegimeConfig, eval_def: EvalDef | None = None) -> bool:
    return not nonconforming_reasons(regime, eval_def)


# -- build + validate -----------------------------------------------------------------------


def _coerce(field_name: str, value: Any) -> Any:
    if field_name == "isolation" and not isinstance(value, Isolation):
        return Isolation(str(value))
    if field_name == "arms" and isinstance(value, str):
        return [a.strip() for a in value.split(",") if a.strip()]
    if field_name == "instances" and isinstance(value, str):
        return [i.strip() for i in value.split(",") if i.strip()]
    return value


def canonical_arms(eval_def: EvalDef) -> list[str]:
    return [name for name, arm in eval_def.arms.items() if arm.canonical]


def build_regime(eval_def: EvalDef, overrides: Mapping[str, Any] | None = None, *,
                 validate: bool = True) -> RegimeConfig:
    """Merge defaults and overrides into a RegimeConfig and (by default) validate it."""
    merged: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    # An unset CLI flag (None) never clears a default.
    ovr = {k: v for k, v in dict(overrides or {}).items() if v is not None}
    for src in (eval_defaults(eval_def), ovr):
        for k, v in src.items():
            if k == "extra" and isinstance(v, Mapping):
                extra.update(v)
            elif k in _FIELDS:
                merged[k] = _coerce(k, v)
            else:
                extra[k] = v
    merged["eval_id"] = eval_def.eval_id
    merged.setdefault("arms", canonical_arms(eval_def))
    merged.setdefault("model", None)
    merged["extra"] = extra
    try:
        regime = RegimeConfig(**merged)
    except TypeError as e:
        raise RegimeError(f"bad regime: {e}") from e
    if validate:
        validate_regime(eval_def, regime)
    return regime


def validate_regime(eval_def: EvalDef, regime: RegimeConfig, *, check_auth_env: bool = True,
                    environ: Mapping[str, str] | None = None) -> None:
    """Raise RegimeError listing every problem with this regime for this eval."""
    probs: list[str] = []
    if not regime.model or not str(regime.model).strip():
        probs.append("no model: pass --model (the runner never launches with the CLI default model)")
    if not regime.arms:
        probs.append("no arms selected")
    for a in regime.arms:
        arm = eval_def.arms.get(a)
        if arm is None:
            probs.append(f"unknown arm {a!r} (eval arms: {', '.join(eval_def.arms)})")
            continue
        if not isolation_satisfies(regime.isolation, arm):
            probs.append(f"arm {a!r} needs isolation {arm.min_isolation.value} or stronger "
                         f"(bash arms need container, or host-unsafe at your own risk); "
                         f"regime isolation is {regime.isolation.value}")
    if regime.instances is not None:
        known = set(eval_def.instances())
        for i in regime.instances:
            if i not in known:
                probs.append(f"unknown instance {i!r}")
    if regime.reps < 1:
        probs.append("reps must be >= 1")
    if regime.max_turns < 1:
        probs.append("max_turns must be >= 1")
    if regime.wall_clock_s < 1:
        probs.append("wall_clock_s must be >= 1")
    if regime.usd_rail is not None and regime.usd_rail <= 0:
        probs.append("usd_rail must be > 0 when set")
    if regime.concurrency < 1:
        probs.append("concurrency must be >= 1")
    if regime.max_attempts_per_cell < 1:
        probs.append("max_attempts_per_cell must be >= 1")
    try:
        rs_problem = ruleset_problem(eval_def, regime.ruleset)
    except ValueError as e:  # the eval declares a bad ``rulesets`` (contract bug)
        rs_problem = str(e)
    if rs_problem:
        probs.append(rs_problem)
    if regime.adapter not in ADAPTER_NAMES:
        probs.append(f"unknown adapter {regime.adapter!r} (choose {', '.join(ADAPTER_NAMES)})")
    if regime.auth_mode not in AUTH_MODES:
        probs.append(f"unknown auth mode {regime.auth_mode!r}")
    elif regime.adapter == "claude_code" and check_auth_env:
        probs.extend(auth_problems(regime.auth_mode, regime.isolation, environ))
    if probs:
        raise RegimeError("invalid regime:\n  - " + "\n  - ".join(probs))


def regime_diff(a: RegimeConfig, b: RegimeConfig) -> dict[str, tuple[Any, Any]]:
    ja, jb = a.to_json(), b.to_json()
    return {k: (ja.get(k), jb.get(k)) for k in sorted(set(ja) | set(jb)) if ja.get(k) != jb.get(k)}
