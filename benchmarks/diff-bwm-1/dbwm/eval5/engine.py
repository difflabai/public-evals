"""Deterministic checklist engine for the eval-5 consulting scenarios (the decidable oracle).

The engine compiles the ``auto`` specs of a scenario registry into deterministic
tests over a submission directory (the prose deliverable, the structured
deliverable and claims.json). Checks whose ``auto`` is null are MANUAL items
and are never scored here.

Two engines live side by side. Three ruleset names select them (the registry
each ruleset reads is chosen in ``dataset.REGISTRY_FILE``):

``paper`` and ``as-run``
    The engine exactly as the paper ran it
    (``scripts/difflab_consulting/checker.py`` at base 87a3ffac plus the
    uncommitted harness diff; the authors' 2026-10-03 fix changed the SOLVARA-A-13
    registry line, not the checker). ``paper`` pairs it with the repaired
    registry, ``as-run`` with registry v0.1 as the paper's sessions were
    scored. The op functions below prefixed ``op_`` are that file's code,
    unchanged except that the ``uv run --with pyyaml`` re-exec
    (checker.py:48-57) is gone: PyYAML is a declared dependency of this eval,
    and when it is missing the paper engine already returned
    ``"pyyaml unavailable"`` (no uv on PATH), which is what happens here too.
    Defects are kept on purpose:
    digits are harvested out of identifiers ("SSE-S3", "CC2.1", "Q3"),
    hyphenated ranges become negative numbers, ``structured_enum`` matches
    both ways so ``''`` satisfies any required value, tokens are raw
    substrings ("IRO" inside "environment"), and absence ops pass on empty
    files.

``v1``
    The release engine. Same DSL and the same op names, with these fixes:

    * numbers are tokenized identifier-safely: ISO dates and code-like
      identifiers (``G-02``, ``INC-2026-007``, ``FAC-008``) are masked, a
      digit glued to a letter/dot/underscore on its left is not a number
      (``S3``, ``CC2.1``, ``Q3``, ``FY2025``), ordinal labels (``Phase 2``,
      ``Step 3``, ``Tier 1``) are masked, and no minus sign is parsed (a
      hyphen between two numbers is a range, not a negative);
    * tokens match on word boundaries after ``[-_\\s]+`` normalization: the
      left edge is always a word boundary; the right edge is a word boundary
      (optional plural ``s``) when the token's last word is at most four
      characters or written in capitals, otherwise the token is a prefix
      ("appeal" matches "appeals", "IRO" no longer matches "environment",
      "api" no longer matches "rapid", "prior-authorization" matches "prior
      authorization");
    * ``structured_enum`` is one-directional (the required value must occur in
      a found value) and ignores empty strings;
    * new ops used by registry v1.0: ``k_of`` (at least k of n sub-specs),
      ``csv_none`` (no row matches where+require, unless an exemption
      matches; fails on an empty table), ``text_amount_near`` (money amounts
      with K/M/B scale words, compared in base units), and
      ``structured_polarity`` (every leaf under a path that mentions X must
      also carry a negation/redirect cue). ``text_number_near`` gains
      ``same_line`` and ``follow_regex``.

    Non-vacuity of absence checks is a registry property (every absence op in
    registry v1.0 sits in an ``all_of`` with a positive gate); the validator
    enforces it.
"""

from __future__ import annotations

import csv
import errno
import io
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Callable, Mapping

RULESET_PAPER = "paper"
RULESET_V1 = "v1"
RULESET_AS_RUN = "as-run"
RULESETS = (RULESET_PAPER, RULESET_V1, RULESET_AS_RUN)
# Rulesets scored by the paper engine (PAPER_OPS); they differ only in the registry they read.
PAPER_ENGINE_RULESETS = (RULESET_PAPER, RULESET_AS_RUN)

# Deliverables are small text files; anything bigger is not a deliverable.
MAX_DELIVERABLE_BYTES = 5 * 1024 * 1024


def _yaml_available() -> bool:
    try:
        import yaml  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------
# File loading
# ---------------------------------------------------------------------------


class Submission:
    """Lazy view of a submission dir: structured docs, CSV tables, raw text (checker.py:60-118)."""

    def __init__(self, root):
        self.root = Path(root)
        self._structured = {}
        self._csv = {}
        self._text = {}

    def path(self, name):
        return self.root / name

    def exists(self, name):
        return self.path(name).exists()

    def _read(self, name):
        p = self.path(name)
        return p.read_text(errors="replace") if p.exists() else None

    def text(self, name):
        if name not in self._text:
            self._text[name] = self._read(name)
        return self._text[name]

    def structured(self, name):
        """Parse YAML or JSON. Returns (data, error)."""
        if name not in self._structured:
            raw = self.text(name)
            if raw is None:
                self._structured[name] = (None, "file not found")
            else:
                data, err = None, None
                if name.lower().endswith((".yaml", ".yml")):
                    if not _yaml_available():
                        err = "pyyaml unavailable"
                    else:
                        import yaml
                        try:
                            data = yaml.safe_load(raw)
                        except Exception as e:  # noqa: BLE001
                            err = f"yaml parse error: {e}"
                else:
                    try:
                        data = json.loads(raw)
                    except Exception as e:  # noqa: BLE001
                        err = f"json parse error: {e}"
                self._structured[name] = (data, err)
        return self._structured[name]

    def table(self, name):
        """Parse CSV. Returns (rows-as-dicts, error)."""
        if name not in self._csv:
            raw = self.text(name)
            if raw is None:
                self._csv[name] = (None, "file not found")
            else:
                try:
                    rows = list(csv.DictReader(io.StringIO(raw)))
                    self._csv[name] = (rows, None if rows else "no data rows")
                except Exception as e:  # noqa: BLE001
                    self._csv[name] = (None, f"csv parse error: {e}")
        return self._csv[name]


