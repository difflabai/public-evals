"""LLM judges (eval 4 rubric panel, eval 5 pairwise ELO, eval 6 D2 rank-off).

A judge is a pure function of the text it is handed. The published judges were
not: they ran ``claude -p <prompt> --allowedTools ""``, and that flag is
ADDITIVE to the operator's ~/.claude/settings.json, which allowed Bash, Read
and more, so a "blind" judge could open the grading material (paper Appendix
A, entry 13). They also inherited the launcher's working
directory -- the harness repo, holding the rubric, checks.jsonl, answer keys
and the eval-6 generators (judge.py:122, pairwise_judge.py:245,
d2_judge.py:144) -- and discarded the stream, so whether any judge used a tool
cannot be audited after the fact.

Adapters here:

``claude_cli`` (reference)
    ``claude -p --model <pinned> --tools "" --restricted --strict-mcp-config
    --disable-slash-commands --no-session-persistence --max-turns 2
    --output-format stream-json --verbose [--system-prompt-file F]
    [--json-schema S]``, prompt on stdin, env allowlist, cwd = a fresh EMPTY
    temp directory. The full stream is saved. The pass is a ``judge_error``
    unless the init event reports no tools, no tool_use block appears, no
    permission was denied, the cwd is still empty afterwards and the output
    parses against the schema. ``--json-schema`` is implemented by the CLI
    through a synthetic ``StructuredOutput`` tool; that one name is tolerated
    in the init tool list and as a tool_use when (and only when) a schema was
    passed, because it is the output channel, not a capability.
``anthropic_api``
    stdlib urllib POST /v1/messages, structured output through a forced tool
    (``tool_choice: {type: tool}``). Models that reject forced tool use
    (HTTP 400 naming tool_choice) are retried once with
    ``output_config.format`` (JSON schema output) in ``structured="auto"``.
``openai_compat``
    stdlib urllib POST <base_url>/chat/completions with ``tools: []`` (the
    paper's eval-6 GLM 5.1 D2 judge, over Fireworks); the judgment is parsed
    from the reply text. A reply with ``tool_calls`` is a judge_error.
``mock``
    fixture map / sequence / callable, for pipelines and tests.

Judge errors are never failures: ``panel_majority`` returns None for a tie or
too few valid votes, and reporters count such items as ``judge_error``
(: capped or unparseable judge passes were once scored as fails).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

import dbwm
from dbwm.core.types import Isolation

STRUCTURED_OUTPUT_TOOL = "StructuredOutput"
TOOL_USE_BLOCK_TYPES = ("tool_use", "server_tool_use", "mcp_tool_use")
DEFAULT_MAX_TURNS = 2
DEFAULT_TIMEOUT_S = 420
USER_AGENT = f"diff-bwm-1/{dbwm.__version__}"

# Error classes on JudgeResult.error_class.
ERR_ISOLATION = "isolation"  # the judge had or used a capability it must not have
ERR_PARSE = "parse"  # it answered, but not in the required shape
ERR_REFUSAL = "refusal"  # the model declined
ERR_INFRA = "infra"  # auth, usage cap, API error, timeout, missing CLI: retryable


@dataclass
class JudgeResult:
    parsed: dict[str, Any] | None  # the judgment, validated against the schema; None on error
    raw_path: Path | None  # the saved stream / raw response
    tool_use_count: int  # tool_use blocks other than the structured-output channel
    cost_usd: float | None
    turns: int | None
    error: str | None = None  # None = valid judgment
    error_class: str | None = None
    model_served: list[str] = field(default_factory=list)
    init_tools: list[str] | None = None
    structured_output_calls: int = 0
    duration_s: float | None = None
    adapter: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None and self.parsed is not None

    def to_json(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "ok": self.ok,
            "parsed": self.parsed,
            "raw_path": self.raw_path.name if self.raw_path else None,
            "tool_use_count": self.tool_use_count,
            "structured_output_calls": self.structured_output_calls,
            "cost_usd": self.cost_usd,
            "turns": self.turns,
            "error": self.error,
            "error_class": self.error_class,
            "model_served": list(self.model_served),
            "init_tools": self.init_tools,
            "duration_s": self.duration_s,
        }


@runtime_checkable
class JudgeAdapter(Protocol):
    name: str

    def judge(self, system: str, user: str, schema: Mapping[str, Any] | None, model: str) -> JudgeResult: ...


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------

def panel_majority(votes: Sequence[bool | None], min_valid: int = 2) -> bool | None:
    """Majority of the valid (non-None) votes; None on a tie or fewer than ``min_valid`` valid votes.

    None means judge_error for that item: reported, never counted as a fail.
    """
    valid = []
    for v in votes:
        if v is None:
            continue
        if not isinstance(v, bool):
            raise TypeError(f"panel votes must be True/False/None, got {v!r}")
        valid.append(v)
    if len(valid) < min_valid:
        return None
    t = sum(1 for v in valid if v)
    f = len(valid) - t
    if t == f:
        return None
    return t > f


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------

def extract_last_json_object(text: str, required_key: str | None = None) -> dict[str, Any] | None:
    """The LAST top-level ``{...}`` in ``text`` that parses (and contains ``required_key``).

    Brace matching that respects JSON strings, so reasoning that itself
    contains braces does not truncate the object (the published eval-4 judge
    used ``\\{[^{}]*"passed"[^{}]*\\}`` and turned such answers into nulls
    counted as fails).
    """
    if not text:
        return None
    best: tuple[int, int, dict[str, Any]] | None = None  # (end, -start, obj)
    for start in range(len(text) - 1, -1, -1):
        if text[start] != "{":
            continue
        end = _matching_brace(text, start)
        if end is None or (best is not None and end < best[0]):
            continue
        try:
            obj = json.loads(text[start:end])
        except ValueError:
            continue
        if not isinstance(obj, dict) or (required_key is not None and required_key not in obj):
            continue
        # Prefer the object that ends last; among those, the outermost (smallest start).
        if best is None or (end, -start) > (best[0], best[1]):
            best = (end, -start, obj)
    return best[2] if best is not None else None


def _matching_brace(text: str, start: int) -> int | None:
    """Index one past the brace closing the object that opens at ``start`` (JSON-string aware)."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


