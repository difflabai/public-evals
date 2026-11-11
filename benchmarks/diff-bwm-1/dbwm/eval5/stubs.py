"""Deterministic stub submissions (strong / medium / weak) and the eval-5 stub agent.

Port of scripts/difflab_consulting/make_stubs.py. A stub is synthesized from
the scenario's own auto-check registry, so checker-vs-stub comparisons are
tautological by design: they validate MECHANICS (that every auto check is
satisfiable, that the checker separates tiers, that the pipeline runs), not
agents.

  strong   applies every auto spec (a constraint satisfier over the DSL) and
           reports the true anchor values as key figures
  medium   skips ~35% of the specs (sha256-deterministic per scenario/check);
           one headline figure drifts out of band; still claims success
  weak     generic boilerplate, hollow structured file, wrong figures, claims
           success

``render(scenario, tier, outdir, ruleset)`` with ruleset "as-run" (or "paper", whose
registry differs only in SOLVARA-A-13) reproduces the paper harness's stub tier
counts; the v1 extensions handle the v1.0-only ops (k_of, csv_none, text_amount_near,
structured_polarity, follow_regex).

``stub_agent`` is what the ``stub`` adapter calls for runner end-to-end tests:
baseline writes the weak stub, checklist-prose the medium stub, and the two
tool arms write the medium stub, call the harness ``qa_check`` oracle, repair
every item it reports as FAIL (from the verdict text alone) and check again.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from . import dataset as D
from . import engine as E

TIER_BY_ARM = {"baseline": "weak", "checklist-prose": "medium",
               "checklist-verdict": "medium", "checklist-executable": "medium"}


def _det_skip(check_id: str, scenario: str, frac: float) -> bool:
    """Deterministic pseudo-random skip decision (no RNG: resumable/stable)."""
    h = int(hashlib.sha256(f"{scenario}:{check_id}".encode()).hexdigest(), 16)
    return (h % 100) < frac * 100


class DocBuilder:
    """Grow a nested dict/list document to satisfy path constraints (make_stubs.py:57-160)."""

    def __init__(self):
        self.root: dict[str, Any] = {}

    def _walk(self, path, want_list_len=None):
        tokens = []
        for m in re.finditer(r"([^.\[\]]+)|\[(\*|\d+)\]", path):
            tokens.append(("key", m.group(1)) if m.group(1) is not None else ("idx", m.group(2)))
        slots = [(None, None, self.root)]
        for t, (kind, tok) in enumerate(tokens):
            last = t == len(tokens) - 1
            nxt = []
            for parent, pkey, node in slots:
                if kind == "key":
                    if not isinstance(node, dict):
                        node = {}
                        _set(parent, pkey, node)
                    if last:
                        nxt.append((node, tok, node.get(tok)))
                    else:
                        peek = tokens[t + 1]
                        default = [] if peek[0] == "idx" else {}
                        if not isinstance(node.get(tok), (dict, list)):
                            node[tok] = default
                        nxt.append((node, tok, node[tok]))
                else:
                    if not isinstance(node, list):
                        node = []
                        _set(parent, pkey, node)
                    if tok == "*":
                        n = max(want_list_len or 1, len(node), 1)
                        while len(node) < n:
                            node.append({})
                        for i in range(len(node)):
                            if last:
                                nxt.append((node, i, node[i]))
                            else:
                                if not isinstance(node[i], (dict, list)):
                                    node[i] = {}
                                nxt.append((node, i, node[i]))
                    else:
                        i = int(tok)
                        while len(node) <= i:
                            node.append({})
                        nxt.append((node, i, node[i]))
            slots = nxt
        return slots

    def ensure_path(self, path, min_count=None):
        if min_count:
            m = re.match(r"(.*)\[\*\]$", path)
            if m:
                self._walk(path, want_list_len=min_count)
                return
            slots = self._walk(path)
            for parent, key, node in slots:
                if not isinstance(node, list):
                    node = []
                    _set(parent, key, node)
                while len(node) < min_count:
                    node.append({"item": f"entry {len(node) + 1}"})
        else:
            slots = self._walk(path)
            for parent, key, node in slots:
                if node is None:
                    _set(parent, key, "provided")

    def set_number(self, path, value, list_len=None):
        for parent, key, _ in self._walk(path, want_list_len=list_len):
            _set(parent, key, value)

    def set_enum(self, path, values):
        slots = self._walk(path, want_list_len=len(values))
        if len(slots) == 1 and not re.search(r"\[\*\]", path):
            parent, key, node = slots[0]
            merged = list(node) if isinstance(node, list) else []
            merged += [v for v in values if v not in merged]
            _set(parent, key, merged)
            return
        for i, (parent, key, node) in enumerate(slots):
            v = values[i % len(values)]
            if isinstance(node, str) and node and v.lower() not in node.lower():
                v = node + " / " + v
            _set(parent, key, v)

    def fill_fields(self, path, fields, list_len=None):
        for parent, key, node in self._walk(path, want_list_len=list_len):
            if not isinstance(node, dict):
                node = {}
                _set(parent, key, node)
            for f in fields:
                if node.get(f) in (None, "", [], {}):
                    node[f] = f"{f.replace('_', ' ')} — see plan narrative"


def _set(parent, key, value):
    if parent is None:
        return
    parent[key] = value


def _wild_len(spec):
    return spec.get("min_count") or (len(spec.get("required_values", [])) or None)


def _follow_literal(rex: str) -> str:
    lit = _literal_from_regex(rex)
    for cand in (lit, " " + lit, " specialty pharmacies", " units"):
        try:
            if re.match(rex, cand, re.I):
                return cand
        except re.error:
            break
    return " " + lit


def apply_spec(spec, docs, csvs, texts, scrub):
    """Apply one auto spec to the accumulating artifacts (make_stubs.py:178-262 + v1 ops)."""
    op = spec.get("op")
    f = spec.get("file", "")
    if op in ("all_of", "k_of"):
        for s in spec["of"]:
            apply_spec(s, docs, csvs, texts, scrub)
        return
    if op in ("any_of",):
        if spec.get("of"):
            apply_spec(spec["of"][0], docs, csvs, texts, scrub)
        return
    if op == "file_exists":
        if f.endswith((".yaml", ".yml", ".json")):
            docs.setdefault(f, DocBuilder())
        elif f.endswith(".csv"):
            csvs.setdefault(f, {"columns": [], "rows": []})
        else:
            texts.setdefault(f, [])
        return
    if op == "structured_path":
        docs.setdefault(f, DocBuilder()).ensure_path(spec["path"], spec.get("min_count"))
    elif op in ("structured_number", "structured_number_all"):
        mid = round((spec["lo"] + spec["hi"]) / 2.0, 4)
        docs.setdefault(f, DocBuilder()).set_number(spec["path"], mid, _wild_len(spec))
    elif op == "structured_enum":
        docs.setdefault(f, DocBuilder()).set_enum(spec["path"], spec["required_values"])
    elif op == "structured_fields":
        docs.setdefault(f, DocBuilder()).fill_fields(spec["path"], spec["required_fields"])
    elif op in ("structured_text_absent", "structured_polarity"):
        docs.setdefault(f, DocBuilder())  # avoidance-only
        if op == "structured_polarity":
            docs[f].ensure_path(spec["path"])
    elif op == "csv_required":
        t = csvs.setdefault(f, {"columns": [], "rows": []})
        col = spec["column"]
        if col not in t["columns"]:
            t["columns"].append(col)
        present = {r.get(col) for r in t["rows"]}
        for v in spec["required_values"]:
            if v not in present:
                t["rows"].append({col: v})
    elif op == "csv_condition":
        t = csvs.setdefault(f, {"columns": [], "rows": []})
        wc, rc = spec["where"]["column"], spec["require"]["column"]
        if wc == "*":
            wc = "control_description"
        for c in (wc, rc):
            if c not in t["columns"]:
                t["columns"].append(c)
        wv = (spec["where"].get("in") or [_literal_from_regex(spec["where"].get("regex", "match"))])[0]
        rv = (spec["require"].get("in") or ["satisfied"])[0]
        need = spec.get("min_rows", 1)
        have = 0
        for r in t["rows"]:
            if str(r.get(wc, "")).strip().lower() == str(wv).strip().lower() and have < need:
                r[rc] = rv
                have += 1
        for _ in range(need - have):
            t["rows"].append({wc: wv, rc: rv})
    elif op == "csv_none":
        csvs.setdefault(f, {"columns": [], "rows": []})  # avoidance-only
    elif op == "csv_min_rows":
        t = csvs.setdefault(f, {"columns": [], "rows": []})
        w = spec.get("where")
        if w and w["column"] not in t["columns"]:
            t["columns"].append(w["column"])

        def matches(r):
            return (str(r.get(w["column"], "")).strip().lower()
                    in [str(x).strip().lower() for x in w.get("in", [])]) if w else True
        while sum(1 for r in t["rows"] if matches(r)) < spec["min_rows"]:
            row = {w["column"]: w["in"][0]} if w else {}
            t["rows"].append(row)
    elif op in ("text_number_near", "text_amount_near"):
        hint = _literal_from_regex(spec.get("context_regex") or "figure")
        mid = round((spec["lo"] + spec["hi"]) / 2.0, 4)
        tail = _follow_literal(spec["follow_regex"]) if spec.get("follow_regex") else ""
        texts.setdefault(f, []).append(f"- {hint}: {mid}{tail}")
    elif op == "text_contains_any":
        toks = spec["tokens"][: spec.get("min_hits", 1)]
        texts.setdefault(f, []).append("This plan addresses " + "; ".join(toks) + ".")
    elif op == "text_absent":
        scrub.setdefault(f, set()).update(spec["tokens"])
    elif op == "text_regex_count":
        lit = _literal_from_regex(spec["regex"])
        if lit:
            texts.setdefault(f, []).extend([lit] * spec["min_count"])


def _literal_from_regex(rex):
    """A string that MATCHES the regex (verified), for planting in stub text."""
    for branch in _branches(rex):
        s = _sample_branch(branch)
        try:
            if s.strip() and re.search(rex, s, re.I):
                return s.strip()
        except re.error:
            break
    lit = re.sub(r"\\[bBdswSWn]|\(\?i\)|[\^\$\.\*\+\?\(\)\[\]\{\}\\]", " ", rex.split("|")[0])
    return re.sub(r"\s+", " ", lit).strip()


def _branches(pattern):
    out, depth, cls, cur, i = [], 0, False, "", 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            cur += pattern[i:i + 2]
            i += 2
            continue
        if cls:
            cur += ch
            cls = ch != "]"
        elif ch == "[":
            cur += ch
            cls = True
        elif ch == "(":
            depth += 1
            cur += ch
        elif ch == ")":
            depth -= 1
            cur += ch
        elif ch == "|" and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    out.append(cur)
    return out


def _sample_branch(branch):
    out, i = [], 0

    def emit(atom, i):
        if i < len(branch) and branch[i] in "?*+":
            out.append(atom)
            return i + 1
        m = re.match(r"\{(\d+)(?:,\d*)?\}", branch[i:])
        if m:
            out.append(atom * int(m.group(1)))
            return i + m.end()
        out.append(atom)
        return i

    while i < len(branch):
        ch = branch[i]
        if ch == "\\" and i + 1 < len(branch):
            c = branch[i + 1]
            atom = {" ": " ", "s": " ", "d": "5", "w": "a", "b": "", "B": "", "n": "\n"}.get(c, c)
            i = emit(atom, i + 2)
        elif ch == "[":
            j = i + 1
            neg = j < len(branch) and branch[j] == "^"
            if neg:
                j += 1
            k = j
            while k < len(branch) and branch[k] != "]":
                k += 2 if branch[k] == "\\" else 1
            body = branch[j:k]
            atom = "q" if neg else next((c for c in body if c not in "-^\\"), "x")
            if atom == "0" and "9" in body:
                atom = "2"
            i = emit(atom, k + 1)
        elif ch == "(":
            depth, k = 1, i + 1
            while k < len(branch) and depth:
                if branch[k] == "\\":
                    k += 1
                elif branch[k] == "(":
                    depth += 1
                elif branch[k] == ")":
                    depth -= 1
                k += 1
            inner = branch[i + 1:k - 1]
            inner = re.sub(r"^\?(:|i|<[^>]+>|=|!)", "", inner)
            i = emit(_sample_branch(_branches(inner)[0]), k)
        elif ch == ".":
            i = emit(" ", i + 1)
        elif ch in "^$":
            i += 1
        else:
            i = emit(ch, i + 1)
    return "".join(out)


BOILER = {
    "strong": [
        "## Executive summary",
        "This engagement deliverable synthesizes the full source pool, resolves "
        "the conflicting figures against the most authoritative and most recent "
        "evidence, and grounds every recommendation in a cited source document.",
        "## Approach",
        "Findings were triangulated across client-internal documents, interview "
        "transcripts, and external reference material; conflicting claims are "
        "reconciled explicitly in the sections below.",
    ],
    "medium": [
        "## Executive summary",
        "This document summarizes our assessment and recommendations based on "
        "the materials provided.",
    ],
    "weak": [
        "## Executive summary",
        "We are excited to present a world-class, best-in-class strategy that "
        "will unlock synergies and drive transformational value going forward.",
        "Our proven framework leverages industry best practices to deliver "
        "actionable insights across all workstreams.",
    ],
}


def _csv_cell(v):
    v = str(v)
    return f'"{v}"' if ("," in v or '"' in v) else v


def _to_yaml(node, indent=0):
    pad = "  " * indent
    if isinstance(node, dict):
        if not node:
            return pad + "{}"
        out = []
        for k, v in node.items():
            if isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{k}:")
                out.append(_to_yaml(v, indent + 1))
            else:
                out.append(f"{pad}{k}: {_scalar(v)}")
        return "\n".join(out)
    if isinstance(node, list):
        if not node:
            return pad + "[]"
        out = []
        for v in node:
            if isinstance(v, (dict, list)) and v:
                body = _to_yaml(v, indent + 1)
                first, _, rest = body.partition("\n")
                out.append(f"{pad}- {first.strip()}" + ("\n" + rest if rest else ""))
            else:
                out.append(f"{pad}- {_scalar(v)}")
        return "\n".join(out)
    return pad + _scalar(node)


def _scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if re.search(r"[:#\{\}\[\],&\*\?\|>%@`\"']|^\s|\s$", s) or s == "":
        return json.dumps(s)
    return s


def _write_artifacts(sid, tier, outdir, specs):
    meta = D.scenario_meta(sid)
    prose_f, struct_f = meta["prose_deliverable"], meta["structured_deliverable"]
    docs, csvs, texts, scrub = {}, {}, {}, {}
    for spec in specs:
        apply_spec(spec, docs, csvs, texts, scrub)
    for f in (prose_f, struct_f):
        apply_spec({"op": "file_exists", "file": f}, docs, csvs, texts, scrub)

    def _scrubbed(fname, body):
        for tok in scrub.get(fname, ()):
            body = re.sub(re.escape(tok), "[omitted]", body, flags=re.I)
        return body

    written = {}
    for fname, b in docs.items():
        node = b.root
        if fname in texts:
            node["_notes"] = texts.pop(fname)
        body = _to_yaml(node) if fname.endswith((".yaml", ".yml")) else json.dumps(node, indent=2)
        written[fname] = _scrubbed(fname, body) + "\n"
    for fname, t in csvs.items():
        cols = t["columns"] or ["item"]
        lines = [",".join(cols)]
        for r in t["rows"]:
            lines.append(",".join(_csv_cell(r.get(c, "n/a")) for c in cols))
        lines += ["# " + ln for ln in texts.pop(fname, [])]
        written[fname] = _scrubbed(fname, "\n".join(lines)) + "\n"
    for fname, lines in texts.items():
        body = "\n".join(BOILER[tier]) + "\n\n## Findings and figures\n" + "\n".join(lines) + "\n"
        written[fname] = _scrubbed(fname, body)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for fname, body in written.items():
        (outdir / fname).write_text(body)


def render(sid: str, tier: str, outdir, ruleset: str = E.RULESET_V1) -> dict[str, list[str]]:
    """Write a tier stub (prose, structured, claims.json) into ``outdir``."""
    checks = D.raw_checks(sid, ruleset)
    anchors = D.anchors(sid)
    auto_checks = [c for c in checks if c.get("auto")]
    applied, skipped, specs = [], [], []
    for c in auto_checks:
        skip = (tier == "weak" and c["auto"].get("op") != "file_exists") or \
               (tier == "medium" and _det_skip(c["check_id"], sid, 0.35))
        if skip:
            skipped.append(c["check_id"])
            continue
        specs.append(c["auto"])
        applied.append(c["check_id"])
    _write_artifacts(sid, tier, outdir, specs)
    key_figs = {}
    for j, k in enumerate(D.headline_figures(sid)):
        a = anchors.get(k) or {}
        v = a.get("value", 0)
        if tier == "weak":
            v = round((a.get("band", [0, 1])[1] or 1) * 3.1, 3)
        elif tier == "medium" and j == 0:
            v = round((a.get("band", [0, 1])[1] or 1) * 1.6, 3)
        key_figs[k] = v
    claims = {
        "believes_meets_requirements": True,
        "checklist_self_assessment": {c["check_id"]: True for c in auto_checks},
        "qa_runs": 0,
        "key_figures": key_figs,
        "rationale": f"stub trial (synthesized {tier}-tier submission)",
    }
    (Path(outdir) / "claims.json").write_text(json.dumps(claims, indent=2) + "\n")
    return {"applied": applied, "skipped": skipped}


_FAIL_LINE = re.compile(r"^\s+FAIL\s+(\S+)", re.M)


def stub_agent(cell, sandbox: Path, call_oracle: Callable[[str, dict], Any]) -> None:
    """Deterministic agent for runner end-to-end tests (no LLM).

    The tool arms repair exactly the items the oracle reports as FAIL, using
    nothing but the oracle's text output to choose them, then re-check; so an
    e2e run exercises the sealed-mode server, the CLI/MCP shim payload path and
    the deliverable read-back.
    """
    sid = cell.instance
    tier = TIER_BY_ARM.get(cell.arm, "weak")
    res = render(sid, tier, sandbox, E.RULESET_V1)
    if cell.arm not in ("checklist-verdict", "checklist-executable"):
        return
    out = call_oracle("qa_check", {})
    failing = _FAIL_LINE.findall(out if isinstance(out, str) else json.dumps(out))
    if failing:
        by_id = {c["check_id"]: c for c in D.raw_checks(sid, E.RULESET_V1) if c.get("auto")}
        specs = [by_id[c]["auto"] for c in res["applied"] + failing if c in by_id]
        _write_artifacts(sid, "strong", sandbox, specs)
        anchors = D.anchors(sid)
        claims = {
            "believes_meets_requirements": True,
            "checklist_self_assessment": {cid: True for cid in by_id},
            "qa_runs": 2,
            "key_figures": {k: (anchors.get(k) or {}).get("value", 0) for k in D.headline_figures(sid)},
            "rationale": "stub trial (medium draft repaired from the qa_check verdict)",
        }
        (Path(sandbox) / "claims.json").write_text(json.dumps(claims, indent=2) + "\n")
        call_oracle("qa_check", {})
