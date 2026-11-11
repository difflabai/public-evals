"""One outcome classifier for every eval and adapter, from structured fields only.

Why: each paper runner had its own idea of "this trial didn't count". The
cap detector matched the agent's own final text for "usage limit"
(coupled_planner.py:587-596; ledger N16), so a completed trial that wrote
those words would be thrown away and retried; a logged-out CLI ("Not logged
in", $0, 1 turn) was scored as a genuine failure in four of five runners
(G04, fixed only in run_confirmatory.py:81-110); natural timeouts were once
retried until success (G02). Here the decision uses the result event's
subtype/is_error/num_turns/total_cost_usd, the harness kill reason and the
runtime assertions. Free text is consulted only inside an is_error result (or
stderr with no result at all), where it is the CLI's message, not the model's.

Order of precedence:
  1. any runtime assertion failure                     -> INFRA_INVALID_REGIME
  2. harness kill: clock -> CLOCK_STOP, turns -> BUDGET_EXHAUSTED,
     cost -> COST_STOP, invalid_regime -> INFRA_INVALID_REGIME
     (an operator interrupt, "interrupt", falls through: with no result
     event it is a CRASH, i.e. retryable, never the agent's failure)
  3. no result event: auth message in stderr -> INFRA_AUTH; else CRASH
  4. subtype error_max_turns -> BUDGET_EXHAUSTED; error_max_budget_usd -> COST_STOP
  5. is_error:
       auth (api_error_status 401/403, or "Not logged in"/invalid key text)
         with cost 0                                                       -> INFRA_AUTH
       usage cap (rate_limit_event status "rejected", or usage-limit text)
         AND num_turns <= 1 AND cost == 0                                  -> INFRA_CAP
       api_error_status 429/5xx/529, or 5xx/overloaded/connection text     -> INFRA_API
       anything else                                                       -> CRASH (retryable)
  6. otherwise                                                              -> COMPLETED
A COMPLETED trial is COMPLETED whatever its final text says, and a capped
session that already did work (turns > 1 or cost > 0) is not INFRA_CAP: it
falls to INFRA_API/CRASH and is retried like any other infra failure.

Structured fields first: ``api_error_status`` is a result-event key and the
``rate_limit_event`` (status, resetsAt) is a stream event on 2.1.287; the
text patterns are the fallback for older CLIs and other adapters.
"""

from __future__ import annotations

import datetime
import re
import time
from typing import Any

from dbwm.core.types import AgentResult, Outcome

AUTH_PATTERNS = ("not logged in", "please run /login", "invalid api key", "authentication_error",
                 "oauth token has expired", "invalid x-api-key", "401 unauthorized", "status 401",
                 "api error: 401", "\"status\":401", "credit balance is too low")
CAP_PATTERNS = ("hit your limit", "usage limit", "limit · resets", "limit reached")
API_PATTERNS = ("overloaded", "internal server error", "api_error", "status 500", "status 502",
                "status 503", "status 504", "status 529", "api error: 500", "api error: 502",
                "api error: 503", "api error: 504", "api error: 529", "api error: 429",
                "rate_limit_error", "connection error", "econnreset", "etimedout",
                "socket hang up", "fetch failed", "request timed out", "service unavailable",
                "bad gateway", "gateway timeout")
AUTH_STATUSES = (401, 403)
API_STATUSES = (408, 429, 500, 502, 503, 504, 529)


def _has(text: str, patterns: tuple[str, ...]) -> bool:
    low = (text or "").lower()
    return any(p in low for p in patterns)


def _stderr_text(result: AgentResult, limit: int = 65536) -> str:
    p = result.stderr_path
    if p is None:
        return ""
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def _error_text(result: AgentResult) -> str:
    parts = [result.error_text or ""]
    r = result.raw_result_event or {}
    if isinstance(r.get("result"), str):
        parts.append(r["result"])
    if isinstance(r.get("errors"), list):
        parts.extend(str(e) for e in r["errors"])
    if r.get("terminal_reason"):
        parts.append(str(r.get("terminal_reason")))
    return "\n".join(p for p in parts if p)


def _status(result: AgentResult) -> int | None:
    v = (result.raw_result_event or {}).get("api_error_status")
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def rate_limit_info(result: AgentResult) -> dict[str, Any] | None:
    """The last ``rate_limit_event.rate_limit_info`` in the transcript (None if absent)."""
    p = result.transcript_path
    if p is None:
        return None
    from dbwm.core import stream_json  # local: stream_json imports types only

    try:
        return stream_json.parse_file(p).rate_limit
    except OSError:
        return None


def _zero(x: float | None) -> bool:
    return x is None or x == 0