class SafeSubmission(Submission):
    """A submission view that only ever reads regular files directly inside ``root``.

    Used by the harness-side oracle (it reads a live sandbox the agent
    controls) and by the release scorer. A deliverable that is a symlink,
    resolves outside the root, is not a regular file or is larger than
    MAX_DELIVERABLE_BYTES is treated as absent, so an agent cannot point the
    checker at files outside its sandbox (e.g. the read-only package mount in
    the sidecar).

    The read itself goes through a descriptor opened with O_NOFOLLOW (and
    O_NONBLOCK, so a FIFO cannot stall the oracle) and checked with fstat, so
    a symlink swapped in after ``_ok`` is never followed and a file deleted
    in between is simply absent (red-team finding RT-T: the earlier
    check-then-reopen-by-name was raced across containers).
    """

    def __init__(self, root, allowed: tuple[str, ...] | None = None):
        super().__init__(root)
        self._root_resolved = Path(root).resolve()
        self.allowed = allowed
        self.rejected: dict[str, str] = {}

    def _ok(self, name) -> bool:
        if self.allowed is not None and name not in self.allowed:
            self.rejected[name] = "not a deliverable"
            return False
        if "/" in name or "\\" in name or name in ("", ".", ".."):
            self.rejected[name] = "not a plain file name"
            return False
        p = self.root / name
        try:
            if p.is_symlink():
                self.rejected[name] = "symlink"
                return False
            if not p.exists():
                return False
            rp = p.resolve()
            if rp.parent != self._root_resolved or not rp.is_file():
                self.rejected[name] = "not a regular file in the submission directory"
                return False
            if rp.stat().st_size > MAX_DELIVERABLE_BYTES:
                self.rejected[name] = "larger than the deliverable size cap"
                return False
        except OSError:
            # changed under us (deleted, swapped, a symlink loop): not a deliverable
            self.rejected[name] = "changed while being checked"
            return False
        return True

    def exists(self, name):
        return self._ok(name)

    def _read(self, name):
        if not self._ok(name):
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(str(self.root / name), flags)
        except OSError as e:
            if e.errno == errno.ELOOP:
                self.rejected[name] = "symlink"
            elif e.errno != errno.ENOENT:
                self.rejected[name] = "changed while being checked"
            return None
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                self.rejected[name] = "not a regular file in the submission directory"
                return None
            chunks, total = [], 0
            while True:
                b = os.read(fd, 1 << 20)
                if not b:
                    break
                total += len(b)
                if total > MAX_DELIVERABLE_BYTES:
                    self.rejected[name] = "larger than the deliverable size cap"
                    return None
                chunks.append(b)
        except OSError:
            self.rejected[name] = "changed while being checked"
            return None
        finally:
            os.close(fd)
        # Decode exactly as Path.read_text(errors="replace") did (locale
        # encoding, universal newlines), so checker results are unchanged.
        return io.TextIOWrapper(io.BytesIO(b"".join(chunks)), errors="replace").read()


# ---------------------------------------------------------------------------
# Path resolution for structured_* ops:  a.b[*].c  /  a[0].b   (checker.py:125-159)
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"([^.\[\]]+)|\[(\*|\d+)\]")


def _tokens(path):
    out = []
    for m in _TOKEN.finditer(path):
        if m.group(1) is not None:
            out.append(("key", m.group(1)))
        else:
            out.append(("idx", m.group(2)))
    return out


def resolve_path(data, path):
    """All values matched by the path expression."""
    nodes = [data]
    for kind, tok in _tokens(path):
        nxt = []
        for n in nodes:
            if kind == "key":
                if isinstance(n, dict):
                    # case-insensitive key match
                    for k, v in n.items():
                        if str(k).lower() == tok.lower():
                            nxt.append(v)
            elif tok == "*":
                if isinstance(n, list):
                    nxt.extend(n)
            else:
                if isinstance(n, list) and int(tok) < len(n):
                    nxt.append(n[int(tok)])
        nodes = nxt
        if not nodes:
            break
    return nodes


def _numbers_in(values, lo, hi):
    """Numeric candidates from matched values, incl. pct-string and /100 forms (paper)."""
    hits = []
    fractional_band = hi <= 1.5
    for v in values:
        cands = []
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            cands.append(float(v))
            if fractional_band and 1 < abs(v) <= 100:
                cands.append(float(v) / 100.0)
        elif isinstance(v, str):
            for m in re.finditer(r"-?\d[\d,]*\.?\d*", v):
                try:
                    x = float(m.group(0).replace(",", ""))
                except ValueError:
                    continue
                cands.append(x)
                if (fractional_band and "%" in v[m.end():m.end() + 2]) or \
                        (fractional_band and 1 < abs(x) <= 100):
                    cands.append(x / 100.0)
        for c in cands:
            if lo - 1e-9 <= c <= hi + 1e-9:
                hits.append({"value": v if isinstance(v, str) else c})
                break
    return hits


