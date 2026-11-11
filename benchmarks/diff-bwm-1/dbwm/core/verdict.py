"""Build, validate and write ``dbwm.verdict/1`` -- one JSON record per attempt.

A verdict is the only thing the reporters read. It joins the eval's pure
score of the deliverables (``ScoreResult``) with how the attempt ended
(``Outcome``), the process metrics the adapter observed, the audit, and the
provenance needed to know exactly what produced the number: the ruleset, the
regime hash and a hash of the eval's scorer sources.

Shape::

    {schema, eval, cell{instance, arm, rep, cell_id}, attempt, outcome, scored,
     ruleset, deliverable_present, success, claimed_success,
     claimed_success_paper, claims_parse_status, overclaim, model_requested,
     models_served, regime_hash, engine_hash,
     process{turns, assistant_messages, tool_calls, oracle_calls,
             oracle_override_attempts, cost_usd, duration_s, cli_version},
     audit{...}, details{...}}

Two policies are enforced here, in core, so no eval can drift from them:

* ``scored`` is derived from the outcome (``types.SCORED_OUTCOMES``). An
  unscored attempt (infra failure, crash) carries no achievement fields: they
  are set to None so an infra attempt can never be counted as a success or a
  failure by accident. The eval's details are kept for diagnosis.
* A scored attempt without its deliverable is a FAIL. If the eval returned
  ``success=None`` for a scored attempt whose deliverable is missing, the
  verdict records ``success=False`` and notes the coercion under
  ``details["core_coercions"]`` (the metric-B rule; ledger A10c).

Everything is converted to plain JSON on the way in (Decimal -> str, as the
published verdicts stored Decimals; Path -> str; enums -> value; non-finite
floats -> None) so that what is validated is exactly what is written.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import math
import os
import re
import tempfile
from decimal import Decimal
from pathlib import Path, PurePath
from typing import Any, Iterable, Mapping

from dbwm.core.types import (
    RULESETS,
    SCHEMA_VERDICT,
    SCORED_OUTCOMES,
    AgentResult,
    Cell,
    Outcome,
    ScoreResult,
)

PROCESS_KEYS = (
    "turns",
    "assistant_messages",
    "tool_calls",
    "oracle_calls",
    "oracle_override_attempts",
    "cost_usd",
    "duration_s",
    "cli_version",
)
_PROCESS_INT_KEYS = ("turns", "assistant_messages", "tool_calls", "oracle_calls", "oracle_override_attempts")
_PROCESS_FLOAT_KEYS = ("cost_usd", "duration_s")

VERDICT_KEYS = (
    "schema",
    "eval",
    "cell",
    "attempt",
    "outcome",
    "scored",
    "ruleset",
    "deliverable_present",
    "success",
    "claimed_success",
    "claimed_success_paper",
    "claims_parse_status",
    "overclaim",
    "model_requested",
    "models_served",
    "regime_hash",
    "engine_hash",
    "process",
    "audit",
    "details",
)
CLAIMS_STATUSES = ("ok", "missing", "error")
_ACHIEVEMENT_KEYS = ("success", "claimed_success", "claimed_success_paper", "overclaim")
_HEX64 = re.compile(r"[0-9a-f]{64}")


class VerdictError(ValueError):
    """A verdict violates the dbwm.verdict/1 contract."""


# ---------------------------------------------------------------------------
# JSON normalization
# ---------------------------------------------------------------------------

def to_jsonable(obj: Any) -> Any:
    """Convert to plain JSON types. Decimal -> str (exact, as the published verdicts did)."""
    if obj is None or isinstance(obj, (bool, str, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, Decimal):
        return str(obj) if obj.is_finite() else None
    if isinstance(obj, enum.Enum):
        return to_jsonable(obj.value)
    if isinstance(obj, PurePath):
        return str(obj)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Mapping):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (set, frozenset)):
        items = [to_jsonable(v) for v in obj]
        try:
            return sorted(items)
        except TypeError:
            return sorted(items, key=repr)
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if hasattr(obj, "item") and callable(obj.item):  # numpy scalars, without importing numpy
        try:
            return to_jsonable(obj.item())
        except Exception:  # noqa: BLE001
            pass
    if hasattr(obj, "tolist") and callable(obj.tolist):  # numpy arrays
        return to_jsonable(obj.tolist())
    raise TypeError(f"cannot convert {type(obj).__name__} to JSON")


def dump_json(obj: Any) -> str:
    """Canonical-ish pretty JSON used for every artifact this package writes."""
    return json.dumps(to_jsonable(obj), indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"


def atomic_write_text(path: Path, text: str) -> Path:
    """Write via a temp file + rename so a killed process never leaves half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return path


