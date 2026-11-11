"""Post-hoc integrity audit of one attempt (``dbwm audit --run``): the second layer behind isolation.

Isolation (fresh sandbox, ``--restricted``, containers, sealed oracle) is the
first layer. This module re-reads what an attempt left behind -- the
transcript, the deliverables, the oracle and egress logs -- and records every
sign that the agent reached information it should not have. Reports exclude
FLAGGED attempts and list them by reason; NOTES are recorded for review but do
not exclude.

What is checked:

(a) Paths in tool_use INPUTS only -- never in tool results or the init event,
    which echo what the harness or the CLI wrote, not what the agent asked
    for. A path outside the sandbox, or one with a segment or basename in the
    eval's ``SCORER_ONLY_NAMES``, is a finding. A bare listing (``ls``, Glob,
    ``find`` without -exec) that merely names such a file is NOT an access
    (an earlier harness counted listings as leaks; paper Appendix A entry 11). Files the eval itself staged into
    the sandbox are exempt by exact path. Inside shell commands, a name that
    is also an ordinary word ("private", "grader") counts only where it can
    name a file (path segment, whole quoted string, whole shell argument);
    as an identifier or in prose it is a note. A name also matches with an
    extension ("ANSWER_KEY.json", "grader.py"). A FILE TOOL on an unstaged
    path inside the sandbox touches the agent's own file (nothing
    scorer-only is ever staged): a note.
(b) CANARY_GUID anywhere in the transcript or deliverables; the eval's
    answer-key values in agent output, tool results and deliverables. A value
    that first appeared in an ORACLE result (the harness computed it from the
    agent's own plan) is explained, not leaked, and is only noted. Evals must
    list only values an honest agent cannot produce from what it was given
    (hidden parameters, seeds, private identifiers) -- never an achievable
    target such as the optimum a correct agent would compute and write down.
(c) Duplicate-deliverable gate: sha256 of each canonicalized deliverable
    against a reference corpus (shipped submissions, the reference solution).
    A byte copy of an earlier output must not count as a success.
(d) The init event's tool list against the arm's expected tools, and the
    served model against the pin.
(e) Oracle override attempts (payload keys like ``mode``) from
    ``oracle_calls.jsonl``. The server ignored them, so they are notes.
(f) Write/Edit outside the sandbox (from the same tool inputs as (a)).
(g) Denied egress from ``egress.jsonl`` (blocked, so notes).
(j) ``zero_cost_completion`` (claude_code attempts): a non-error result event
    with ``total_cost_usd`` exactly 0, at most one turn and no tool call. No
    model call costs nothing, so this is a session that never ran (the authors'
    logged-out CLI shape, which its runner re-ran: ``run_confirmatory.py``
    ``_is_infra_failure``); the outcome classifier calls it COMPLETED because
    nothing in the result is an error. Flagged, so the cell is excluded from
    reports and listed by reason instead of counted as the agent's failure.

Severity policy. An access that the tool layer refused (listed in the result
event's permission_denials, or a file tool whose result is an error) is a
note: nothing was read. Under CONTAINER isolation the agent's filesystem holds
nothing but the sandbox, so outside-sandbox paths are notes there; under HOST
and HOST_UNSAFE they are flags. Scorer-only names, canary hits, unexplained
answer-key values, duplicates and regime mismatches are flags at every level.

Answer-key values are never written into audit.json: findings carry a short
sha256 fingerprint and the length instead, so audit files can be published.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from dbwm.core.types import CANARY_GUID, Cell, Isolation, ToolPolicy

AUDIT_SCHEMA = "dbwm.audit/1"
AUDIT_FILE = "audit.json"
RUN_SUMMARY_FILE = "audit_summary.json"
TRANSCRIPT = "transcript.jsonl"
ORACLE_CALLS = "oracle_calls.jsonl"
EGRESS = "egress.jsonl"
API_RELAY = "api_relay.jsonl"
CREDENTIAL_REDACTIONS = "credential_redactions.json"
DELIVERABLES = "deliverables"
AGENT_RESULT = "agent_result.json"

DEFAULT_SCORER_ONLY_NAMES = (
    "reference.json", "world.json", "ANSWER_KEY", "checks.jsonl", "answer_key", "grader", "private")

CLAUDE_TOOL_NAMES = {"read": "Read", "write": "Write", "edit": "Edit", "bash": "Bash", "glob": "Glob", "grep": "Grep"}
MCP_PREFIX = "mcp__dbwm__"

# Flag reasons (attempt excluded from reports).
F_CANARY = "canary"
F_ANSWER_KEY = "answer_key_value"
F_SCORER_ONLY = "scorer_only_access"
F_OUTSIDE = "outside_sandbox_access"
F_WRITE_OUTSIDE = "write_outside_sandbox"
F_DUPLICATE = "duplicate_deliverable"
F_TOOLS = "init_tools_mismatch"
F_MODEL = "model_mismatch"
F_CREDENTIAL = "credential_exposed"
F_ZERO_COST = "zero_cost_completion"
# Note reasons (recorded, not excluded).
N_LISTING = "listing_of_scorer_only_name"
N_SCORER_DENIED = "scorer_only_attempt_denied"
N_OUTSIDE_DENIED = "outside_sandbox_attempt_denied"
N_OUTSIDE_CONTAINER = "outside_sandbox_path_in_container"
N_OUTSIDE_LISTING = "outside_sandbox_listing"
N_UNCLASSIFIED = "path_unclassified"
N_ORACLE_VALUE = "answer_key_value_explained_by_oracle"
N_OVERRIDE = "oracle_override_attempt"
N_EGRESS = "egress_denied"
N_RELAY_DENIED = "api_relay_denied"
N_EXTRA_MODELS = "models_served_beyond_pin"
N_BAD_LINES = "unparseable_transcript_lines"
N_WORD_MENTION = "scorer_only_word_in_code_or_prose"
N_SCORER_INSIDE = "scorer_only_name_on_agent_file_in_sandbox"

BASH_TOOLS = {"Bash", "run_bash"}
LISTING_TOOLS = {"Glob", "LS", "list_dir"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "write_file", "edit_file"}
PATH_KEYS = ("file_path", "path", "notebook_path", "filename", "file", "directory", "dir")
LISTING_PROGS = {"ls", "tree", "stat", "du", "file", "pwd", "cd", "basename", "dirname", "realpath",
                 "readlink", "test", "[", "echo", "printf", "true", "false", "mkdir", "touch"}
_FIND_ACTIONS = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}
_CMD_PREFIXES = {"env", "sudo", "time", "nohup", "command", "exec", "nice", "builtin"}
SYSTEM_PREFIXES = ("/usr/", "/bin/", "/sbin/", "/lib/", "/lib64/", "/lib32/", "/opt/", "/etc/", "/dev/",
                   "/proc/", "/sys/", "/System/", "/Library/", "/private/etc/")

_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://\S+")
_PATHISH_RE = re.compile(r"[\w.~\-*]*/[\w.~\-/*]*")
_SEGMENT_SPLIT = re.compile(r"[/\\]+")
_VAR_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")


# ---------------------------------------------------------------------------
# Transcript model
# ---------------------------------------------------------------------------

@dataclass
class ToolUse:
    seq: int  # order of appearance in the transcript
    id: str | None
    name: str
    input: Any
    denied: bool = False
    result_text: str | None = None
    result_is_error: bool | None = None
    result_seq: int | None = None


@dataclass
class Transcript:
    init: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    tool_uses: list[ToolUse] = field(default_factory=list)
    agent_text: list[tuple[int, str]] = field(default_factory=list)  # (seq, assistant text)
    bad_lines: int = 0
    raw_text: str = ""


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(str(b.get("text") or ""))
                elif "content" in b:
                    parts.append(_content_text(b.get("content")))
                else:
                    parts.append(json.dumps(b, sort_keys=True, default=str))
            else:
                parts.append(str(b))
        return "\n".join(parts)
    if isinstance(content, dict):
        return json.dumps(content, sort_keys=True, default=str)
    return str(content)


def parse_transcript(path: str | os.PathLike) -> Transcript:
    """Read Claude stream-json (and the command adapter's bare ``{type: tool_use}`` lines)."""
    t = Transcript()
    p = Path(path)
    if not p.exists():
        return t
    t.raw_text = p.read_text(encoding="utf-8", errors="replace")
    by_id: dict[str, ToolUse] = {}
    seq = 0
    for line in t.raw_text.splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            t.bad_lines += 1
            continue
        if not isinstance(ev, dict):
            t.bad_lines += 1
            continue
        seq += 1
        typ = ev.get("type")
        if typ == "system" and ev.get("subtype") == "init" and t.init is None:
            t.init = ev
        elif typ == "result":
            t.result = ev
        elif typ == "tool_use":  # command adapter metrics line
            tu = ToolUse(seq=seq, id=ev.get("id"), name=str(ev.get("name") or ""), input=ev.get("input"))
            t.tool_uses.append(tu)
            if tu.id:
                by_id[str(tu.id)] = tu
        elif typ == "assistant":
            for b in (ev.get("message") or {}).get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") in ("tool_use", "server_tool_use", "mcp_tool_use"):
                    tu = ToolUse(seq=seq, id=b.get("id"), name=str(b.get("name") or ""), input=b.get("input"))
                    t.tool_uses.append(tu)
                    if tu.id:
                        by_id[str(tu.id)] = tu
                elif b.get("type") == "text":
                    t.agent_text.append((seq, str(b.get("text") or "")))
        elif typ == "user":
            extra = ev.get("tool_use_result")
            for b in (ev.get("message") or {}).get("content") or []:
                if not isinstance(b, dict) or b.get("type") != "tool_result":
                    continue
                tu = by_id.get(str(b.get("tool_use_id")))
                if tu is None:
                    continue
                text = _content_text(b.get("content"))
                if extra is not None:
                    text += "\n" + _content_text(extra)
                tu.result_text = text
                tu.result_is_error = bool(b.get("is_error"))
                tu.result_seq = seq
    denied_ids = set()
    for d in (t.result or {}).get("permission_denials") or []:
        if isinstance(d, dict) and d.get("tool_use_id"):
            denied_ids.add(str(d["tool_use_id"]))
    for tu in t.tool_uses:
        if tu.id and str(tu.id) in denied_ids:
            tu.denied = True
    return t


# ---------------------------------------------------------------------------
# Path classification
# ---------------------------------------------------------------------------

def _strip_private(abs_path: str) -> str:
    for pre in ("/private/var/", "/private/tmp/", "/private/etc/"):
        if abs_path.startswith(pre):
            return abs_path[len("/private"):]
    if abs_path in ("/private/var", "/private/tmp", "/private/etc"):
        return abs_path[len("/private"):]
    return abs_path


def _root_variants(roots: Iterable[str | os.PathLike | None]) -> list[str]:
    out: list[str] = []
    for r in roots:
        if not r:
            continue
        s = posixpath.normpath(str(r))
        for v in (s, _strip_private(s), os.path.realpath(s) if os.path.isabs(s) else s):
            v = posixpath.normpath(_strip_private(v))
            if v not in out:
                out.append(v)
    return out


@dataclass
class PathInfo:
    raw: str
    inside: bool | None  # None = cannot tell (absolute path, sandbox root unknown)
    rel: str | None  # sandbox-relative POSIX path when inside
    segments: list[str]  # segments to check against scorer-only names


def classify_path(raw: str, roots: Sequence[str]) -> PathInfo:
    """Classify a path as the agent wrote it, relative to the sandbox roots (its cwd)."""
    p = raw.strip().strip("'\"")
    if p.startswith("~"):
        segs = [s for s in _SEGMENT_SPLIT.split(p) if s and s != "~"]
        return PathInfo(raw, False, None, segs)
    if p.startswith("/"):
        norm = posixpath.normpath(_strip_private(p))
        for r in roots:
            if norm == r or norm.startswith(r.rstrip("/") + "/"):
                rel = posixpath.relpath(norm, r)
                rel = "" if rel == "." else rel
                return PathInfo(raw, True, rel, [s for s in rel.split("/") if s])
        segs = [s for s in norm.split("/") if s]
        return PathInfo(raw, False if roots else None, None, segs)
    norm = posixpath.normpath(p.replace("\\", "/"))
    if norm == ".." or norm.startswith("../"):
        return PathInfo(raw, False, None, [s for s in norm.split("/") if s and s != ".."])
    rel = "" if norm == "." else norm
    return PathInfo(raw, True, rel, [s for s in rel.split("/") if s])


def _scorer_hits(info: PathInfo, names_lower: set[str], exempt: set[str]) -> list[str]:
    if info.inside and info.rel is not None:
        if info.rel in exempt or any(e.startswith(info.rel + "/") for e in exempt if info.rel):
            return []
    hits = set()
    for seg in info.segments:
        low = seg.lower()
        if low in names_lower:
            hits.add(seg)
        elif "." in low and low.split(".", 1)[0] in names_lower:
            hits.add(seg)  # "ANSWER_KEY.json", "grader.py": the name with an extension
    return sorted(hits)


def _name_regex(names: Iterable[str]) -> re.Pattern[str] | None:
    names = sorted({n for n in names if n}, key=len, reverse=True)
    if not names:
        return None
    alt = "|".join(re.escape(n) for n in names)
    return re.compile(rf"(?<![\w.\-])(?:{alt})(?![\w\-])", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Bash command parsing
# ---------------------------------------------------------------------------

_SEG_SPLIT = re.compile(r"\|\||&&|[;\n]|(?<!\|)\|(?!\|)|(?<![>&<])&(?![>&])")


def _split_segments(command: str) -> list[str]:
    return [s for s in _SEG_SPLIT.split(command) if s and s.strip()]


def bash_programs_and_tokens(command: str) -> tuple[list[str], list[str]]:
    """(program names, all tokens) for a shell command; a best-effort, conservative parse."""
    progs: list[str] = []
    tokens: list[str] = []
    for seg in _split_segments(command):
        try:
            toks = shlex.split(seg, posix=True)
        except ValueError:
            toks = seg.split()
        expanded: list[str] = []
        for tok in toks:
            for part in re.split(r"[<>]+", tok):
                part = part.strip().lstrip("$(`").rstrip(")`")
                if part.startswith("-") and "=" in part:
                    part = part.split("=", 1)[1]
                if part:
                    expanded.append(part)
        i = 0
        while i < len(expanded) and (_VAR_ASSIGN.match(expanded[i]) or expanded[i] in _CMD_PREFIXES):
            i += 1
        if i < len(expanded):
            progs.append(posixpath.basename(expanded[i]))
            if progs[-1] == "find" and any(t in _FIND_ACTIONS for t in expanded[i:]):
                progs[-1] = "find-exec"
        tokens.extend(expanded)
    return progs, tokens


def _bash_is_listing(progs: list[str]) -> bool:
    return bool(progs) and all(p in LISTING_PROGS or p == "find" for p in progs)


_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_CONTENT_PROGS = {"cat", "tee"}
_PRINT_PROGS = {"echo", "printf"}
_REDIRECT_RE = re.compile(r">{1,2}\s*([^\s;|&]+)")


def sanitize_command_for_paths(command: str) -> str:
    """Drop text an agent WRITES (not reads) before looking for paths in a shell command.

    Heredoc bodies fed to ``cat``/``tee`` and the arguments of ``echo``/``printf``
    are file content (reports, notes); a word like "private" there is prose,
    not an access. Their redirect targets are kept. Heredocs fed to
    interpreters (``python3 <<EOF``) are code and are kept whole.
    """
    lines = command.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = _HEREDOC_RE.search(line)
        if m:
            progs, _ = bash_programs_and_tokens(line[: m.start()])
            delim = m.group(2)
            j = i + 1
            body_end = None
            while j < len(lines):
                if lines[j].strip() == delim:
                    body_end = j
                    break
                j += 1
            if body_end is not None and progs and progs[-1] in _CONTENT_PROGS:
                out.append(delim)
                i = body_end + 1
                continue
        i += 1
    kept: list[str] = []
    for seg_line in "\n".join(out).split("\n"):
        parts = re.split(r"(\|\||&&|;|(?<!\|)\|(?!\|)|(?<![>&<])&(?![>&]))", seg_line)
        rebuilt = []
        for part in parts:
            progs, _ = bash_programs_and_tokens(part) if part.strip() and part not in ("||", "&&", ";", "|", "&") \
                else ([], [])
            if progs and progs[0] in _PRINT_PROGS:
                targets = _REDIRECT_RE.findall(part)
                rebuilt.append(" ".join(f"> {t}" for t in targets))
            else:
                rebuilt.append(part)
        kept.append("".join(rebuilt))
    return "\n".join(kept)


_PLAIN_WORD = re.compile(r"[a-z]+")


def _is_plain_word(name: str) -> bool:
    """A scorer-only name that is also an ordinary word or identifier ("private", "grader")."""
    return bool(_PLAIN_WORD.fullmatch(name.lower()))


def _heredoc_body_lines(command: str) -> set[int]:
    """Indexes of the lines of ``command`` that are heredoc bodies (not shell syntax)."""
    lines = command.split("\n")
    body: set[int] = set()
    i = 0
    while i < len(lines):
        m = _HEREDOC_RE.search(lines[i])
        if m:
            delim = m.group(2)
            j = i + 1
            while j < len(lines) and lines[j].strip() != delim:
                body.add(j)
                j += 1
            i = j + 1
            continue
        i += 1
    return body


def _path_candidates_from_text(text: str, name_re: re.Pattern[str] | None) -> tuple[list[str], list[str]]:
    """(path candidates, plain-word mentions) in a shell command.

    Candidates: every slash-bearing token, and every scorer-only name that is
    distinctive ("reference.json", "ANSWER_KEY") wherever it appears --
    including inside interpreter code. A name that is also an ordinary word
    ("private", "grader") is a candidate only where it can name a file: as a
    path segment (caught by the slash rule), as a whole quoted string
    (``open("grader")``), or as a whole argument token on a shell line (``cat
    grader``). Elsewhere (``private = 3`` in a heredoc, a comment) it is
    returned as a mention, which the audit records as a note: flagging prose
    and identifiers as accesses is exactly the false-positive class an earlier
    harness suffered from (Appendix A entry 11).
    """
    text = _URL_RE.sub(" ", text)
    cands = [m.group(0) for m in _PATHISH_RE.finditer(text) if m.group(0).strip("/")
             or m.group(0) == "/"]
    if text.strip().startswith("~"):
        cands.append(text.strip().split()[0])
    mentions: list[str] = []
    if name_re is not None:
        lines = text.split("\n")
        body = _heredoc_body_lines(text)
        shell_args: set[str] = set()
        for i, line in enumerate(lines):
            if i in body:
                continue
            for seg in _split_segments(line):
                _progs, toks = bash_programs_and_tokens(seg)
                shell_args.update(toks[1:] if toks else [])
        for m in name_re.finditer(text):
            name = m.group(0)
            if any(name in c for c in cands):
                continue  # already inside a path candidate
            if not _is_plain_word(name):
                cands.append(name)
                continue
            before = text[m.start() - 1] if m.start() > 0 else ""
            after = text[m.end()] if m.end() < len(text) else ""
            if (before in "'\"" and before and after == before) or name in shell_args:
                cands.append(name)
            else:
                mentions.append(name)
    out: list[str] = []
    for c in cands:
        if c not in out:
            out.append(c)
    return out, sorted(set(mentions))


# ---------------------------------------------------------------------------
# Deliverable canonicalization (duplicate gate)
# ---------------------------------------------------------------------------

def canonical_sha256(path: str | os.PathLike) -> str:
    """sha256 of a deliverable after canonicalization.

    JSON files: parsed and re-serialized with sorted keys and fixed separators
    (whitespace and key order cannot hide a copy). Other text: line endings
    normalized, trailing whitespace stripped. Binary: raw bytes.
    """
    p = Path(path)
    data = p.read_bytes()
    if p.suffix.lower() in (".json", ".jsonl"):
        try:
            if p.suffix.lower() == ".json":
                obj = json.loads(data.decode("utf-8"))
                canon = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            else:
                lines = [json.dumps(json.loads(ln), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                         for ln in data.decode("utf-8").splitlines() if ln.strip()]
                canon = "\n".join(lines)
            return hashlib.sha256(canon.encode("utf-8")).hexdigest()
        except (ValueError, UnicodeDecodeError):
            pass
    if b"\0" not in data[:8192]:
        try:
            text = data.decode("utf-8")
            lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
            while lines and not lines[-1]:
                lines.pop()
            return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
        except UnicodeDecodeError:
            pass
    return hashlib.sha256(data).hexdigest()


def eval_canonical_sha256(path: str | os.PathLike, canonicalize: Callable[[Path], Any] | None) -> str | None:
    """Hash of an eval-specific canonical form, or None.

    An eval may define ``canonicalize_deliverable(path) -> str | None`` to
    match deliverables by meaning rather than bytes (eval 2: allocations
    compared by value, so re-typing numbers as strings cannot hide a copy of
    a reference plan). None (or a hook error) means "no eval canonical form".
    The hash lives in its own namespace, so it never collides with
    :func:`canonical_sha256`.
    """
    if canonicalize is None:
        return None
    try:
        form = canonicalize(Path(path))
    except Exception:  # noqa: BLE001 - an unparseable deliverable simply has no eval form
        return None
    if not isinstance(form, str):
        return None
    return eval_canonical_digest(form)


def eval_canonical_digest(form: str) -> str:
    """Corpus entry for an eval canonical form (what :func:`eval_canonical_sha256` yields for a
    file whose ``canonicalize_deliverable`` returns ``form``). Lets an eval list a reference plan
    that is not a file (e.g. eval 2's careful optimum) in its duplicate corpus by value."""
    return hashlib.sha256(b"dbwm-eval-canonical\0" + form.encode("utf-8")).hexdigest()


def _deliverable_hashes(p: Path, canonicalize: Callable[[Path], Any] | None) -> set[str]:
    out = {canonical_sha256(p)}
    h = eval_canonical_sha256(p, canonicalize)
    if h:
        out.add(h)
    return out


def corpus_hashes(items: Iterable[str | os.PathLike],
                  canonicalize: Callable[[Path], Any] | None = None) -> set[str]:
    """Canonical hashes from hex digests, files, or directories (recursively).

    With ``canonicalize`` (an eval's ``canonicalize_deliverable``), each file
    also contributes its eval-canonical hash.
    """
    out: set[str] = set()
    for it in items:
        s = str(it)
        if re.fullmatch(r"[0-9a-f]{64}", s):
            out.add(s)
            continue
        p = Path(s)
        if p.is_file():
            if p.stat().st_size:
                out |= _deliverable_hashes(p, canonicalize)
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.stat().st_size:
                    out |= _deliverable_hashes(f, canonicalize)
    return out


# ---------------------------------------------------------------------------
# Expected tools / model
# ---------------------------------------------------------------------------

def expected_tool_names(policy: ToolPolicy, adapter: str = "claude_code",
                        isolation: Isolation | str | None = None) -> set[str]:
    """Adapter-native tool names an arm's init event must list exactly."""
    if adapter == "openai_compat":
        from dbwm.core.adapters.openai_compat import expected_tool_names as oc_names

        return set(oc_names(policy, isolation))
    names = {CLAUDE_TOOL_NAMES[b] for b in policy.builtin}
    if policy.transport == "mcp":
        names |= {MCP_PREFIX + s.name for s in policy.oracle_tools}
    return names


def model_matches_pin(served: str, pin: str) -> bool:
    """The runtime assertion's rule (dbwm.core.stream_json.model_matches), so audit and runner agree.

    Exact id, the pin plus a date suffix, a family alias (``haiku``) contained
    in the full id; ``[1m]``-style suffixes and provider decorations ignored.
    """
    from dbwm.core.stream_json import model_matches

    return model_matches(pin, served)


# ---------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------

def _fingerprint(value: str) -> dict[str, Any]:
    return {"sha256_12": hashlib.sha256(value.encode("utf-8")).hexdigest()[:12], "len": len(value)}


def _find_manifest(attempt_dir: Path) -> dict[str, Any] | None:
    for d in list(attempt_dir.parents)[:4]:
        m = d / "manifest.json"
        if m.is_file():
            try:
                obj = json.loads(m.read_text(encoding="utf-8"))
            except ValueError:
                return None
            return obj if isinstance(obj, dict) else None
    return None


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


class _Findings:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, severity: str, reason: str, **detail: Any) -> None:
        self.items.append({"severity": severity, "reason": reason, "detail": detail})

    def flag(self, reason: str, **detail: Any) -> None:
        self.add("flag", reason, **detail)

    def note(self, reason: str, **detail: Any) -> None:
        self.add("note", reason, **detail)


def audit_attempt(
    attempt_dir: str | os.PathLike,
    eval_def: Any,
    cell: Cell,
    *,
    sandbox: str | os.PathLike | None = None,
    isolation: Isolation | str | None = None,
    expected_tools: Iterable[str] | None = None,
    model_pin: str | None = None,
    answer_key_values: Sequence[Any] | None = None,
    disclosed_values: Sequence[Any] | None = None,
    reference_corpus: Iterable[str | os.PathLike] | None = None,
    scorer_only_names: Iterable[str] | None = None,
    write: bool = True) -> dict[str, Any]:
    """Audit one attempt directory and (by default) write ``audit.json`` into it.

    Anything not passed explicitly is looked up: the sandbox from the init
    event's cwd, isolation and model pin from the nearest ``manifest.json``
    (``regime``), answer-key values from ``eval_def.answer_key_values(cell)``,
    disclosed values from ``dbwm.core.types.disclosed_values_for`` (an
    answer-key value seen only inside one of them is noted, not flagged),
    scorer-only names from the eval package's ``SCORER_ONLY_NAMES``, the
    duplicate corpus from ``eval_def.duplicate_corpus(cell)`` if defined.
    A check that cannot run says so in ``checks`` (never a silent pass).
    """
    adir = Path(attempt_dir)
    f = _Findings()
    checks: dict[str, str] = {}
    manifest = _find_manifest(adir)
    regime = (manifest or {}).get("regime") or {}

    iso_raw = isolation if isolation is not None else ((manifest or {}).get("isolation") or regime.get("isolation"))
    try:
        iso = Isolation(iso_raw) if iso_raw is not None else None
    except ValueError:
        iso = None
    if iso is None:
        checks["isolation"] = "unknown; treated as HOST (outside-sandbox paths flag)"
        iso = Isolation.HOST
    else:
        checks["isolation"] = iso.value
    pin = model_pin or regime.get("model")

    arm = None
    try:
        arm = eval_def.arms[cell.arm]
    except Exception:  # noqa: BLE001
        checks["arm"] = f"arm {cell.arm!r} not found on eval definition"

    tr = parse_transcript(adir / TRANSCRIPT)
    if not (adir / TRANSCRIPT).exists():
        checks["transcript"] = "missing"
    if tr.bad_lines:
        f.note(N_BAD_LINES, count=tr.bad_lines)

    # Names, exemptions, oracle tool names.
    if scorer_only_names is None:
        scorer_only_names = _eval_attr(eval_def, "SCORER_ONLY_NAMES") or DEFAULT_SCORER_ONLY_NAMES
    names = [str(n) for n in scorer_only_names]
    names_lower = {n.lower() for n in names}
    name_re = _name_regex(names)
    exempt: set[str] = set()
    try:
        manifest_files = eval_def.stage(cell).files
        exempt = {posixpath.normpath(sf.dst) for sf in manifest_files}
    except Exception as e:  # noqa: BLE001
        checks["staged_exemptions"] = f"unavailable ({type(e).__name__}); no staged-file exemptions"
    oracle_tool_names: set[str] = set()
    shim_names: set[str] = set()
    if arm is not None:
        for spec in arm.tools.oracle_tools:
            oracle_tool_names |= {MCP_PREFIX + spec.name, spec.name}
            shim_names.add(spec.cli_name)

    roots = _root_variants([(tr.init or {}).get("cwd"), sandbox])
    if not roots:
        checks["paths"] = "sandbox root unknown (no init cwd, none given): absolute paths unclassified"

    # (a) + (f) paths in tool_use inputs.
    for tu in tr.tool_uses:
        if tu.name in oracle_tool_names or tu.name.startswith("mcp__"):
            # Oracle tool inputs (red-team RT-A1): the handlers confine every file
            # argument to the sandbox and refuse the rest, so a scorer-only or
            # outside path here is an attempt that was denied: a note, never a flag.
            oinp = tu.input if isinstance(tu.input, dict) else {}
            for k in PATH_KEYS:
                v = oinp.get(k)
                if not isinstance(v, str) or not v:
                    continue
                where = {"tool": tu.name, "via": "oracle", "input": {k: v[:500]}}
                info = classify_path(v, roots)
                hits = _scorer_hits(info, names_lower, exempt)
                if hits:
                    f.note(N_SCORER_DENIED, path=v[:500], names=hits, **where)
                elif info.inside is False:
                    f.note(N_OUTSIDE_DENIED, path=v[:500], write=False, **where)
            continue
        inp = tu.input if isinstance(tu.input, dict) else {}
        is_bash = tu.name in BASH_TOOLS
        if is_bash:
            command = str(inp.get("command") or "")
            clean = sanitize_command_for_paths(command)
            progs, _tokens = bash_programs_and_tokens(clean)
            listing = _bash_is_listing(progs)
            cands, mentions = _path_candidates_from_text(clean, name_re)
            where = {"tool": tu.name, "command": command[:500]}
            if mentions:
                f.note(N_WORD_MENTION, names=mentions, **where)
        else:
            listing = tu.name in LISTING_TOOLS
            cands = []
            for k in PATH_KEYS:
                v = inp.get(k)
                if isinstance(v, str) and v:
                    cands.append(v)
            if tu.name == "Glob" and isinstance(inp.get("pattern"), str):
                cands.append(inp["pattern"])
            if tu.name == "Grep" and isinstance(inp.get("glob"), str):
                cands.append(inp["glob"])
            where = {"tool": tu.name, "input": {k: inp[k] for k in PATH_KEYS if k in inp}}
        refused = tu.denied or (not is_bash and bool(tu.result_is_error))
        writes = tu.name in WRITE_TOOLS
        for c in cands:
            info = classify_path(c, roots)
            hits = _scorer_hits(info, names_lower, exempt)
            if hits:
                if listing:
                    f.note(N_LISTING, path=c, names=hits, **where)
                elif refused:
                    f.note(N_SCORER_DENIED, path=c, names=hits, **where)
                elif not is_bash and info.inside:
                    # A file tool on a sandbox path that was not staged: the agent's
                    # own file (the pre-check guarantees nothing scorer-only is
                    # staged). In a shell command the location is not knowable
                    # (cd, find -name), so those still flag.
                    f.note(N_SCORER_INSIDE, path=c, names=hits, **where)
                else:
                    f.flag(F_SCORER_ONLY, path=c, names=hits, **where)
                continue
            if info.inside is None:
                f.note(N_UNCLASSIFIED, path=c, **where)
                continue
            if info.inside:
                continue
            norm = posixpath.normpath(c) if c.startswith("/") else c
            if is_bash and (norm.startswith(SYSTEM_PREFIXES) or norm in ("/dev/null", "/")):
                continue
            if refused:
                f.note(N_OUTSIDE_DENIED, path=c, write=writes, **where)
            elif iso == Isolation.CONTAINER:
                f.note(N_OUTSIDE_CONTAINER, path=c, write=writes, **where)
            elif listing and not writes:
                f.note(N_OUTSIDE_LISTING, path=c, **where)
            else:
                f.flag(F_WRITE_OUTSIDE if writes else F_OUTSIDE, path=c, **where)
    checks.setdefault("paths", "ok")

    # (b) canary and answer-key values.
    if CANARY_GUID in tr.raw_text:
        f.flag(F_CANARY, where="transcript")
    deliv = adir / DELIVERABLES
    if deliv.is_dir():
        from dbwm.core import canary as core_canary

        for hit in core_canary.scan_for_guid(deliv):
            f.flag(F_CANARY, where="deliverable", file=_rel(hit, deliv))
    checks["canary"] = "ok"

    if answer_key_values is None:
        try:
            answer_key_values = list(eval_def.answer_key_values(cell))
        except Exception as e:  # noqa: BLE001
            answer_key_values = None
            checks["answer_key_values"] = f"unavailable ({type(e).__name__}: {e})"
    if answer_key_values is not None:
        if disclosed_values is None:
            from dbwm.core.types import disclosed_values_for

            try:
                disclosed_values = disclosed_values_for(eval_def, cell)
            except Exception as e:  # noqa: BLE001 - scan without exemptions, and say so
                disclosed_values = []
                checks["disclosed_values"] = f"unavailable ({type(e).__name__}: {e}); none exempted"
        _scan_values(f, tr, deliv, answer_key_values, oracle_tool_names, shim_names, disclosed_values)
        checks.setdefault("answer_key_values", "ok")
        if disclosed_values:
            checks.setdefault("disclosed_values", f"{len(disclosed_values)} exempted where wholly inside them")

    # (c) duplicate gate.
    corpus_items = reference_corpus
    if corpus_items is None:
        dc = getattr(eval_def, "duplicate_corpus", None)
        if callable(dc):
            try:
                corpus_items = list(dc(cell))
            except Exception as e:  # noqa: BLE001
                checks["duplicates"] = f"corpus unavailable ({type(e).__name__}: {e})"
    if corpus_items is not None:
        canon_hook = getattr(eval_def, "canonicalize_deliverable", None)
        canon_hook = canon_hook if callable(canon_hook) else None
        corpus = corpus_hashes(corpus_items, canon_hook)
        if deliv.is_dir():
            for fp in sorted(deliv.rglob("*")):
                if fp.is_file() and fp.stat().st_size and _deliverable_hashes(fp, canon_hook) & corpus:
                    f.flag(F_DUPLICATE, file=_rel(str(fp), deliv))
        checks["duplicates"] = (f"ok ({len(corpus)} corpus hashes"
                                + (", eval canonical form" if canon_hook else "") + ")")
    else:
        checks.setdefault("duplicates", "skipped: no reference corpus provided")

    # (d) regime: tools and model.
    init = tr.init
    if init is None:
        checks["regime"] = "skipped: no init event in transcript"
    else:
        want = None
        if expected_tools is not None:
            want = set(expected_tools)
        elif arm is not None:
            adapter = "openai_compat" if init.get("adapter") == "openai_compat" else "claude_code"
            try:
                want = expected_tool_names(arm.tools, adapter, iso)
            except Exception as e:  # noqa: BLE001
                checks["regime"] = f"expected tools unavailable ({type(e).__name__})"
        got = set(init.get("tools") or [])
        if want is not None and got != want:
            f.flag(F_TOOLS, expected=sorted(want), got=sorted(got))
        served = init.get("model")
        if pin and served and not model_matches_pin(str(served), str(pin)):
            f.flag(F_MODEL, pin=pin, served=served)
        mu = (tr.result or {}).get("modelUsage")
        if pin and isinstance(mu, dict):
            extra = sorted(m for m in mu if not model_matches_pin(str(m), str(pin)))
            if extra:
                f.note(N_EXTRA_MODELS, pin=pin, models=extra)
        if not pin:
            checks["model"] = "skipped: no model pin known"
        checks.setdefault("regime", "ok")

    # (j) a "completed" claude_code session that cost exactly nothing never ran.
    adapter = (manifest or {}).get("adapter") or (regime.get("adapter") if isinstance(regime, dict) else None)
    if adapter is None and isinstance(tr.init, dict) and tr.init.get("claude_code_version"):
        adapter = "claude_code"
    zc = zero_cost_completion(tr) if adapter == "claude_code" else None
    if zc is not None:
        f.flag(F_ZERO_COST, **zc)
    checks["zero_cost_completion"] = ("flagged" if zc is not None else "ok") if adapter == "claude_code" \
        else f"skipped: adapter {adapter!r} (claude_code only)"

    # (e) oracle overrides, (g) egress.
    calls = _jsonl(adir / ORACLE_CALLS)
    overrides = [c for c in calls if c.get("override_attempt")]
    if overrides:
        keys: set[str] = set()
        for c in overrides:
            for src in (c.get("override_keys"), c.get("override_attempt")):
                if isinstance(src, list):
                    keys.update(str(k) for k in src)
        f.note(N_OVERRIDE, count=len(overrides), keys=sorted(keys))
    checks["oracle_overrides"] = "ok" if (adir / ORACLE_CALLS).exists() else "no oracle log"
    egress = _jsonl(adir / EGRESS)
    denied = [e for e in egress if _egress_denied(e)]
    if denied:
        f.note(N_EGRESS, count=len(denied), hosts=sorted({str(e.get("host")) for e in denied}))
    checks["egress"] = "ok" if (adir / EGRESS).exists() else "no egress log"
    # (h) the CONTAINER API relay (RT-E): denied requests (another model, another
    # endpoint, a wrong credential) are notes; usage is summarized for the record.
    from dbwm.core.api_relay import summarize as relay_summarize

    relay = relay_summarize(_jsonl(adir / API_RELAY)) if (adir / API_RELAY).exists() else None
    if relay is not None:
        checks["api_relay"] = "ok"
        if relay["denied"]:
            f.note(N_RELAY_DENIED, count=relay["denied"], reasons=relay["denied_reasons"])
    # (i) a real credential value was found (and redacted) in the attempt record.
    red = adir / CREDENTIAL_REDACTIONS
    if red.exists():
        try:
            files = json.loads(red.read_text(encoding="utf-8")).get("files") or []
        except (OSError, ValueError, AttributeError):
            files = ["?"]
        f.flag(F_CREDENTIAL, files=files)

    flags = sorted({it["reason"] for it in f.items if it["severity"] == "flag"})
    notes = sorted({it["reason"] for it in f.items if it["severity"] == "note"})
    out = {
        "schema": AUDIT_SCHEMA,
        "eval": cell.eval_id,
        "cell_id": cell.cell_id,
        "flagged": bool(flags),
        "reasons": flags,
        "notes": notes,
        "findings": f.items,
        "checks": checks,
        "counts": {
            "tool_uses": len(tr.tool_uses),
            # Authorised real-op calls (call_index set): the rule shared with
            # verdict.oracle_call_counts and oracle.server.summarize_calls.
            "oracle_calls": len([c for c in calls if c.get("call_index") is not None]),
            "oracle_requests": len([c for c in calls if c.get("op") != "describe"]),
            "oracle_override_attempts": len(overrides),
            "egress_denied": len(denied),
            "unparseable_transcript_lines": tr.bad_lines,
        },
    }
    if relay is not None:
        out["api_relay"] = relay
        out["counts"]["api_relay_requests"] = relay["requests"]
        out["counts"]["api_relay_denied"] = relay["denied"]
    if write:
        from dbwm.core.verdict import atomic_write_text, dump_json

        atomic_write_text(adir / AUDIT_FILE, dump_json(out))
    return out


def zero_cost_completion(tr: Transcript) -> dict[str, Any] | None:
    """The shape of check (j), or None: a non-error result, cost exactly 0 (a number, not missing), at most
    one turn and no tool_use anywhere in the transcript."""
    r = tr.result
    if not isinstance(r, dict) or r.get("is_error") is True:
        return None
    cost, turns = r.get("total_cost_usd"), r.get("num_turns")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost != 0:
        return None
    if isinstance(turns, bool) or not isinstance(turns, int) or turns > 1:
        return None
    if tr.tool_uses:
        return None
    return {"total_cost_usd": cost, "num_turns": turns, "subtype": r.get("subtype"), "tool_uses": 0}


def audit_summary(audit: Mapping[str, Any]) -> dict[str, Any]:
    """The compact form stored in ``verdict["audit"]`` (what reports read)."""
    return {
        "schema": audit.get("schema", AUDIT_SCHEMA),
        "flagged": bool(audit.get("flagged")),
        "reasons": list(audit.get("reasons") or []),
        "notes": list(audit.get("notes") or []),
    }


def _eval_attr(eval_def: Any, name: str) -> Any:
    v = getattr(eval_def, name, None)
    if v is not None:
        return v
    mod = getattr(type(eval_def), "__module__", None)
    if mod:
        import sys

        pkg = sys.modules.get(mod.rsplit(".", 1)[0]) if "." in mod else None
        for m in (sys.modules.get(mod), pkg):
            if m is not None and getattr(m, name, None) is not None:
                return getattr(m, name)
    return None


def _rel(path: str, base: Path) -> str:
    member = ""
    if "!" in path:
        path, member = path.split("!", 1)
        member = "!" + member
    try:
        return Path(path).relative_to(base).as_posix() + member
    except ValueError:
        return Path(path).name + member


def _egress_denied(rec: Mapping[str, Any]) -> bool:
    if "allowed" in rec:
        return rec.get("allowed") is False
    dec = str(rec.get("decision") or rec.get("action") or "").lower()
    if dec:
        return dec in ("deny", "denied", "block", "blocked")
    return rec.get("status") == 403


def _scan_values(f: _Findings, tr: Transcript, deliv: Path, values: Sequence[Any],
                 oracle_tool_names: set[str], shim_names: set[str],
                 disclosed: Sequence[Any] | None = None) -> None:
    from dbwm.core import canary as core_canary

    vals = core_canary.filter_values(values)
    if not vals:
        return
    # Occurrences wholly inside a string the brief discloses on purpose
    # (ArmSpec.disclosed_values) are not answer-key reads.
    ds = [str(d) for d in (disclosed or ()) if d is not None and str(d)]
    covers = {v: core_canary.disclosure_covers(v, ds) for v in vals}
    # Events in transcript order: agent text, tool inputs, tool results.
    events: list[tuple[int, int, str, str]] = []  # (seq, order, kind, text)
    for seq, text in tr.agent_text:
        events.append((seq, 0, "agent_output", text))
    for tu in tr.tool_uses:
        events.append((tu.seq, 1, "agent_output", json.dumps(tu.input, sort_keys=True, default=str)))
        if tu.result_text is not None:
            kind = "tool_result"
            if tu.name in oracle_tool_names or tu.name.startswith("mcp__"):
                kind = "oracle_result"
            elif tu.name in BASH_TOOLS and isinstance(tu.input, dict):
                progs, _ = bash_programs_and_tokens(str(tu.input.get("command") or ""))
                if progs and all(p in shim_names for p in progs):
                    kind = "oracle_result"
            events.append((tu.result_seq or tu.seq, 2, kind, tu.result_text))
    events.sort(key=lambda e: (e[0], e[1]))
    explained: set[str] = set()
    for _seq, _o, kind, text in events:
        for v in vals:
            if not core_canary.occurs_undisclosed(text, v, covers[v]):
                continue
            if kind == "oracle_result":
                if v not in explained:
                    explained.add(v)
                    f.note(N_ORACLE_VALUE, where=kind, value=_fingerprint(v))
            elif v in explained:
                f.note(N_ORACLE_VALUE, where=kind, value=_fingerprint(v))
            else:
                f.flag(F_ANSWER_KEY, where=kind, value=_fingerprint(v))
    if deliv.is_dir():
        for label, v in core_canary.scan_for_values(deliv, vals, disclosed=ds):
            if v in explained:
                f.note(N_ORACLE_VALUE, where="deliverable", file=_rel(label, deliv), value=_fingerprint(v))
            else:
                f.flag(F_ANSWER_KEY, where="deliverable", file=_rel(label, deliv), value=_fingerprint(v))


# ---------------------------------------------------------------------------
# Whole run
# ---------------------------------------------------------------------------

def _cell_from_dir(eval_id: str, cell_dir_name: str) -> Cell | None:
    parts = cell_dir_name.rsplit("__", 2)
    if len(parts) != 3 or not parts[2].startswith("r") or not parts[2][1:].isdigit():
        return None
    return Cell(eval_id=eval_id, instance=parts[0], arm=parts[1], rep=int(parts[2][1:]))


def iter_attempt_dirs(run_dir: str | os.PathLike) -> list[Path]:
    """``cells/<cell_id>/attempt-<k>/`` directories under a run, sorted."""
    base = Path(run_dir) / "cells"
    if not base.is_dir():
        return []
    out = [p for p in base.glob("*/attempt-*") if p.is_dir()]

    def key(p: Path) -> tuple[str, int]:
        k = p.name.split("-", 1)[1]
        return (p.parent.name, int(k) if k.isdigit() else 0)

    return sorted(out, key=key)


def audit_run(run_dir: str | os.PathLike, eval_def: Any, *, update_verdicts: bool = True,
              **kwargs: Any) -> dict[str, Any]:
    """Audit every attempt under ``run_dir``; write audit.json per attempt and audit_summary.json.

    With ``update_verdicts`` the compact audit summary is merged into each
    attempt's verdict.json (so reports can exclude flagged cells). Identical
    canonical deliverables across different cells are recorded as notes
    (independent agents can converge; sandboxes are isolated).
    """
    from dbwm.core import verdict as core_verdict

    run = Path(run_dir)
    per_attempt: list[dict[str, Any]] = []
    by_reason: dict[str, int] = {}
    hashes: dict[str, list[str]] = {}
    errors: list[str] = []
    for adir in iter_attempt_dirs(run):
        cell = _cell_from_dir(eval_def.eval_id, adir.parent.name)
        if cell is None:
            errors.append(f"cannot parse cell id from {adir.parent.name!r}")
            continue
        try:
            a = audit_attempt(adir, eval_def, cell, **kwargs)
            summary_a = audit_summary(a)
        except Exception as e:  # noqa: BLE001 - an audit crash excludes the attempt, visibly
            errors.append(f"{adir.relative_to(run).as_posix()}: audit error: {type(e).__name__}: {e}")
            a = {"flagged": True, "reasons": ["audit_error"], "notes": []}
            summary_a = {"schema": AUDIT_SCHEMA, "flagged": True, "reasons": ["audit_error"], "notes": [],
                         "error": f"{type(e).__name__}: {e}"}
        per_attempt.append({"cell_id": cell.cell_id, "attempt": adir.name, "flagged": a["flagged"],
                            "reasons": a["reasons"], "notes": a["notes"]})
        for r in a["reasons"]:
            by_reason[r] = by_reason.get(r, 0) + 1
        deliv = adir / DELIVERABLES
        if deliv.is_dir():
            for fp in sorted(deliv.rglob("*")):
                if fp.is_file() and fp.stat().st_size:
                    hashes.setdefault(canonical_sha256(fp), []).append(f"{cell.cell_id}/{adir.name}/"
                                                                       f"{fp.relative_to(deliv).as_posix()}")
        if update_verdicts:
            # Every ruleset's verdict of this attempt (verdict.json and the
            # re-scorings verdict.<ruleset>.json) carries the same audit.
            for vpath in sorted(adir.glob("verdict*.json")):
                if not re.fullmatch(r"verdict(?:\.[A-Za-z0-9_-]+)?\.json", vpath.name):
                    continue
                try:
                    core_verdict.update_audit(vpath, summary_a)
                except core_verdict.VerdictError as e:
                    errors.append(f"{vpath.relative_to(run).as_posix()}: {e}")
    cross = []
    for h, where in sorted(hashes.items()):
        cells = {w.split("/", 1)[0] for w in where}
        if len(cells) > 1:
            cross.append({"sha256": h, "files": where})
    summary = {
        "schema": AUDIT_SCHEMA + "-run",
        "eval": eval_def.eval_id,
        "attempts": len(per_attempt),
        "flagged": [a for a in per_attempt if a["flagged"]],
        "by_reason": dict(sorted(by_reason.items())),
        "cross_cell_identical_deliverables": cross,
        "errors": errors,
    }
    from dbwm.core.verdict import atomic_write_text, dump_json

    atomic_write_text(run / RUN_SUMMARY_FILE, dump_json(summary))
    return summary