# ---------------------------------------------------------------------------
# PAPER ops (checker.py:196-464). Each returns (passed: bool, measured: dict).
# ``eng`` is the Engine running the op (for the recursive all_of / any_of).
# ---------------------------------------------------------------------------


def op_file_exists(eng, sub, a):
    ok = sub.exists(a["file"])
    return ok, {"file": a["file"], "exists": ok}


def op_structured_path(eng, sub, a):
    data, err = sub.structured(a["file"])
    if err:
        return False, {"error": err}
    vals = resolve_path(data, a["path"])
    if not vals:
        return False, {"path": a["path"], "found": False}
    m = {"path": a["path"], "found": True}
    ok = True
    if "min_count" in a or "max_count" in a:
        n = len(vals[0]) if len(vals) == 1 and isinstance(vals[0], list) else len(vals)
        m["count"] = n
        if "min_count" in a and n < a["min_count"]:
            ok = False
        if "max_count" in a and n > a["max_count"]:
            ok = False
    return ok, m


def op_structured_number(eng, sub, a):
    data, err = sub.structured(a["file"])
    if err:
        return False, {"error": err}
    vals = resolve_path(data, a["path"])
    hits = _numbers_in(vals, a["lo"], a["hi"])
    return bool(hits), {"path": a["path"], "band": [a["lo"], a["hi"]],
                        "matched": len(vals), "in_band": hits[:5]}


def op_structured_number_all(eng, sub, a):
    """EVERY numeric value at the path must lie in [lo, hi] (and at least one
    must exist) -- the universal counterpart of the existential structured_number."""
    data, err = sub.structured(a["file"])
    if err:
        return False, {"error": err}
    vals = [float(v) for v in resolve_path(data, a["path"])
            if isinstance(v, (int, float)) and not isinstance(v, bool)]
    out = [v for v in vals if not (a["lo"] - 1e-9 <= v <= a["hi"] + 1e-9)]
    return (bool(vals) and not out), {"path": a["path"], "band": [a["lo"], a["hi"]],
                                      "numeric_values": len(vals),
                                      "out_of_band": out[:5]}


def _strings_at(data, path):
    out = []
    for v in resolve_path(data, path):
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, (int, float, bool)):
            out.append(str(v))
        elif isinstance(v, list):
            out.extend(str(x) for x in v if isinstance(x, (str, int, float)))
    return out


def op_structured_enum(eng, sub, a):
    data, err = sub.structured(a["file"])
    if err:
        return False, {"error": err}
    found = [s.lower() for s in _strings_at(data, a["path"])]
    covered = {}
    for req in a["required_values"]:
        covered[req] = any(req.lower() in f or f in req.lower() for f in found)
    frac = sum(covered.values()) / max(1, len(covered))
    ok = frac >= a.get("min_fraction", 1.0) - 1e-9
    return ok, {"path": a["path"], "covered": covered,
                "fraction": round(frac, 3), "values_found": len(found)}


def op_structured_fields(eng, sub, a):
    data, err = sub.structured(a["file"])
    if err:
        return False, {"error": err}
    objs = [v for v in resolve_path(data, a["path"]) if isinstance(v, dict)]
    if not objs:
        return False, {"path": a["path"], "objects": 0}

    def has(o, f):
        # an explicit empty list/dict is a PROVIDED value ("no dependencies"
        # rendered as [] is honest, not missing); only None/"" count as absent
        for k, v in o.items():
            if str(k).lower() == f.lower():
                return v is not None and v != ""
        return False
    good = sum(1 for o in objs if all(has(o, f) for f in a["required_fields"]))
    frac = good / len(objs)
    ok = frac >= a.get("min_fraction", 1.0) - 1e-9
    return ok, {"path": a["path"], "objects": len(objs),
                "complete": good, "fraction": round(frac, 3)}


def op_structured_text_absent(eng, sub, a):
    """No string at the path may contain any forbidden token -- path-scoped absence."""
    data, err = sub.structured(a["file"])
    if err:
        return False, {"error": err}
    found = []
    for s in _strings_at(data, a["path"]):
        low = s.lower()
        for tok in a["tokens"]:
            if tok.lower() in low:
                found.append({"value": s[:120], "token": tok})
    return not found, {"path": a["path"], "forbidden_present": found[:5]}


def _col(row, name):
    for k, v in row.items():
        if k is not None and str(k).strip().lower() == name.strip().lower():
            return (v or "").strip()
    return ""


def op_csv_required(eng, sub, a):
    rows, err = sub.table(a["file"])
    if err:
        return False, {"error": err}
    present = {_col(r, a["column"]).lower() for r in rows}
    covered = {req: req.strip().lower() in present for req in a["required_values"]}
    frac = sum(covered.values()) / max(1, len(covered))
    ok = frac >= a.get("min_fraction", 1.0) - 1e-9
    missing = [k for k, v in covered.items() if not v]
    return ok, {"column": a["column"], "fraction": round(frac, 3),
                "missing": missing[:10]}