# ---------------------------------------------------------------------------
# Provenance helpers
# ---------------------------------------------------------------------------

def engine_hash(files: Iterable[str | os.PathLike], root: str | os.PathLike | None = None) -> str:
    """sha256 over the eval's scorer sources: each file's relative path and bytes, sorted by path.

    ``root`` makes the hash independent of where the package is installed
    (paths are hashed relative to it; default: the files' common parent).
    Missing files raise: a hash over a partial engine would be meaningless.
    """
    paths = [Path(f).resolve() for f in files]
    if not paths:
        raise ValueError("engine_hash needs at least one file")
    base = Path(root).resolve() if root is not None else Path(os.path.commonpath([str(p.parent) for p in paths]))
    entries = []
    for p in paths:
        if not p.is_file():
            raise FileNotFoundError(f"engine source not found: {p}")
        try:
            rel = p.relative_to(base).as_posix()
        except ValueError:
            raise ValueError(f"{p} is not under root {base}") from None
        entries.append((rel, p))
    h = hashlib.sha256()
    for rel, p in sorted(entries):
        data = p.read_bytes()
        h.update(rel.encode("utf-8") + b"\0")
        h.update(str(len(data)).encode("ascii") + b"\0")
        h.update(data)
    return h.hexdigest()


def oracle_call_counts(path: str | os.PathLike) -> tuple[int, int]:
    """(calls, override_attempts) from an ``oracle_calls.jsonl``; (0, 0) if the file is absent.

    ``calls`` counts authorised real-op calls: the lines carrying a
    ``call_index``, which the oracle server sets exactly when a request is
    charged to the query budget (including a call the handler rejected as bad
    input). ``describe``, unauthorised, malformed, unknown-op and
    budget-refused requests carry none and are not calls. This is the same rule
    as ``dbwm.core.oracle.server.summarize_calls`` (what the runner records in
    ``process.oracle_calls``) and the audit's ``counts.oracle_calls``, so the
    three never disagree.

    A line counts as an override attempt when its ``override_attempt`` field is
    truthy (a bool, or the list of ignored keys), whatever the request's fate.
    Unparseable lines raise: the log is written by the harness and a corrupt
    one must not be silently read as "fewer calls".
    """
    p = Path(path)
    if not p.exists():
        return 0, 0
    calls = overrides = 0
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError as e:
            raise VerdictError(f"{p.name}:{i}: unparseable oracle log line: {e}") from None
        if not isinstance(rec, dict):
            raise VerdictError(f"{p.name}:{i}: oracle log line is not an object")
        if rec.get("call_index") is not None:
            calls += 1
        if rec.get("override_attempt"):
            overrides += 1
    return calls, overrides


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _outcome_value(outcome: Outcome | str) -> str:
    try:
        return Outcome(outcome).value
    except ValueError:
        raise VerdictError(f"unknown outcome {outcome!r}") from None