_JSON_TYPES: dict[str, Callable[[Any], bool]] = {
    "object": lambda x: isinstance(x, dict),
    "array": lambda x: isinstance(x, list),
    "string": lambda x: isinstance(x, str),
    "boolean": lambda x: isinstance(x, bool),
    "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
    "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
    "null": lambda x: x is None,
}


def validate_against_schema(obj: Any, schema: Mapping[str, Any] | None, path: str = "$") -> list[str]:
    """Minimal JSON-Schema check: type, enum, const, required, properties, items, additionalProperties.

    Enough for judge output schemas (flat objects with enums and booleans);
    unknown keywords are ignored rather than guessed at.
    """
    if not schema:
        return []
    errs: list[str] = []
    t = schema.get("type")
    if t is not None:
        types = [t] if isinstance(t, str) else list(t)
        if not any(_JSON_TYPES.get(tt, lambda x: False)(obj) for tt in types):
            return [f"{path}: expected {t}, got {type(obj).__name__}"]
    if "enum" in schema and obj not in schema["enum"]:
        errs.append(f"{path}: {obj!r} not in {schema['enum']}")
    if "const" in schema and obj != schema["const"]:
        errs.append(f"{path}: {obj!r} != {schema['const']!r}")
    if isinstance(obj, dict):
        for k in schema.get("required", []) or []:
            if k not in obj:
                errs.append(f"{path}: missing required key {k!r}")
        props = schema.get("properties") or {}
        for k, sub in props.items():
            if k in obj:
                errs.extend(validate_against_schema(obj[k], sub, f"{path}.{k}"))
        if schema.get("additionalProperties") is False:
            extra = sorted(set(obj) - set(props))
            if extra:
                errs.append(f"{path}: unexpected keys {extra}")
    if isinstance(obj, list) and isinstance(schema.get("items"), Mapping):
        for i, item in enumerate(obj):
            errs.extend(validate_against_schema(item, schema["items"], f"{path}[{i}]"))
    return errs


def _first_required_key(schema: Mapping[str, Any] | None) -> str | None:
    if schema and isinstance(schema.get("required"), list) and schema["required"]:
        return str(schema["required"][0])
    return None