def _row_match(row, cond):
    if cond["column"] == "*":  # content-keyed: match anywhere in the row
        val = " ".join(str(v) for v in row.values() if v).lower()
    else:
        val = _col(row, cond["column"]).lower()
    if "in" in cond:
        return any(val == str(x).strip().lower() for x in cond["in"]) \
            if cond["column"] != "*" else \
            any(str(x).strip().lower() in val for x in cond["in"])
    if "regex" in cond:
        return bool(re.search(cond["regex"], val, re.I))
    return False


def op_csv_condition(eng, sub, a):
    rows, err = sub.table(a["file"])
    if err:
        return False, {"error": err}
    where = [r for r in rows if _row_match(r, a["where"])]
    good = [r for r in where if _row_match(r, a["require"])]
    need = a.get("min_rows", 1)
    return len(good) >= need, {"where_rows": len(where), "satisfying": len(good),
                               "min_rows": need}


def op_csv_min_rows(eng, sub, a):
    rows, err = sub.table(a["file"])
    if err:
        return False, {"error": err}
    if "where" in a:
        rows = [r for r in rows if _row_match(r, a["where"])]
    return len(rows) >= a["min_rows"], {"rows": len(rows), "min_rows": a["min_rows"]}


WORD_NUMBERS = {w: i for i, w in enumerate(
    ("zero one two three four five six seven eight nine ten eleven twelve "
     "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty")
    .split())}


def op_text_number_near(eng, sub, a):
    """A number in [lo,hi] near the context. Fairness accommodations:
    the context may match the SAME line or the PREVIOUS line (heading +
    value-on-next-line layouts); small integers (band hi <= 20) also match
    spelled out ("three former employees"); the /100 percent fallback fires
    only when an explicit % marker follows the number."""
    raw = sub.text(a["file"])
    if raw is None:
        return False, {"error": "file not found"}
    rex = re.compile(a["context_regex"], re.I) if a.get("context_regex") else None
    lo, hi = a["lo"], a["hi"]
    fractional = hi <= 1.5
    hits = []
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        if rex and not (rex.search(line) or (i > 0 and rex.search(lines[i - 1]))):
            continue
        for m in re.finditer(r"-?\d[\d,]*\.?\d*", line):
            try:
                x = float(m.group(0).replace(",", ""))
            except ValueError:
                continue
            cands = [x]
            # /100 fallback ONLY for explicit percents ("42%", "42 pct"):
            # a bare 95.2 on a fractional-band line must not pass as 0.952
            pct = re.match(r"\s?(%|percent\b|pct\b)", line[m.end():])
            if fractional and pct and 1 < abs(x) <= 100:
                cands.append(x / 100.0)
            if any(lo - 1e-9 <= c <= hi + 1e-9 for c in cands):
                hits.append({"line": line.strip()[:140], "value": x})
                break
        if hi <= 20 and not hits:  # spelled-out small integers
            for w, v in WORD_NUMBERS.items():
                if lo <= v <= hi and re.search(rf"\b{w}\b", line, re.I):
                    hits.append({"line": line.strip()[:140], "value": v})
                    break
        if len(hits) >= 5:
            break
    return bool(hits), {"band": [lo, hi], "context": a.get("context_regex"),
                        "hits": hits[:3]}


def op_text_contains_any(eng, sub, a):
    raw = sub.text(a["file"])
    if raw is None:
        return False, {"error": "file not found"}
    low = raw.lower()
    found = [t for t in a["tokens"] if t.lower() in low]
    need = a.get("min_hits", 1)
    return len(found) >= need, {"found": found[:10], "min_hits": need}


def op_text_absent(eng, sub, a):
    raw = sub.text(a["file"])
    if raw is None:
        return False, {"error": "file not found"}
    low = raw.lower()
    present = [t for t in a["tokens"] if t.lower() in low]
    return not present, {"forbidden_present": present}


def op_text_regex_count(eng, sub, a):
    raw = sub.text(a["file"])
    if raw is None:
        return False, {"error": "file not found"}
    n = len(re.findall(a["regex"], raw, re.I | re.M))
    return n >= a["min_count"], {"regex": a["regex"], "count": n,
                                 "min_count": a["min_count"]}


def op_all_of(eng, sub, a):
    parts = [eng.run_op(sub, x) for x in a["of"]]
    return all(p for p, _ in parts), {"parts": [m for _, m in parts]}


def op_any_of(eng, sub, a):
    parts = [eng.run_op(sub, x) for x in a["of"]]
    return any(p for p, _ in parts), {"parts": [m for _, m in parts]}