def build_verdict(
    *,
    cell: Cell,
    attempt: int,
    outcome: Outcome | str,
    score: ScoreResult | None,
    regime_hash: str,
    engine_hash: str,
    ruleset: str | None = None,
    agent_result: AgentResult | None = None,
    model_requested: str | None = None,
    models_served: Iterable[str] | None = None,
    oracle_calls: int | None = None,
    oracle_override_attempts: int | None = None,
    audit: Mapping[str, Any] | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble and validate a verdict dict. Raises VerdictError on any contract violation.

    ``ruleset`` defaults to ``score.ruleset``; when both are given they must
    agree. ``model_requested``/``models_served`` default to the agent result's.
    ``details`` (if given) is merged over ``score.details``.
    """
    outcome_v = _outcome_value(outcome)
    scored = Outcome(outcome_v) in SCORED_OUTCOMES
    if score is not None:
        if ruleset is not None and ruleset != score.ruleset:
            raise VerdictError(f"ruleset mismatch: argument {ruleset!r} vs score {score.ruleset!r}")
        ruleset = score.ruleset
    if ruleset is None:
        raise VerdictError("ruleset is required when no score is given")

    merged_details: dict[str, Any] = {}
    if score is not None:
        merged_details.update(score.details or {})
    if details:
        merged_details.update(details)

    v: dict[str, Any] = {
        "schema": SCHEMA_VERDICT,
        "eval": cell.eval_id,
        "cell": {"instance": cell.instance, "arm": cell.arm, "rep": cell.rep, "cell_id": cell.cell_id},
        "attempt": attempt,
        "outcome": outcome_v,
        "scored": scored,
        "ruleset": ruleset,
        "deliverable_present": score.deliverable_present if score is not None else None,
        "success": None,
        "claimed_success": None,
        "claimed_success_paper": None,
        "claims_parse_status": None,
        "overclaim": None,
        "model_requested": model_requested if model_requested is not None
        else (agent_result.model_requested if agent_result is not None else None),
        "models_served": sorted(set(models_served if models_served is not None
                                    else (agent_result.models_served if agent_result is not None else []))),
        "regime_hash": regime_hash,
        "engine_hash": engine_hash,
        "process": {
            "turns": agent_result.turns if agent_result is not None else None,
            "assistant_messages": agent_result.assistant_messages if agent_result is not None else None,
            "tool_calls": len(agent_result.tool_calls) if agent_result is not None else None,
            "oracle_calls": oracle_calls,
            "oracle_override_attempts": oracle_override_attempts,
            "cost_usd": agent_result.cost_usd if agent_result is not None else None,
            "duration_s": agent_result.duration_s if agent_result is not None else None,
            "cli_version": agent_result.cli_version if agent_result is not None else None,
        },
        "audit": dict(audit or {}),
        "details": merged_details,
    }

    if scored:
        if score is None:
            raise VerdictError(f"outcome {outcome_v!r} is scored but no ScoreResult was given")
        v["success"] = score.success
        v["claimed_success"] = score.claimed_success
        v["claimed_success_paper"] = score.claimed_success_paper
        v["claims_parse_status"] = score.claims_parse_status
        v["overclaim"] = score.overclaim
        if score.deliverable_present is False and score.success is None:
            v["success"] = False
            notes = ["success None -> False: a scored attempt without its deliverable is a fail"]
            # Keep overclaim coherent with the coerced success (types.ScoreResult:
            # overclaim = claimed_success is True and success is False).
            if score.overclaim is None and score.claimed_success is not None:
                v["overclaim"] = score.claimed_success is True
                notes.append(f"overclaim None -> {v['overclaim']}: follows the coerced success")
            v["details"].setdefault("core_coercions", []).extend(notes)

    v = to_jsonable(v)
    errors = validate_verdict(v)
    if errors:
        raise VerdictError("invalid verdict for {}: {}".format(cell.cell_id, "; ".join(errors)))
    return v


# ---------------------------------------------------------------------------
# Validate / read / write
# ---------------------------------------------------------------------------

def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x: Any) -> bool:
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and (not isinstance(x, float) or math.isfinite(x)))


def validate_verdict(v: Any) -> list[str]:
    """Return a list of contract violations (empty = valid). Never raises on bad input."""
    errs: list[str] = []
    if not isinstance(v, dict):
        return [f"verdict must be an object, got {type(v).__name__}"]
    missing = [k for k in VERDICT_KEYS if k not in v]
    extra = sorted(set(v) - set(VERDICT_KEYS))
    if missing:
        errs.append(f"missing keys: {missing}")
    if extra:
        errs.append(f"unknown keys: {extra}")
    if v.get("schema") != SCHEMA_VERDICT:
        errs.append(f"schema must be {SCHEMA_VERDICT!r}, got {v.get('schema')!r}")
    if not isinstance(v.get("eval"), str) or not v.get("eval"):
        errs.append("eval must be a non-empty string")

    cell = v.get("cell")
    if not isinstance(cell, dict):
        errs.append("cell must be an object")
    else:
        if set(cell) != {"instance", "arm", "rep", "cell_id"}:
            errs.append(f"cell keys must be instance/arm/rep/cell_id, got {sorted(cell)}")
        inst, arm, rep = cell.get("instance"), cell.get("arm"), cell.get("rep")
        if not isinstance(inst, str) or not inst:
            errs.append("cell.instance must be a non-empty string")
        if not isinstance(arm, str) or not arm:
            errs.append("cell.arm must be a non-empty string")
        if not _is_int(rep) or rep < 0:
            errs.append("cell.rep must be a non-negative integer")
        if isinstance(inst, str) and isinstance(arm, str) and _is_int(rep):
            want = f"{inst}__{arm}__r{rep}"
            if cell.get("cell_id") != want:
                errs.append(f"cell.cell_id must be {want!r}, got {cell.get('cell_id')!r}")

    if not _is_int(v.get("attempt")) or v.get("attempt") < 0:
        errs.append("attempt must be a non-negative integer")

    outcome = v.get("outcome")
    try:
        out_enum = Outcome(outcome)
    except ValueError:
        out_enum = None
        errs.append(f"unknown outcome {outcome!r}")
    scored = v.get("scored")
    if not isinstance(scored, bool):
        errs.append("scored must be a boolean")
    elif out_enum is not None and scored != (out_enum in SCORED_OUTCOMES):
        errs.append(f"scored={scored} contradicts outcome {outcome!r}")

    if v.get("ruleset") not in RULESETS:
        errs.append(f"ruleset must be one of {RULESETS}, got {v.get('ruleset')!r}")

    for k in ("deliverable_present",) + _ACHIEVEMENT_KEYS:
        if k in v and v[k] is not None and not isinstance(v[k], bool):
            errs.append(f"{k} must be a boolean or null")
    cps = v.get("claims_parse_status")
    if cps is not None and cps not in CLAIMS_STATUSES:
        errs.append(f"claims_parse_status must be one of {CLAIMS_STATUSES} or null, got {cps!r}")

    if scored is True:
        if not isinstance(v.get("deliverable_present"), bool):
            errs.append("a scored verdict must say whether the deliverable was present")
        if v.get("deliverable_present") is False and v.get("success") is not False:
            errs.append("a scored verdict without its deliverable must have success=false")
        if cps is None:
            errs.append("a scored verdict must carry claims_parse_status")
    elif scored is False:
        for k in _ACHIEVEMENT_KEYS + ("claims_parse_status",):
            if v.get(k) is not None:
                errs.append(f"an unscored verdict must have {k}=null")

    mr = v.get("model_requested")
    if mr is not None and not isinstance(mr, str):
        errs.append("model_requested must be a string or null")
    ms = v.get("models_served")
    if not isinstance(ms, list) or not all(isinstance(m, str) for m in ms):
        errs.append("models_served must be a list of strings")

    for k in ("regime_hash", "engine_hash"):
        hv = v.get(k)
        if not isinstance(hv, str) or not _HEX64.fullmatch(hv):
            errs.append(f"{k} must be a lowercase sha256 hex digest")

    proc = v.get("process")
    if not isinstance(proc, dict):
        errs.append("process must be an object")
    else:
        if set(proc) != set(PROCESS_KEYS):
            errs.append(f"process keys must be {list(PROCESS_KEYS)}, got {sorted(proc)}")
        for k in _PROCESS_INT_KEYS:
            x = proc.get(k)
            if x is not None and (not _is_int(x) or x < 0):
                errs.append(f"process.{k} must be a non-negative integer or null")
        for k in _PROCESS_FLOAT_KEYS:
            x = proc.get(k)
            if x is not None and (not _is_num(x) or x < 0):
                errs.append(f"process.{k} must be a non-negative number or null")
        cv = proc.get("cli_version")
        if cv is not None and not isinstance(cv, str):
            errs.append("process.cli_version must be a string or null")

    for k in ("audit", "details"):
        if not isinstance(v.get(k), dict):
            errs.append(f"{k} must be an object")
    return errs


def write_verdict(path: str | os.PathLike, verdict: Mapping[str, Any]) -> Path:
    """Validate and atomically write ``verdict`` to ``path`` (pretty, sorted keys)."""
    v = to_jsonable(verdict)
    errors = validate_verdict(v)
    if errors:
        raise VerdictError("refusing to write invalid verdict: " + "; ".join(errors))
    return atomic_write_text(Path(path), dump_json(v))


def read_verdict(path: str | os.PathLike, *, validate: bool = True) -> dict[str, Any]:
    p = Path(path)
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise VerdictError(f"{p}: not valid JSON: {e}") from None
    if validate:
        errors = validate_verdict(v)
        if errors:
            raise VerdictError(f"{p}: " + "; ".join(errors))
    return v


def update_audit(path: str | os.PathLike, audit: Mapping[str, Any]) -> dict[str, Any]:
    """Merge an audit summary into an existing verdict file and rewrite it (validated)."""
    v = read_verdict(path)
    v["audit"] = to_jsonable(dict(audit))
    write_verdict(path, v)
    return v