def classify(result: AgentResult) -> Outcome:
    """Map an AgentResult to an Outcome (see module docstring for the rules)."""
    if result.assertion_failures:
        return Outcome.INFRA_INVALID_REGIME
    kill = result.killed_by_harness
    if kill == "clock":
        return Outcome.CLOCK_STOP
    if kill == "turns":
        return Outcome.BUDGET_EXHAUSTED
    if kill == "cost":
        return Outcome.COST_STOP
    if kill == "invalid_regime":
        return Outcome.INFRA_INVALID_REGIME
    r = result.raw_result_event
    if r is None and result.result_subtype is None:
        if _has(_stderr_text(result) + "\n" + (result.error_text or ""), AUTH_PATTERNS):
            return Outcome.INFRA_AUTH
        return Outcome.CRASH
    subtype = result.result_subtype if result.result_subtype is not None else (r or {}).get("subtype")
    if subtype == "error_max_turns":
        return Outcome.BUDGET_EXHAUSTED
    if subtype == "error_max_budget_usd":
        return Outcome.COST_STOP
    is_error = result.is_error if result.is_error is not None else bool((r or {}).get("is_error"))
    if is_error:
        text = _error_text(result)
        status = _status(result)
        cost = result.cost_usd
        turns = result.turns
        no_work = (turns is None or turns <= 1) and _zero(cost)
        if (status in AUTH_STATUSES or _has(text, AUTH_PATTERNS)) and _zero(cost):
            return Outcome.INFRA_AUTH
        rl = rate_limit_info(result) or {}
        if no_work and (rl.get("status") == "rejected" or _has(text, CAP_PATTERNS)):
            return Outcome.INFRA_CAP
        if status in API_STATUSES or _has(text, API_PATTERNS):
            return Outcome.INFRA_API
        return Outcome.CRASH
    return Outcome.COMPLETED


def cap_reset_seconds(result: AgentResult, now: float | None = None) -> float | None:
    """Seconds until the usage cap resets: ``rate_limit_info.resetsAt`` if present, else the text."""
    rl = rate_limit_info(result) or {}
    at = rl.get("resetsAt")
    if isinstance(at, (int, float)) and at > 0:
        return max(0.0, float(at) - (time.time() if now is None else now))
    return parse_reset_seconds(cap_message(result))


# -- usage-cap reset parsing (ported from run_confirmatory.py:157-215) ----------------

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def parse_reset_seconds(message: str | None, now: datetime.datetime | None = None) -> float | None:
    """Seconds until the reset time named in a usage-limit message; None if unparseable.

    Two forms (the weekly one was originally unparsed and caused ~130 wasted
    re-probes over three days; ledger G06):
        "You've hit your limit · resets 4:30am (America/Denver)"
        "You've hit your limit · resets Sep 18 at 1am (America/Denver)"
    The time zone in parentheses is ignored; local time is assumed, as in the
    original parser.
    """
    if not message:
        return None
    low = message.lower()
    now = now or datetime.datetime.now()
    m = re.search(r"resets\s+([a-z]{3,9})\s+(\d{1,2})\s+at\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)", low)
    if m:
        mon = _MONTHS.get(m.group(1)[:3])
        if mon:
            day, hh, mm, ap = int(m.group(2)), int(m.group(3)), int(m.group(4) or 0), m.group(5)
            hh = _to_24h(hh, ap)
            for yr in (now.year, now.year + 1):
                try:
                    target = datetime.datetime(yr, mon, day, hh, mm)
                except ValueError:
                    continue
                if target > now:
                    return max(0.0, (target - now).total_seconds())
    m = re.search(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)", low)
    if not m:
        return None
    hh, mm = _to_24h(int(m.group(1)), m.group(3)), int(m.group(2) or 0)
    if hh > 23 or mm > 59:
        return None
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    return max(0.0, (target - now).total_seconds())


def _to_24h(hh: int, ap: str) -> int:
    if ap == "pm" and hh != 12:
        hh += 12
    if ap == "am" and hh == 12:
        hh = 0
    return hh


def cap_message(result: AgentResult) -> str:
    """The CLI's usage-limit text for an INFRA_CAP attempt (for the reset parser)."""
    return _error_text(result) or _stderr_text(result)


def outcome_from_value(v: str | Outcome | None) -> Outcome | None:
    if v is None or isinstance(v, Outcome):
        return v
    return Outcome(v)


__all__ = ["classify", "parse_reset_seconds", "cap_message", "cap_reset_seconds", "rate_limit_info",
           "outcome_from_value",
           "AUTH_PATTERNS", "CAP_PATTERNS", "API_PATTERNS"]