PAPER_OPS: dict[str, Callable] = {
    "file_exists": op_file_exists,
    "structured_path": op_structured_path,
    "structured_number": op_structured_number,
    "structured_number_all": op_structured_number_all,
    "structured_enum": op_structured_enum,
    "structured_fields": op_structured_fields,
    "structured_text_absent": op_structured_text_absent,
    "csv_required": op_csv_required,
    "csv_condition": op_csv_condition,
    "csv_min_rows": op_csv_min_rows,
    "text_number_near": op_text_number_near,
    "text_contains_any": op_text_contains_any,
    "text_absent": op_text_absent,
    "text_regex_count": op_text_regex_count,
    "all_of": op_all_of,
    "any_of": op_any_of,
}


# ---------------------------------------------------------------------------
# v1 helpers: identifier-safe numbers, word-boundary tokens
# ---------------------------------------------------------------------------

# Masked before numbers are read: ISO / slash dates, code-like identifiers
# (uppercase prefix + hyphen + digits: G-02, INC-2026-007, FAC-008, COVID-19),
# and ordinal labels whose number is a name, not a quantity.
_V1_MASKS = (
    re.compile(r"(?<!\d)\d{4}-\d{1,2}-\d{1,2}(?!\d)"),
    re.compile(r"(?<!\d)(?:19|20)\d{2}-(?:0[1-9]|1[0-2])(?!\d)"),
    re.compile(r"(?<!\d)\d{1,2}/\d{1,2}/\d{2,4}(?!\d)"),
    re.compile(r"\b[A-Z][A-Za-z]{0,7}-\d[\w.\-]*"),
    re.compile(r"\b(?:phase|tier|level|step|wave|stage|round|version|section|appendix|exhibit|"
               r"table|figure|item|priority|option|scenario|case|batch|release|rev)\s?#?\d+(?:\.\d+)*\b",
               re.I))
# A number: not glued to a letter, digit, dot or underscore on its left
# (S3, CC2.1, Q3, FY2025, v2.1), no sign (a hyphen between numbers is a range).
_V1_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])\d[\d,]*(?:\.\d+)?")


def _mask(line: str) -> str:
    for rx in _V1_MASKS:
        line = rx.sub(lambda m: " " * len(m.group(0)), line)
    return line


def v1_numbers(text: str):
    """Yield (value, start, end) for identifier-safe numbers in one line of text."""
    masked = _mask(text)
    for m in _V1_NUMBER.finditer(masked):
        s = m.group(0).rstrip(",")
        try:
            x = float(s.replace(",", ""))
        except ValueError:
            continue
        yield x, m.start(), m.start() + len(s)


_SCALES = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "mn": 1e6, "million": 1e6,
           "b": 1e9, "bn": 1e9, "billion": 1e9}
_SCALE_RE = re.compile(r"\s?(k|K|thousand|M|MM|mn|million|B|bn|billion)\b")


def v1_amounts(text: str):
    """Yield (base_value, raw, start) for money-like amounts with an optional scale word."""
    masked = _mask(text)
    for m in _V1_NUMBER.finditer(masked):
        s = m.group(0).rstrip(",")
        try:
            x = float(s.replace(",", ""))
        except ValueError:
            continue
        end = m.start() + len(s)
        sm = _SCALE_RE.match(text, end)
        scale = 1.0
        raw = s
        if sm:
            scale = _SCALES[sm.group(1).lower()]
            raw = text[m.start():sm.end()]
        yield x * scale, raw, m.start()


def _norm_text(s: str) -> str:
    return re.sub(r"[-_\s]+", " ", s.lower()).strip()


_TOKEN_CACHE: dict[str, re.Pattern] = {}


def token_pattern(tok: str) -> re.Pattern:
    """Word-boundary matcher for a registry token (see the module docstring)."""
    p = _TOKEN_CACHE.get(tok)
    if p is None:
        t = _norm_text(tok)
        last = re.split(r"[ ]", t)[-1] if t else ""
        last_orig = re.split(r"[-_\s]+", tok.strip())[-1] if tok.strip() else ""
        strict = len(last) <= 4 or (last_orig.isupper() and any(c.isalpha() for c in last_orig))
        left = r"(?<![a-z0-9])" if t[:1].isalnum() else ""
        right = r"s?(?![a-z0-9])" if (strict and t[-1:].isalnum()) else ""
        p = re.compile(left + re.escape(t) + right)
        _TOKEN_CACHE[tok] = p
    return p


def token_in(tok: str, text: str) -> bool:
    return bool(token_pattern(tok).search(_norm_text(text)))


# ---------------------------------------------------------------------------
# v1 ops (fixes + new ops). Same (passed, measured) contract.
# ---------------------------------------------------------------------------


def _v1_numbers_in(values, lo, hi):
    hits = []
    fractional_band = hi <= 1.5
    for v in values:
        cands = []
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            cands.append(float(v))
            if fractional_band and 1 < abs(v) <= 100:
                cands.append(float(v) / 100.0)
        elif isinstance(v, str):
            for x, _s, e in v1_numbers(v):
                cands.append(x)
                if (fractional_band and "%" in v[e:e + 2]) or (fractional_band and 1 < abs(x) <= 100):
                    cands.append(x / 100.0)
        for c in cands:
            if lo - 1e-9 <= c <= hi + 1e-9:
                hits.append({"value": v if isinstance(v, str) else c})
                break
    return hits