def _stem(out_dir: Path, label: str | None, model: str, system: str, user: str,
          schema: Mapping[str, Any] | None) -> Path:
    h = hashlib.sha256()
    for part in (model, system, user, json.dumps(schema, sort_keys=True) if schema else ""):
        h.update(part.encode("utf-8") + b"\0")
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in (label or "judge"))[:80]
    base = f"{safe}-{h.hexdigest()[:12]}"
    # Reserve the stem atomically: repeated passes of the same prompt (panels,
    # ELO repeats) may run on concurrent threads, and two passes must never
    # share files. The placeholder is overwritten by the final result record.
    k = 1
    while True:
        stem = out_dir / (base if k == 1 else f"{base}-{k}")
        try:
            fd = os.open(str(stem) + ".result.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            k += 1
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write('{"status": "running"}\n')
        return stem


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# claude_cli
# ---------------------------------------------------------------------------

_CAP_MARKERS = ("usage limit", "hit your limit", "limit · resets", "limit reached")
_AUTH_MARKERS = ("not logged in", "invalid api key", "authentication_error", "401", "please run /login")
_API_MARKERS = ("api error", "overloaded", "internal server error", "529", "500", "502", "503", "504")


def _same_dir(a: str | None, b: Path) -> bool:
    if not a:
        return False
    try:
        return os.path.realpath(a) == os.path.realpath(str(b))
    except OSError:
        return False


class ClaudeCliJudge:
    """Reference judge: a tool-less ``claude -p`` session in an empty directory."""

    name = "claude_cli"

    def __init__(
        self,
        out_dir: str | os.PathLike,
        *,
        claude_bin: str | Sequence[str] = "claude",
        auth_mode: str = "session",
        env: Mapping[str, str] | None = None,
        extra_env: Mapping[str, str] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_turns: int = DEFAULT_MAX_TURNS,
        max_budget_usd: float | None = None,
        effort: str | None = None,
        use_json_schema: bool = True,
        tmp_root: str | os.PathLike | None = None):
        self.out_dir = Path(out_dir)
        self.claude_cmd = [claude_bin] if isinstance(claude_bin, str) else list(claude_bin)
        self.auth_mode = auth_mode
        self.env = dict(env) if env is not None else None
        self.extra_env = dict(extra_env or {})
        self.timeout_s = timeout_s
        self.max_turns = max_turns
        self.max_budget_usd = max_budget_usd
        self.effort = effort
        self.use_json_schema = use_json_schema
        self.tmp_root = str(tmp_root) if tmp_root is not None else None

    # -- command and environment -------------------------------------------

    def build_argv(self, model: str, system_file: Path | None, schema: Mapping[str, Any] | None) -> list[str]:
        if not model:
            raise ValueError("a pinned judge model is required")
        argv = self.claude_cmd + [
            "-p",
            "--model", model,
            "--tools", "",
            "--restricted",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--max-turns", str(self.max_turns),
            "--output-format", "stream-json",
            "--verbose",
        ]
        if system_file is not None:
            argv += ["--system-prompt-file", str(system_file)]
        if schema is not None and self.use_json_schema:
            argv += ["--json-schema", json.dumps(schema, sort_keys=True, separators=(",", ":"))]
        if self.effort:
            argv += ["--effort", self.effort]
        if self.max_budget_usd is not None:
            argv += ["--max-budget-usd", str(self.max_budget_usd)]
        if self.auth_mode == "api_key":
            # Unlike agents (claude_code adapter: --bare drops Write/Glob/Grep), the
            # judge has --tools "" and --bare keeps the synthetic StructuredOutput tool:
            # checked on 2.1.287 against a local fake API (review M1, 2026-10-02).
            argv += ["--bare"]
        return argv

    def build_env(self, harness_dir: Path) -> dict[str, str]:
        if self.env is not None:
            env = dict(self.env)
        else:
            from dbwm.core import env as core_env

            env = core_env.build_agent_env(self.auth_mode, Isolation.HOST, harness_out=harness_dir)
        env.update(self.extra_env)
        return env

    # -- run ---------------------------------------------------------------

    def judge(self, system: str, user: str, schema: Mapping[str, Any] | None, model: str,
              *, label: str | None = None) -> JudgeResult:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        stem = _stem(self.out_dir, label, model, system, user, schema)
        raw_path = stem.with_name(stem.name + ".stream.jsonl")
        stderr_path = stem.with_name(stem.name + ".stderr.txt")
        system_file = None
        if system:
            system_file = stem.with_name(stem.name + ".system.txt")
            system_file.write_text(system, encoding="utf-8")
        stem.with_name(stem.name + ".user.txt").write_text(user, encoding="utf-8")

        res = JudgeResult(parsed=None, raw_path=raw_path, tool_use_count=0, cost_usd=None, turns=None,
                          adapter=self.name)
        cwd = Path(tempfile.mkdtemp(prefix="dbwm_judge_", dir=self.tmp_root))
        harness_dir = Path(tempfile.mkdtemp(prefix="dbwm_judge_cfg_", dir=self.tmp_root))
        t0 = time.monotonic()
        try:
            try:
                from dbwm.core.paths import assert_outside_repos

                assert_outside_repos(cwd)
            except ImportError:  # pragma: no cover - paths.py is part of core
                pass
            except Exception as e:  # noqa: BLE001
                return self._finish(res, stem, t0, f"judge cwd is inside a repository: {e}", ERR_ISOLATION)

            argv = self.build_argv(model, system_file, schema)
            env = self.build_env(harness_dir)
            timed_out = False
            try:
                with open(raw_path, "wb") as out, open(stderr_path, "wb") as err:
                    proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.PIPE,
                                            stdout=out, stderr=err, start_new_session=True)
                    try:
                        proc.communicate(input=user.encode("utf-8"), timeout=self.timeout_s)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        _kill_group(proc)
            except FileNotFoundError as e:
                return self._finish(res, stem, t0, f"judge CLI not found: {e}", ERR_INFRA)

            leftover = sorted(p.name for p in cwd.iterdir())
            self._parse_stream(res, raw_path, stderr_path, cwd, schema, timed_out, leftover)
        finally:
            shutil.rmtree(cwd, ignore_errors=True)
            shutil.rmtree(harness_dir, ignore_errors=True)
        return self._finish(res, stem, t0, res.error, res.error_class)

    def _finish(self, res: JudgeResult, stem: Path, t0: float, error: str | None,
                error_class: str | None) -> JudgeResult:
        res.error = error
        res.error_class = error_class if error else None
        if error:
            res.parsed = None
        res.duration_s = round(time.monotonic() - t0, 3)
        _write_json(stem.with_name(stem.name + ".result.json"), res.to_json())
        return res

    def _parse_stream(self, res: JudgeResult, raw_path: Path, stderr_path: Path, cwd: Path,
                      schema: Mapping[str, Any] | None, timed_out: bool, leftover: list[str]) -> None:
        init = None
        result_ev = None
        tool_ids: set[str] = set()
        anon_tool_uses = 0
        so_calls = 0
        so_input = None
        served: set[str] = set()
        bad_lines = 0
        for line in raw_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                bad_lines += 1
                continue
            if not isinstance(ev, dict):
                continue
            typ = ev.get("type")
            if typ == "system" and ev.get("subtype") == "init" and init is None:
                init = ev
            elif typ == "assistant":
                msg = ev.get("message") or {}
                if msg.get("model"):
                    served.add(str(msg["model"]))
                for block in msg.get("content") or []:
                    if not isinstance(block, dict) or block.get("type") not in TOOL_USE_BLOCK_TYPES:
                        continue
                    if block.get("name") == STRUCTURED_OUTPUT_TOOL and schema is not None:
                        so_calls += 1
                        so_input = block.get("input")
                        continue
                    bid = block.get("id")
                    if bid:
                        tool_ids.add(str(bid))
                    else:
                        anon_tool_uses += 1
            elif typ == "result":
                result_ev = ev
        res.tool_use_count = len(tool_ids) + anon_tool_uses
        res.structured_output_calls = so_calls

        if init is not None:
            res.init_tools = list(init.get("tools") or [])
            if init.get("model"):
                served.add(str(init["model"]))
        if result_ev is not None:
            res.cost_usd = result_ev.get("total_cost_usd")
            res.turns = result_ev.get("num_turns")
            mu = result_ev.get("modelUsage")
            if isinstance(mu, dict):
                served.update(str(k) for k in mu)
        res.model_served = sorted(served)

        # Isolation first: a judge that could act is invalid whatever it answered.
        if res.tool_use_count:
            res.error, res.error_class = f"judge used tools ({res.tool_use_count} tool_use)", ERR_ISOLATION
            return
        if result_ev is not None and result_ev.get("permission_denials"):
            res.error, res.error_class = "judge attempted tools (permission_denials non-empty)", ERR_ISOLATION
            return
        if leftover:
            res.error, res.error_class = f"judge cwd modified: {leftover[:5]}", ERR_ISOLATION
            return
        if init is not None:
            allowed = {STRUCTURED_OUTPUT_TOOL} if schema is not None else set()
            extra_tools = sorted(set(res.init_tools or []) - allowed)
            if extra_tools:
                res.error, res.error_class = f"judge init.tools not empty: {extra_tools}", ERR_ISOLATION
                return
            if init.get("mcp_servers"):
                res.error, res.error_class = "judge init.mcp_servers not empty", ERR_ISOLATION
                return
            if init.get("skills"):
                res.error, res.error_class = "judge init.skills not empty", ERR_ISOLATION
                return
            foreign = [p for p in (init.get("plugins") or []) if not _builtin_plugin(p)]
            if foreign:
                res.error, res.error_class = f"judge init.plugins has operator plugins: {foreign[:3]}", ERR_ISOLATION
                return
            if "cwd" in init and not _same_dir(init.get("cwd"), cwd):
                res.error, res.error_class = "judge init.cwd is not the empty judge directory", ERR_ISOLATION
                return

        if timed_out:
            res.error, res.error_class = f"judge timed out after {self.timeout_s}s", ERR_INFRA
            return
        if init is None:
            stderr = stderr_path.read_text(encoding="utf-8", errors="replace")[-500:] if stderr_path.exists() else ""
            res.error, res.error_class = f"no init event in judge stream (stderr tail: {stderr.strip()!r})", ERR_INFRA
            return
        if result_ev is None:
            res.error, res.error_class = "no result event in judge stream", ERR_INFRA
            return

        text = result_ev.get("result") or ""
        if result_ev.get("is_error"):
            low = (text + " " + " ".join(str(e) for e in (result_ev.get("errors") or []))).lower()
            cost = result_ev.get("total_cost_usd") or 0
            if any(m in low for m in _CAP_MARKERS) and (res.turns or 0) <= 1 and not cost:
                res.error = "infra_cap: usage limit reached"
            elif any(m in low for m in _AUTH_MARKERS) and not cost:
                res.error = "infra_auth: not authenticated"
            elif any(m in low for m in _API_MARKERS):
                res.error = f"infra_api: {text[:200]!r}"
            else:
                res.error = f"judge session ended in error: subtype={result_ev.get('subtype')!r} {text[:200]!r}"
            res.error_class = ERR_INFRA
            return

        candidate = None
        so = result_ev.get("structured_output")
        if isinstance(so, dict):
            candidate = so
        elif isinstance(so_input, dict):
            candidate = so_input
        else:
            candidate = extract_last_json_object(text, _first_required_key(schema))
        if candidate is None:
            res.error, res.error_class = f"no parseable JSON judgment in output: {text[-300:]!r}", ERR_PARSE
            return
        problems = validate_against_schema(candidate, schema)
        if problems:
            res.error, res.error_class = "judgment does not match schema: " + "; ".join(problems[:5]), ERR_PARSE
            return
        res.parsed = candidate


