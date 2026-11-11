"""Reading an eval-1 claims.json: the paper's object and the strict v1 reading.

Both readings come from :mod:`dbwm.core.claims` (the benchmark-wide claims
parser); this module only fixes eval 1's field names and reproduces how the
paper pipeline handed the file to its scorer.

Fields (planner.py:104-109 asks for exactly these):
  goal_met, constraints_satisfied   booleans; ``success`` is the published
                                    fallback when either is absent
                                    (oracle.py:324-325, 363-364)
  projected_final_revenue           number, optional
  rationale                         free text, copied into the verdict

Paper pipeline (planner.py:544-549 then oracle.py score_plan): a missing or
unparseable claims.json became ``{}``; any parsed value was passed through,
and ``score_plan`` did ``claims = claims or {}`` then ``claims.get(...)``. So a
truthy non-object (a list, a string) crashed the scorer and the trial got NO
verdict; ``bool()`` decided the booleans (the string "false" claims success); ``Decimal(str(v))`` read the forecast and silently dropped
"$10,078,177" (None).

Strict reading (ruleset v1): JSON booleans only; numbers as JSON numbers or
numeric strings after stripping ``$ , ~``; unit suffixes ("10.08M") are errors;
the parse status ("ok" | "missing" | "error") is recorded in every verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from dbwm.core import claims as core_claims

BOOL_FIELDS = ("goal_met", "constraints_satisfied")
FORECAST_FIELD = "projected_final_revenue"
FIELDS = {"goal_met": core_claims.BOOL, "constraints_satisfied": core_claims.BOOL,
          FORECAST_FIELD: core_claims.NUMBER}
ALIASES = dict(core_claims.PAPER_ALIASES)


@dataclass
class Eval1Claims:
    """Both readings of one claims.json."""

    paper_obj: Any  # what the paper pipeline passed to score_plan ({} if absent/unparseable)
    status: str  # strict parse status: "ok" | "missing" | "error"
    errors: list[str] = field(default_factory=list)
    goal_met: bool | None = None  # strict
    constraints_satisfied: bool | None = None  # strict
    claimed_success: bool | None = None  # strict: True iff both True; False if either False
    forecast: Decimal | None = None  # strict projected_final_revenue
    forecast_status: str = "missing"  # "ok" | "missing" | "error"
    claimed_success_paper: bool | None = None  # None where the paper scorer crashed
    rationale: Any = ""


def _forecast_status(obj: Any, strict_value: Any, errors: list[str]) -> str:
    if any(e.startswith(f"field {FORECAST_FIELD!r}") for e in errors):
        return "error"
    if strict_value is not None:
        return "ok"
    return "missing"


def paper_claimed_success(obj: Any) -> bool | None:
    """oracle.py's claimed_success for the object score_plan received; None = it crashed."""
    claims = obj or {}
    if not isinstance(claims, dict):
        return None  # ``claims.get`` raised AttributeError: the paper wrote no verdict
    claimed_goal = bool(claims.get("goal_met", claims.get("success")))
    claimed_cons = bool(claims.get("constraints_satisfied", claims.get("success")))
    return bool(claimed_goal and claimed_cons)


def read_claims(source: Any) -> Eval1Claims:
    """Read claims from a path (or an already-parsed object, for tests)."""
    if isinstance(source, (str, Path)) and not Path(source).exists():
        parsed = core_claims.parse_claims_detailed(None, FIELDS, aliases=ALIASES)
        parsed.status = core_claims.STATUS_MISSING
        paper_obj: Any = {}
    else:
        parsed = core_claims.parse_claims_detailed(source, FIELDS, aliases=ALIASES)
        paper_obj = parsed.raw if parsed.raw is not None else {}
    strict = parsed.strict
    claimed = core_claims.claimed_all({k: strict[k] for k in BOOL_FIELDS}, BOOL_FIELDS)
    rationale = paper_obj.get("rationale", "") if isinstance(paper_obj, dict) else ""
    return Eval1Claims(
        paper_obj=paper_obj,
        status=parsed.status,
        errors=list(parsed.errors) if parsed.status != core_claims.STATUS_MISSING else [],
        goal_met=strict["goal_met"],
        constraints_satisfied=strict["constraints_satisfied"],
        claimed_success=claimed,
        forecast=strict[FORECAST_FIELD],
        forecast_status=_forecast_status(paper_obj, strict[FORECAST_FIELD], list(parsed.errors)),
        claimed_success_paper=paper_claimed_success(paper_obj),
        rationale=rationale)