def v1_structured_number(eng, sub, a):
    data, err = sub.structured(a["file"])
    if err:
        return False, {"error": err}
    vals = resolve_path(data, a["path"])
    hits = _v1_numbers_in(vals, a["lo"], a["hi"])
    return bool(hits), {"path": a["path"], "band": [a["lo"], a["hi"]],
                        "matched": len(vals), "in_band": hits[:5]}


def v1_structured_enum(eng, sub, a):
    data, err = sub.structured(a["file"])
    if err:
        return False, {"error": err}
    found = [s for s in _strings_at(data, a["path"]) if s.strip()]
    covered = {}
    for req in a["required_values"]:
        covered[req] = any(token_in(req, f) for f in found)
    frac = sum(covered.values()) / max(1, len(covered))
    ok = frac >= a.get("min_fraction", 1.0) - 1e-9
    return ok, {"path": a["path"], "covered": covered,
                "fraction": round(frac, 3), "values_found": len(found)}


def v1_structured_text_absent(eng, sub, a):
    data, err = sub.structured(a["file"])
    if err:
        return False, {"error": err}
    found = []
    for s in _strings_at(data, a["path"]):
        for tok in a["tokens"]:
            if token_in(tok, s):
                found.append({"value": s[:120], "token": tok})
    return not found, {"path": a["path"], "forbidden_present": found[:5]}


def v1_text_number_near(eng, sub, a):
    """paper text_number_near with identifier-safe numbers, plus two options.

    ``same_line``: the context must match the number's own line (no
    previous-line accommodation). ``follow_regex``: the in-band number must be
    followed (within 40 characters) by text matching this regex, e.g. "4
    specialty pharmacies".
    """
    raw = sub.text(a["file"])
    if raw is None:
        return False, {"error": "file not found"}
    rex = re.compile(a["context_regex"], re.I) if a.get("context_regex") else None
    fol = re.compile(a["follow_regex"], re.I) if a.get("follow_regex") else None
    same_line = bool(a.get("same_line"))
    lo, hi = a["lo"], a["hi"]
    fractional = hi <= 1.5
    hits = []
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        if rex and not (rex.search(line) or (not same_line and i > 0 and rex.search(lines[i - 1]))):
            continue
        for x, _s, e in v1_numbers(line):
            cands = [x]
            pct = re.match(r"\s?(%|percent\b|pct\b)", line[e:])
            if fractional and pct and 1 < abs(x) <= 100:
                cands.append(x / 100.0)
            if fol and not fol.match(line[e:e + 40]):
                continue
            if any(lo - 1e-9 <= c <= hi + 1e-9 for c in cands):
                hits.append({"line": line.strip()[:140], "value": x})
                break
        if hi <= 20 and not hits:  # spelled-out small integers
            masked = _mask(line)
            for w, v in WORD_NUMBERS.items():
                if not (lo <= v <= hi):
                    continue
                for wm in re.finditer(rf"\b{w}\b", masked, re.I):
                    if fol and not fol.match(line[wm.end():wm.end() + 40]):
                        continue
                    hits.append({"line": line.strip()[:140], "value": v})
                    break
                if hits:
                    break
        if len(hits) >= 5:
            break
    return bool(hits), {"band": [lo, hi], "context": a.get("context_regex"),
                        "hits": hits[:3]}


def v1_text_amount_near(eng, sub, a):
    """A money amount in [lo, hi] (base units) on a context line.

    Scale words are honoured ("$2.76M", "2.76 million", "150K"), so a bare
    "Phase 1" or "3 years" on a "million" line is never read as $1M or $3M.
    Same context rules as text_number_near (``same_line`` optional).
    """
    raw = sub.text(a["file"])
    if raw is None:
        return False, {"error": "file not found"}
    rex = re.compile(a["context_regex"], re.I) if a.get("context_regex") else None
    same_line = bool(a.get("same_line"))
    lo, hi = a["lo"], a["hi"]
    hits = []
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        if rex and not (rex.search(line) or (not same_line and i > 0 and rex.search(lines[i - 1]))):
            continue
        for x, rawtxt, _s in v1_amounts(line):
            if lo - 1e-9 <= x <= hi + 1e-9:
                hits.append({"line": line.strip()[:140], "value": x, "as_written": rawtxt})
                break
        if len(hits) >= 5:
            break
    return bool(hits), {"band": [lo, hi], "context": a.get("context_regex"), "hits": hits[:3]}


def v1_text_contains_any(eng, sub, a):
    raw = sub.text(a["file"])
    if raw is None:
        return False, {"error": "file not found"}
    found = [t for t in a["tokens"] if token_in(t, raw)]
    need = a.get("min_hits", 1)
    return len(found) >= need, {"found": found[:10], "min_hits": need}


def v1_text_absent(eng, sub, a):
    raw = sub.text(a["file"])
    if raw is None:
        return False, {"error": "file not found"}
    present = [t for t in a["tokens"] if token_in(t, raw)]
    return not present, {"forbidden_present": present}


def v1_k_of(eng, sub, a):
    """At least ``min_pass`` of the sub-specs pass."""
    parts = [eng.run_op(sub, x) for x in a["of"]]
    k = sum(1 for p, _ in parts if p)
    need = a["min_pass"]
    return k >= need, {"passed_parts": k, "min_pass": need, "parts": [m for _, m in parts]}