def _builtin_plugin(p: Any) -> bool:
    """Claude Code 2.1.287 always lists its own ``@builtin`` plugins, even under --restricted."""
    if isinstance(p, dict):
        return p.get("path") == "builtin" or str(p.get("source", "")).endswith("@builtin")
    return str(p).endswith("@builtin")


def _kill_group(proc: subprocess.Popen, grace_s: float = 5.0) -> None:
    """SIGTERM then SIGKILL the whole process group (the CLI spawns children)."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    for sig, wait in ((signal.SIGTERM, grace_s), (signal.SIGKILL, grace_s)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


# ---------------------------------------------------------------------------
# anthropic_api
# ---------------------------------------------------------------------------

API_VERSION = "2023-06-01"
JUDGMENT_TOOL = "record_judgment"
_RETRY_STATUS = (408, 429, 500, 502, 503, 504, 529)


def _post_json(url: str, body: dict, headers: Mapping[str, str], *, timeout_s: float, retries: int,
               backoff_s: float, sleep: Callable[[float], None]) -> tuple[int, dict | None, str]:
    """POST JSON with retries on transient statuses and network errors: (status, parsed JSON or None, raw).

    Status 0 = no HTTP response (network error; the raw text says which).
    ``retry-after`` is honoured up to 60 s. Credentials travel only in
    ``headers``, which are never returned or logged.
    """
    data = json.dumps(body).encode("utf-8")
    last: tuple[int, dict | None, str] = (0, None, "")
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={**headers, "content-type": "application/json",
                                              "user-agent": USER_AGENT})
        retry_after = None
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                raw = r.read().decode("utf-8", errors="replace")
                try:
                    return r.status, json.loads(raw), raw
                except ValueError:
                    return r.status, None, raw
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
            last = (e.code, parsed, raw)
            if e.code not in _RETRY_STATUS:
                return last
            ra = e.headers.get("retry-after") if e.headers else None
            try:
                retry_after = min(float(ra), 60.0) if ra else None
            except ValueError:
                retry_after = None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = (0, None, f"{type(e).__name__}: {e}")
        if attempt < retries:
            sleep(retry_after if retry_after is not None else backoff_s * (2 ** attempt))
    return last


class AnthropicApiJudge:
    """Messages API judge over stdlib urllib. The key is read from the harness env, never logged."""

    name = "anthropic_api"

    def __init__(
        self,
        out_dir: str | os.PathLike,
        *,
        api_key_env: str = "ANTHROPIC_API_KEY",
        base_url: str = "https://api.anthropic.com",
        max_tokens: int = 4096,
        timeout_s: float = 300,
        retries: int = 3,
        backoff_s: float = 2.0,
        structured: str = "auto",
        pricing: Mapping[str, tuple[float, float]] | None = None,
        sleep: Callable[[float], None] = time.sleep):
        if structured not in ("auto", "tool", "output_config", "none"):
            raise ValueError(f"structured must be auto|tool|output_config|none, got {structured!r}")
        self.out_dir = Path(out_dir)
        self.api_key_env = api_key_env
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.retries = retries
        self.backoff_s = backoff_s
        self.structured = structured
        self.pricing = dict(pricing or {})  # model prefix -> (USD per 1M input, USD per 1M output)
        self.sleep = sleep

    def _body(self, system: str, user: str, schema: Mapping[str, Any] | None, model: str, mode: str) -> dict:
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": user}],
        }
        if system:
            body["system"] = system
        if schema is not None and mode == "tool":
            body["tools"] = [{"name": JUDGMENT_TOOL, "description": "Record the judgment.",
                              "input_schema": dict(schema)}]
            body["tool_choice"] = {"type": "tool", "name": JUDGMENT_TOOL}
        elif schema is not None and mode == "output_config":
            body["output_config"] = {"format": {"type": "json_schema", "schema": dict(schema)}}
        return body

    def _post(self, body: dict, key: str) -> tuple[int, dict | None, str]:
        """(status, parsed JSON or None, raw text). Retries transient failures."""
        return _post_json(self.base_url + "/v1/messages", body,
                          {"x-api-key": key, "anthropic-version": API_VERSION},
                          timeout_s=self.timeout_s, retries=self.retries, backoff_s=self.backoff_s, sleep=self.sleep)

    def _cost(self, model: str, usage: Mapping[str, Any]) -> float | None:
        for prefix, (pin, pout) in sorted(self.pricing.items(), key=lambda kv: -len(kv[0])):
            if model.startswith(prefix):
                return round((usage.get("input_tokens", 0) * pin
                              + usage.get("cache_read_input_tokens", 0) * pin * 0.1
                              + usage.get("cache_creation_input_tokens", 0) * pin * 1.25
                              + usage.get("output_tokens", 0) * pout) / 1e6, 6)
        return None

    def judge(self, system: str, user: str, schema: Mapping[str, Any] | None, model: str,
              *, label: str | None = None) -> JudgeResult:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        stem = _stem(self.out_dir, label, model, system, user, schema)
        raw_path = stem.with_name(stem.name + ".response.json")
        res = JudgeResult(parsed=None, raw_path=raw_path, tool_use_count=0, cost_usd=None, turns=None,
                          adapter=self.name)
        t0 = time.monotonic()

        def finish(error: str | None, cls: str | None) -> JudgeResult:
            res.error, res.error_class = error, (cls if error else None)
            if error:
                res.parsed = None
            res.duration_s = round(time.monotonic() - t0, 3)
            _write_json(stem.with_name(stem.name + ".result.json"), res.to_json())
            return res

        key = os.environ.get(self.api_key_env)
        if not key:
            raw_path.write_text(json.dumps({"error": f"{self.api_key_env} not set"}) + "\n", encoding="utf-8")
            return finish(f"infra_auth: {self.api_key_env} is not set", ERR_INFRA)
        if not model:
            raise ValueError("a pinned judge model is required")

        mode = "none" if schema is None or self.structured == "none" else (
            "tool" if self.structured in ("auto", "tool") else "output_config")
        exchanges = []
        status, resp, raw = 0, None, ""
        for _ in range(2):
            body = self._body(system, user, schema, model, mode)
            status, resp, raw = self._post(body, key)
            exchanges.append({"mode": mode, "request": body, "status": status,
                              "response": resp if resp is not None else raw[:4000]})
            err_text = json.dumps(resp) if resp is not None else raw
            if status == 400 and mode == "tool" and self.structured == "auto" and "tool_choice" in err_text:
                mode = "output_config"  # model rejects forced tool use; use JSON-schema output instead
                continue
            break
        _write_json(raw_path, {"exchanges": exchanges})

        if status != 200 or not isinstance(resp, dict):
            low = (raw or "").lower()
            kind = "infra_auth" if status in (401, 403) else ("infra_cap" if status == 429 else "infra_api")
            return finish(f"{kind}: HTTP {status} {low[:200]!r}", ERR_INFRA)

        res.turns = 1
        if resp.get("model"):
            res.model_served = [str(resp["model"])]
        usage = resp.get("usage") or {}
        res.cost_usd = self._cost(model, usage)
        if resp.get("stop_reason") == "refusal":
            return finish("model refused (stop_reason=refusal)", ERR_REFUSAL)

        candidate = None
        texts = []
        for block in resp.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == JUDGMENT_TOOL:
                res.structured_output_calls += 1
                if isinstance(block.get("input"), dict):
                    candidate = block["input"]
            elif block.get("type") in TOOL_USE_BLOCK_TYPES:
                res.tool_use_count += 1
            elif block.get("type") == "text":
                texts.append(block.get("text") or "")
        if res.tool_use_count:
            return finish(f"judge used tools ({res.tool_use_count} tool_use)", ERR_ISOLATION)
        if candidate is None:
            text = "\n".join(texts)
            try:
                parsed_text = json.loads(text)
                candidate = parsed_text if isinstance(parsed_text, dict) else None
            except ValueError:
                candidate = extract_last_json_object(text, _first_required_key(schema))
        if candidate is None:
            return finish(f"no parseable JSON judgment (stop_reason={resp.get('stop_reason')!r})", ERR_PARSE)
        problems = validate_against_schema(candidate, schema)
        if problems:
            return finish("judgment does not match schema: " + "; ".join(problems[:5]), ERR_PARSE)
        res.parsed = candidate
        return finish(None, None)


# ---------------------------------------------------------------------------
# openai_compat
# ---------------------------------------------------------------------------

class OpenAICompatJudge:
    """OpenAI-compatible chat-completions judge over stdlib urllib (the eval-6 GLM 5.1 D2 judge).

    Generalizes the paper's non-Claude judge arm (``bwm_latent/d2_judge.py``
    ``run_judge_glm`` over ``bwm_coupled/fireworks_backend.py``): one
    ``POST <base_url>/chat/completions`` with ``{"model", "max_tokens",
    "messages": [system?, user], "tools": []}``, the judgment parsed from the
    reply text with :func:`extract_last_json_object` and validated against the
    schema. Differences from the paper call, on purpose: an honest User-Agent
    (``diff-bwm-1/<version>``, not a browser string), the full exchange saved
    beside the result, and a reply carrying ``tool_calls`` is a judge_error
    (isolation) rather than ignored.

    ``base_url`` (e.g. ``https://api.fireworks.ai/inference/v1``) falls back to
    ``$DBWM_JUDGE_BASE_URL``; the key is read from ``$<api_key_env>`` at call
    time, sent only as the bearer header, never written to disk.
    ``empty_tools=True`` sends ``"tools": []`` like the paper (some providers
    reject an empty list: set False to omit the key). ``pricing`` is
    ``{"input", "cached_input", "output"}`` in USD per 1M tokens (cached input
    from ``usage.prompt_tokens_details.cached_tokens``, the paper's formula).
    ``extra_body`` is merged into the request (e.g. ``{"temperature": 0}``) and
    may not set model, messages or tools.
    """

    name = "openai_compat"

    def __init__(
        self,
        out_dir: str | os.PathLike,
        *,
        base_url: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        max_tokens: int | None = 4000,
        timeout_s: float = 300,
        retries: int = 3,
        backoff_s: float = 2.0,
        empty_tools: bool = True,
        pricing: Mapping[str, float] | None = None,
        extra_body: Mapping[str, Any] | None = None,
        sleep: Callable[[float], None] = time.sleep):
        url = base_url or os.environ.get("DBWM_JUDGE_BASE_URL") or ""
        if not url.startswith(("http://", "https://")):
            raise ValueError("openai_compat judge needs base_url (http/https), e.g. "
                             "https://api.fireworks.ai/inference/v1, or $DBWM_JUDGE_BASE_URL")
        clash = sorted(set(extra_body or {}) & {"model", "messages", "tools", "tool_choice"})
        if clash:
            raise ValueError(f"extra_body may not set {clash} (the judge owns them)")
        self.out_dir = Path(out_dir)
        self.base_url = url.rstrip("/")
        self.api_key_env = api_key_env
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.retries = retries
        self.backoff_s = backoff_s
        self.empty_tools = empty_tools
        self.pricing = dict(pricing or {})
        self.extra_body = dict(extra_body or {})
        self.sleep = sleep

    def _body(self, system: str, user: str, model: str) -> dict[str, Any]:
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
        body: dict[str, Any] = {"model": model, "messages": messages}
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        if self.empty_tools:
            body["tools"] = []
        body.update(self.extra_body)
        return body

    def _cost(self, usage: Mapping[str, Any]) -> float | None:
        if not self.pricing:
            return None
        pt = int(usage.get("prompt_tokens") or 0)
        ct = int(usage.get("completion_tokens") or 0)
        cached = min(int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0), pt)
        p = self.pricing
        return round(((pt - cached) * p.get("input", 0.0) + cached * p.get("cached_input", p.get("input", 0.0))
                      + ct * p.get("output", 0.0)) / 1e6, 6)

    def judge(self, system: str, user: str, schema: Mapping[str, Any] | None, model: str,
              *, label: str | None = None) -> JudgeResult:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        stem = _stem(self.out_dir, label, model, system, user, schema)
        raw_path = stem.with_name(stem.name + ".response.json")
        res = JudgeResult(parsed=None, raw_path=raw_path, tool_use_count=0, cost_usd=None, turns=None,
                          adapter=self.name, init_tools=[] if self.empty_tools else None)
        t0 = time.monotonic()

        def finish(error: str | None, cls: str | None) -> JudgeResult:
            res.error, res.error_class = error, (cls if error else None)
            if error:
                res.parsed = None
            res.duration_s = round(time.monotonic() - t0, 3)
            _write_json(stem.with_name(stem.name + ".result.json"), res.to_json())
            return res

        if not model:
            raise ValueError("a pinned judge model is required")
        key = os.environ.get(self.api_key_env)
        if not key:
            raw_path.write_text(json.dumps({"error": f"{self.api_key_env} not set"}) + "\n", encoding="utf-8")
            return finish(f"infra_auth: {self.api_key_env} is not set", ERR_INFRA)

        body = self._body(system, user, model)
        status, resp, raw = _post_json(self.base_url + "/chat/completions", body,
                                       {"authorization": f"Bearer {key}"}, timeout_s=self.timeout_s,
                                       retries=self.retries, backoff_s=self.backoff_s, sleep=self.sleep)
        _write_json(raw_path, {"exchanges": [{"url_path": "/chat/completions", "request": body, "status": status,
                                              "response": resp if resp is not None else raw[:4000]}]})
        if status != 200 or not isinstance(resp, dict):
            low = (raw or "").lower()
            kind = "infra_auth" if status in (401, 403) else ("infra_cap" if status == 429 else "infra_api")
            return finish(f"{kind}: HTTP {status} {low[:200]!r}", ERR_INFRA)

        res.turns = 1
        if resp.get("model"):
            res.model_served = [str(resp["model"])]
        res.cost_usd = self._cost(resp.get("usage") or {})
        choices = resp.get("choices") or []
        choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        msg = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        finish_reason = choice.get("finish_reason")
        calls = msg.get("tool_calls") or []
        res.tool_use_count = len(calls) if isinstance(calls, list) else 1
        if res.tool_use_count:
            return finish(f"judge used tools ({res.tool_use_count} tool_calls)", ERR_ISOLATION)
        if msg.get("refusal") or finish_reason == "content_filter":
            return finish(f"model refused (finish_reason={finish_reason!r})", ERR_REFUSAL)
        content = msg.get("content")
        if isinstance(content, list):  # content parts
            content = "".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
        text = content if isinstance(content, str) else ""
        candidate = None
        try:
            whole = json.loads(text)
            candidate = whole if isinstance(whole, dict) else None
        except ValueError:
            candidate = extract_last_json_object(text, _first_required_key(schema))
        if candidate is None:
            return finish(f"no parseable JSON judgment (finish_reason={finish_reason!r})", ERR_PARSE)
        problems = validate_against_schema(candidate, schema)
        if problems:
            return finish("judgment does not match schema: " + "; ".join(problems[:5]), ERR_PARSE)
        res.parsed = candidate
        return finish(None, None)


# ---------------------------------------------------------------------------
# mock
# ---------------------------------------------------------------------------

class MockJudge:
    """Deterministic offline judge for pipeline validation. Never report its output as results.

    ``responses`` is one of:
      * a callable ``(system, user, schema, model) -> dict | JudgeResult | None``;
      * a mapping keyed by the user prompt text or its sha256 hex digest;
      * a sequence of dicts consumed in order.
    A ``None`` / missing fixture yields a judge_error (class infra).
    """

    name = "mock"

    def __init__(self, responses: Any, out_dir: str | os.PathLike | None = None):
        self.responses = responses
        self.out_dir = Path(out_dir) if out_dir is not None else None
        self._i = 0

    def _lookup(self, system: str, user: str, schema: Mapping[str, Any] | None, model: str) -> Any:
        r = self.responses
        if callable(r):
            return r(system, user, schema, model)
        if isinstance(r, Mapping):
            if user in r:
                return r[user]
            return r.get(hashlib.sha256(user.encode("utf-8")).hexdigest())
        if isinstance(r, Sequence) and not isinstance(r, (str, bytes)):
            if self._i >= len(r):
                return None
            v = r[self._i]
            self._i += 1
            return v
        raise TypeError("MockJudge responses must be a callable, mapping or sequence")

    def judge(self, system: str, user: str, schema: Mapping[str, Any] | None, model: str,
              *, label: str | None = None) -> JudgeResult:
        got = self._lookup(system, user, schema, model)
        stem = None
        raw_path = None
        if self.out_dir is not None:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            stem = _stem(self.out_dir, label, model, system, user, schema)
            raw_path = stem.with_name(stem.name + ".mock.json")
            _write_json(raw_path, {"fixture": got if not isinstance(got, JudgeResult) else got.to_json()})
        res = self._result(got, schema, model, raw_path)
        if stem is not None:
            _write_json(stem.with_name(stem.name + ".result.json"), res.to_json())
        return res

    def _result(self, got: Any, schema: Mapping[str, Any] | None, model: str, raw_path: Path | None) -> JudgeResult:
        if isinstance(got, JudgeResult):
            got.adapter = got.adapter or self.name
            return got
        res = JudgeResult(parsed=None, raw_path=raw_path, tool_use_count=0, cost_usd=0.0, turns=1,
                          adapter=self.name, model_served=[f"mock:{model}"])
        if got is None:
            res.error, res.error_class = "mock: no fixture for this prompt", ERR_INFRA
            return res
        if not isinstance(got, dict):
            res.error, res.error_class = f"mock fixture is not an object: {type(got).__name__}", ERR_PARSE
            return res
        problems = validate_against_schema(got, schema)
        if problems:
            res.error, res.error_class = "judgment does not match schema: " + "; ".join(problems[:5]), ERR_PARSE
            return res
        res.parsed = dict(got)
        return res


JUDGE_ADAPTERS: dict[str, type] = {
    ClaudeCliJudge.name: ClaudeCliJudge,
    AnthropicApiJudge.name: AnthropicApiJudge,
    OpenAICompatJudge.name: OpenAICompatJudge,
    MockJudge.name: MockJudge,
}


def get_judge(name: str, *args: Any, **kwargs: Any) -> Any:
    """Instantiate a judge adapter by its registry name (regime ``judge_adapter``)."""
    try:
        cls = JUDGE_ADAPTERS[name]
    except KeyError:
        raise ValueError(f"unknown judge adapter {name!r}; choose one of {sorted(JUDGE_ADAPTERS)}") from None
    return cls(*args, **kwargs)