def v1_csv_none(eng, sub, a):
    """No data row matches ``where`` AND ``require`` (rows matching ``unless`` are exempt).

    Fails on a missing/empty table, so the absence is never vacuous.
    """
    rows, err = sub.table(a["file"])
    if err:
        return False, {"error": err}
    bad = [r for r in rows if _row_match(r, a["where"]) and _row_match(r, a["require"])
           and not (a.get("unless") and _row_match(r, a["unless"]))]
    return not bad, {"rows": len(rows), "violating_rows": len(bad),
                     "examples": [" | ".join(str(v) for v in r.values() if v)[:120] for r in bad[:3]]}


def _leaves(node, prefix=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _leaves(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _leaves(v, f"{prefix}[{i}]")
    elif node is not None and not isinstance(node, bool):
        yield prefix, str(node)


def v1_structured_polarity(eng, sub, a):
    """Every leaf under ``path`` whose key path or value mentions ``mention_regex``
    must also match ``allow_regex`` (a negation, exclusion or redirect cue).

    Fails when the path does not resolve (no vacuous pass). Used for the
    copay-eligibility trap: "eligible_population: commercially insured only;
    Medicare/Medicaid not eligible" passes, "eligibility: all patients
    including government-insured" fails.
    """
    data, err = sub.structured(a["file"])
    if err:
        return False, {"error": err}
    nodes = resolve_path(data, a["path"])
    if not nodes:
        return False, {"path": a["path"], "found": False}
    mention = re.compile(a["mention_regex"], re.I)
    allow = re.compile(a["allow_regex"], re.I)
    viol = []
    n = 0
    for node in nodes:
        for kp, val in _leaves(node):
            blob = f"{kp.replace('_', ' ')}: {val}"
            if mention.search(blob):
                n += 1
                if not allow.search(blob):
                    viol.append(blob[:140])
    return not viol, {"path": a["path"], "mentions": n, "violations": viol[:3]}


V1_OPS: dict[str, Callable] = dict(PAPER_OPS)
V1_OPS.update({
    "structured_number": v1_structured_number,
    "structured_enum": v1_structured_enum,
    "structured_text_absent": v1_structured_text_absent,
    "text_number_near": v1_text_number_near,
    "text_contains_any": v1_text_contains_any,
    "text_absent": v1_text_absent,
    "k_of": v1_k_of,
    "csv_none": v1_csv_none,
    "text_amount_near": v1_text_amount_near,
    "structured_polarity": v1_structured_polarity,
})

ABSENCE_OPS = ("text_absent", "structured_text_absent", "csv_none", "structured_polarity")


class Engine:
    """One ruleset's op table. ``run_op``/``run_checks`` mirror checker.py:467-516."""

    def __init__(self, ruleset: str):
        if ruleset in PAPER_ENGINE_RULESETS:
            self.ops = PAPER_OPS
        elif ruleset == RULESET_V1:
            self.ops = V1_OPS
        else:
            raise ValueError(f"unknown ruleset {ruleset!r}")
        self.ruleset = ruleset

    def run_op(self, sub, spec):
        fn = self.ops.get(spec.get("op"))
        if fn is None:
            return False, {"error": f"unknown op {spec.get('op')!r}"}
        try:
            return fn(self, sub, spec)
        except Exception as e:  # noqa: BLE001
            return False, {"error": f"op raised: {e}"}

    def run_checks(self, reg, submission, submission_label=None):
        sub = submission if isinstance(submission, Submission) else Submission(submission)
        results, manual = [], []
        for item in reg:
            if not item.get("auto"):
                manual.append({"id": item["id"], "title": item["title"]})
                continue
            passed, measured = self.run_op(sub, item["auto"])
            results.append({"id": item["id"], "title": item["title"],
                            "pass": bool(passed), "measured": measured,
                            "criteria": item["criteria"]})
        return {
            "submission": str(submission_label if submission_label is not None else sub.root),
            "checks": results,
            "manual_items": manual,
            "summary": {"passed": sum(r["pass"] for r in results),
                        "total": len(results),
                        "failed_ids": [r["id"] for r in results if not r["pass"]]},
        }


def run_checks(reg, submission, ruleset: str, submission_label=None):
    return Engine(ruleset).run_checks(reg, submission, submission_label)


# ---------------------------------------------------------------------------
# Registry and renderings
# ---------------------------------------------------------------------------


def load_registry(checks_path) -> list[dict[str, Any]]:
    """[{id, title, criteria, pass_condition, auto}] from a checks.jsonl.

    checker.py:482-495 plus the authors' 2026-10-03 ``pass_condition`` field
    (consulting_planner.stripped_registry at aaa04e5: ``score_1_criteria``).
    The engine reads id/title/criteria/auto only, so scoring is unchanged;
    ``render_checklist_v1`` reads ``pass_condition``.
    """
    items = []
    for line in Path(checks_path).read_text().splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        items.append({"id": c["check_id"], "title": c.get("title", c["check_id"]),
                      "criteria": c.get("check_description", ""),
                      "pass_condition": c.get("score_1_criteria", ""),
                      "auto": c.get("auto")})
    return items


CHECKLIST_HEADER = (
    "QA CHECKLIST for the engagement deliverables (client review standard).",
    "The reviewed submission is expected to satisfy EVERY item:", "")
PASSES_WHEN = "    PASSES WHEN: "


def render_checklist_as_run(reg) -> str:
    """The CHECKLIST.md text the published sessions' checklist arms received (checker.py:519-526
    at base 87a3ffac): one line per check (id, title, description), no pass conditions."""
    lines = list(CHECKLIST_HEADER)
    for i, item in enumerate(reg, 1):
        tag = "" if item.get("auto") else " [manual review item]"
        lines.append(f"{i:2d}. [{item['id']}] [{item['title']}]{tag} {item['criteria']}")
    return "\n".join(lines) + "\n"


def render_checklist_v1(reg, pass_conditions: bool = True) -> str:
    """The CHECKLIST.md text new runs stage, byte-identical in the three checklist arms.

    With ``pass_conditions`` (the default) every item with a non-empty pass
    condition (its registry ``score_1_criteria``; manual items included) gets a
    ``    PASSES WHEN: <condition>`` line under it, the layout of the authors'
    2026-10-03 prose rendering (the paper's checker.py:519-542). Unlike the original, no arm
    receives any registry or engine, so every arm sees exactly this text and
    nothing more. Without pass
    conditions the text equals ``render_checklist_as_run`` of the same registry.
    """
    if not pass_conditions:
        return render_checklist_as_run(reg)
    lines = list(CHECKLIST_HEADER)
    for i, item in enumerate(reg, 1):
        tag = "" if item.get("auto") else " [manual review item]"
        lines.append(f"{i:2d}. [{item['id']}] [{item['title']}]{tag} {item['criteria']}")
        pc = (item.get("pass_condition") or "").strip()
        if pc:
            lines.append(PASSES_WHEN + pc)
    return "\n".join(lines) + "\n"


def pass_conditions(reg) -> list[str]:
    """The pass-condition texts ``render_checklist_v1`` prints (non-empty, stripped, in order)."""
    return [pc for pc in ((item.get("pass_condition") or "").strip() for item in reg) if pc]


def paper_cli_render(res: Mapping[str, Any], mode: str) -> str:
    """Exactly what ``checker.py --mode verdict|figures`` printed (checker.py:555-565).

    ``print(prose_checklist(reg))`` is not covered here (prose mode); the
    paper's prose rendering is render_checklist_as_run(reg) + "\\n".
    """
    s = res["summary"]
    out = [f"CHECKLIST RESULT: {s['passed']}/{s['total']} PASS "
           f"({len(res['manual_items'])} manual items not machine-checked)"]
    for r in res["checks"]:
        out.append(f"  {'PASS' if r['pass'] else 'FAIL'}  {r['id']:12s} {r['title']}")
        if mode == "figures":
            out.append(f"        criteria: {r['criteria'][:200]}")
            out.append(f"        measured: {json.dumps(r['measured'], default=str)[:260]}")
    for m in res["manual_items"]:
        out.append(f"  MANUAL {m['id']:12s} {m['title']} (not machine-checkable)")
    return "\n".join(out) + "\n"


# Keys in a ``measured`` dict that carry the grader's own spec (bands,
# regexes, required-value lists) rather than what was found in the
# submission. The v1 figures rendering drops them: no arm receives the
# registry.
_SPEC_KEYS = ("band", "context", "regex", "min_count", "min_hits", "min_rows", "min_pass",
              "missing", "path", "column")


def _redact(measured: Any) -> Any:
    if isinstance(measured, dict):
        out = {}
        for k, v in measured.items():
            if k in _SPEC_KEYS:
                continue
            if k == "covered" and isinstance(v, dict):
                out["covered"] = f"{sum(1 for x in v.values() if x)}/{len(v)}"
                continue
            out[k] = _redact(v)
        return out
    if isinstance(measured, list):
        return [_redact(x) for x in measured]
    return measured


def figures_render_v1(res: Mapping[str, Any]) -> str:
    """The v1 ``figures`` text: the paper's layout, with the grader spec redacted
    from the measured values (what was found in YOUR files stays)."""
    s = res["summary"]
    out = [f"CHECKLIST RESULT: {s['passed']}/{s['total']} PASS "
           f"({len(res['manual_items'])} manual items not machine-checked)"]
    for r in res["checks"]:
        out.append(f"  {'PASS' if r['pass'] else 'FAIL'}  {r['id']:12s} {r['title']}")
        out.append(f"        criteria: {r['criteria'][:200]}")
        out.append(f"        measured: {json.dumps(_redact(r['measured']), default=str)[:260]}")
    for m in res["manual_items"]:
        out.append(f"  MANUAL {m['id']:12s} {m['title']} (not machine-checkable)")
    return "\n".join(out) + "\n"


def walk_auto(spec: Mapping[str, Any], fn: Callable[[Mapping[str, Any]], None]) -> None:
    fn(spec)
    if spec.get("op") in ("all_of", "any_of", "k_of"):
        for s in spec.get("of", []):
            walk_auto(s, fn)
