#!/usr/bin/env python3
"""Deterministic checklist over an AA-Briefcase-Lite w1_t2 `market_model.xlsx`.

This is eval 4's EXECUTABLE CHECKLIST — the "test suite for a spreadsheet".
It implements the machine-checkable core of the 12 published binary rubric
checks (W1-T2-A-01..09, C-01..03) as deterministic tests over the workbook:
source-anchored numeric ranges, required structural elements, and render
integrity. It is deliberately a HEURISTIC APPROXIMATION of the rubric (the
official grade is the dataset's LLM judge); its purpose in the experiment is
to be a *decidable, executable* rendering of the same criteria the prose arm
receives as text.

PORT NOTE (diff-bwm-1). The check registry, the corpus extraction and every
threshold below are byte-for-byte the paper harness's checker (snapshot
``scripts/bwm_briefcase/checker.py``); both rulesets use them unchanged so the
secondary (checker) measure stays comparable with the published runs. Changes
are confined to plumbing, none of which alters a result:

- the ``uv run`` self-bootstrap re-exec is gone (install the ``eval4`` extra,
  and the ``recalc`` extra for the ``formulas`` recalculation);
- the recalculation step now RECORDS what happened (``recalc_status``,
  ``uncached_formula_cells``) instead of failing silently, so a scorer can tell
  "formula outputs invisible because ``formulas`` is not installed" apart from
  a real miss (the v1 ruleset refuses to score in that case);
- ``logging.disable`` is restored after recalculation (the paper ran this in a
  subprocess; in-process it would have silenced the harness's logging);
- ``format_report`` reproduces the CLI's printed output as a string, so the
  harness-side ``qa_check`` oracle returns exactly what the paper's tool printed;
- recalculation never opens a second file: a workbook that refers to another
  workbook (``xl/externalLinks`` parts, ``'[book.xlsx]Sheet'!A1`` references,
  ``INDIRECT`` strings, defined names) is not recalculated
  (``recalc_status="rejected_external_links"``), and ``formulas`` runs through a
  model that refuses to load any book but the one under check. Without this an
  agent workbook could make the oracle or the scorer read another file
  (red-team finding RT-X).

This module is NEVER staged into an agent sandbox. In v1 the runnable arms
reach it only through the sealed ``qa_check`` oracle op.

Modes:
  figures  (default)  per-check PASS/FAIL + the measured values + notes
  verdict             per-check PASS/FAIL only (no figures)  [mechanism arm]
  prose               no workbook needed: print the checklist as prose text
                      (this exact text is what the checklist arms get)
  serialize           workbook -> judge input (values, formatting summary)

Usage:
  python -m dbwm.eval4.checker --workbook market_model.xlsx [--mode figures|verdict] [--json out.json]
  python -m dbwm.eval4.checker --mode prose
"""

import argparse
import json
import logging
import os
import re
import sys
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Source-anchored constants (from the dataset's own source pool)
# ---------------------------------------------------------------------------

# halberd_central_financial_assumptions.xlsx: NZD->USD, FY2022..FY2031
CENTRAL_NZD_RATES = (0.654, 0.625, 0.602, 0.585, 0.595, 0.61, 0.62, 0.625)

GENERIC_SCENARIO = re.compile(
    r"^\s*(scenario\s*[a-z0-9]|case\s*\d|(base|central|upside|downside|bull|bear|high|low|mid)"
    r"(\s*case)?)\s*$", re.I)

RECOMMENDATION_TOKENS = (
    "we recommend", "recommend invest", "recommend pass", "recommend proceeding",
    "continue diligence", "we should proceed", "compelling fit",
    "attractive opportunity", "strong platform fit", "thesis pillar",
)
UK_SPELLINGS = ("colour", "favourable", "capitalise", "normalise", "modelling",
                "prioritise")
# "analyse"/"analysed"/"analysing" are UK spellings, but a plain substring check
# on "analyse" would false-positive on the valid US noun plural "analyses"; a
# word-boundary regex on the verb forms avoids that without missing cases
# followed by punctuation (a trailing-space substring check would).
ANALYSE_RE = re.compile(r"\banalys(e|ed|ing)\b")

ERROR_VALUES = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NULL!", "#NUM!")
DEFAULT_TABS = re.compile(r"^sheet\d+$", re.I)

# recalc_status values (diff-bwm-1 addition; the paper recorded none of this)
RECALC_NOT_NEEDED = "not_needed"    # every formula cell carries a cached value
RECALC_OK = "ok"                    # `formulas` recomputed >= 1 numeric value
RECALC_EMPTY = "empty"              # `formulas` ran but produced no numeric value
RECALC_UNAVAILABLE = "unavailable"  # uncached formulas and `formulas` not importable
RECALC_FAILED = "failed"            # `formulas` raised on this workbook
RECALC_REJECTED_EXTERNAL = "rejected_external_links"  # refers to another workbook: never recalculated


# ---------------------------------------------------------------------------
# External-workbook guard (diff-bwm-1 addition, red-team finding RT-X)
# ---------------------------------------------------------------------------
#
# `formulas` resolves references to OTHER workbooks by opening them from disk:
# an xl/externalLinks part (`[1]Sheet!A1`), an inline `'../x/[book.xlsx]Sheet'!A1`
# or `'/abs/[book.xlsx]Sheet'!A1`, a defined name holding such a reference, and
# `INDIRECT("...")` with such a string (all verified with formulas 1.3.4). In the
# CONTAINER sidecar the package is mounted next to the sandbox, so an agent
# workbook could pull scorer-only figures into the checker's output (figures
# mode prints them; verdict mode leaks PASS bits), and the host scorer could be
# made to read files from the runs tree. Three layers: (1) a static scan
# rejects every workbook that names another workbook before `formulas` runs;
# (2) `formulas` runs through a model that refuses to load any book but the one
# under check (covers computed INDIRECT strings the scan cannot see); (3) a
# recalculation whose solution mentions any other book is discarded.

_STRING_LITERAL = re.compile(r'"(?:[^"]|"")*"')
_EXTERNAL_REL = re.compile(rb"externalLink", re.I)
_EXTERNAL_REFS_TAG = re.compile(rb"<(?:\w+:)?externalReferences?\b", re.I)
_MAX_SCANNED_PART = 8 * 1024 * 1024
_SOLUTION_BOOK = re.compile(r"^'([^'\[]*)\[([^\]]+)\]")


class ExternalBookRefused(RuntimeError):
    """Raised inside `formulas` when a formula asks for a second workbook."""


def _formula_text(value):
    """The formula text of an openpyxl cell value, or None (plain/array formulas)."""
    if isinstance(value, str):
        return value if value.startswith("=") else None
    text = getattr(value, "text", None)  # ArrayFormula / DataTableFormula
    return text if isinstance(text, str) else None


def _names_another_book(formula):
    """True if a formula's text (string literals removed) contains a workbook
    reference (`[`...`]`). Excel structured table references also use brackets;
    the paper's `formulas` recalculation cannot evaluate them either."""
    return "[" in _STRING_LITERAL.sub('""', formula)


def zip_external_links(path):
    """Package-level external-workbook markers in an .xlsx -> list of reasons.

    Looks for xl/externalLinks/ parts, an <externalReferences> element in
    xl/workbook.xml, and any relationship whose type names an external link.
    A file that is not a zip yields [] (openpyxl reports it on its own)."""
    reasons = []
    try:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                low = info.filename.lower()
                if low.startswith("xl/externallinks/"):
                    reasons.append(f"package part {info.filename}")
                    continue
                if low == "xl/workbook.xml" or low.endswith(".rels"):
                    with z.open(info) as fh:
                        data = fh.read(_MAX_SCANNED_PART)
                    if low == "xl/workbook.xml" and _EXTERNAL_REFS_TAG.search(data):
                        reasons.append("externalReferences element in xl/workbook.xml")
                    elif low.endswith(".rels") and _EXTERNAL_REL.search(data):
                        reasons.append(f"external-link relationship in {info.filename}")
    except (zipfile.BadZipFile, OSError, KeyError):
        return []
    return reasons


def formula_external_refs(formulas_wb):
    """Workbook references in cell formulas and defined names of an openpyxl
    workbook loaded with data_only=False -> list of reasons (capped)."""
    reasons = []
    for ws in formulas_wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                f = _formula_text(c.value)
                if f is not None and _names_another_book(f):
                    reasons.append(f"formula in {ws.title}!{c.coordinate}")
                    if len(reasons) >= 20:
                        return reasons
    name_sets = [getattr(formulas_wb, "defined_names", None)]
    name_sets += [getattr(ws, "defined_names", None) for ws in formulas_wb.worksheets]
    for names in name_sets:
        if names is None:
            continue
        items = names.values() if hasattr(names, "values") else getattr(names, "definedName", [])
        for dn in items:
            text = getattr(dn, "attr_text", None) or getattr(dn, "value", None) or ""
            if isinstance(text, str) and _names_another_book(text):
                reasons.append(f"defined name {getattr(dn, 'name', '?')}")
    return reasons


def _single_book_model(formulas_mod):
    """A `formulas.ExcelModel` that loads exactly one file: the first book it
    is given. Every later request for a different book raises inside
    `formulas`, whose `complete()` turns it into a #REF! cell (formulas 1.3.4
    calls `add_book` from `load()` and `complete()` only)."""

    class SingleBookModel(formulas_mod.ExcelModel):
        _dbwm_main = None

        def add_book(self, book=None, context=None, data_only=False):
            if isinstance(book, str):
                key = book.replace("\\", "/")
                if self._dbwm_main is None:
                    self._dbwm_main = key.rsplit("/", 1)[-1].upper()
                elif key.upper() != self._dbwm_main:
                    raise ExternalBookRefused("references to other workbooks are not evaluated")
            elif self._dbwm_main is not None:
                raise ExternalBookRefused("references to other workbooks are not evaluated")
            return super().add_book(book, context, data_only)

    return SingleBookModel()


def _foreign_books(solution_keys, main_name):
    """Books other than ``main_name`` named by `formulas` solution keys."""
    main = main_name.upper()
    out = set()
    for k in solution_keys:
        m = _SOLUTION_BOOK.match(str(k))
        if m and (m.group(1) or m.group(2).upper() != main):
            out.add(m.group(1) + m.group(2))
    return sorted(out)


# ---------------------------------------------------------------------------
# Workbook corpus extraction
# ---------------------------------------------------------------------------

class Corpus:
    """Flat, searchable view of the workbook: strings + numbers with context."""

    def __init__(self, path):
        import openpyxl
        self.values = openpyxl.load_workbook(path, data_only=True)   # cached results
        self.formulas = openpyxl.load_workbook(path, data_only=False)
        self.tabs = list(self.values.sheetnames)
        self.strings = []   # (sheet, coord, text)
        self.numbers = []   # (sheet, coord, value, context_text)
        self.errors = []    # (sheet, coord, error_value)
        self.formula_count = 0
        self.hardcode_count = 0
        self.recalculated = False
        self.uncached_formula_cells = 0
        self.recalc_status = RECALC_NOT_NEEDED
        self.recalc_error = None
        # References to other workbooks (RT-X); recalculation is refused if any.
        self.external_refs = zip_external_links(path) + formula_external_refs(self.formulas)
        self._solved = self._recalc_if_needed(path)
        self._extract()
        self.all_text = "\n".join(t for _, _, t in self.strings).lower()

    def _recalc_if_needed(self, path):
        """openpyxl-written workbooks carry NO cached formula results, so
        data_only=True sees None for every computed cell. When that happens,
        recompute the workbook with the `formulas` package so computed outputs
        (e.g. the bottom-up market size) are checkable. Returns
        {(SHEET_UPPER, COORD): value}."""
        uncached = 0
        for ws_f, ws_v in zip(self.formulas.worksheets, self.values.worksheets):
            for row in ws_f.iter_rows():
                for c in row:
                    if (isinstance(c.value, str) and c.value.startswith("=")
                            and ws_v.cell(c.row, c.column).value is None):
                        uncached += 1
        self.uncached_formula_cells = uncached
        if not uncached:
            return {}
        if self.external_refs:
            # Never let `formulas` open another file (RT-X): the computed
            # outputs stay invisible, exactly as for a failed recalculation.
            self.recalc_status = RECALC_REJECTED_EXTERNAL
            self.recalc_error = "workbook refers to other workbooks: " + "; ".join(self.external_refs[:5])
            return {}
        prev_disable = logging.root.manager.disable
        try:
            import warnings
            os.environ.setdefault("TQDM_DISABLE", "1")
            logging.disable(logging.CRITICAL)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    import formulas
                except ImportError as e:
                    self.recalc_status = RECALC_UNAVAILABLE
                    self.recalc_error = f"{type(e).__name__}: {e}"
                    return {}
                xl = _single_book_model(formulas).loads(str(path)).finish()
                sol = xl.calculate()
                foreign = _foreign_books(sol.keys(), Path(path).name)
                if foreign:
                    # Backstop for a `formulas` code path the guard missed: the
                    # values computed from another book are discarded.
                    self.recalc_status = RECALC_REJECTED_EXTERNAL
                    self.recalc_error = "recalculation touched other workbooks: " + "; ".join(foreign[:5])
                    return {}
        except Exception as e:  # noqa: BLE001 — recalc is best-effort
            self.recalc_status = RECALC_FAILED
            self.recalc_error = f"{type(e).__name__}: {str(e)[:200]}"
            return {}
        finally:
            logging.disable(prev_disable)
        solved = {}
        rex = re.compile(r"^'\[[^\]]+\]([^']+)'!([A-Z]+\d+)$", re.I)
        for k, v in sol.items():
            m = rex.match(k)
            if not m:
                continue
            try:
                val = v.value[0, 0] if hasattr(v, "value") else v
            except Exception:  # noqa: BLE001
                continue
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                solved[(m.group(1).upper(), m.group(2))] = float(val)
        self.recalculated = bool(solved)
        self.recalc_status = RECALC_OK if solved else RECALC_EMPTY
        return solved

    def _extract(self):
        for ws in self.formulas.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    v = c.value
                    if isinstance(v, str) and v.startswith("="):
                        self.formula_count += 1
                    elif isinstance(v, (int, float)) and not isinstance(v, bool):
                        self.hardcode_count += 1
        for ws in self.values.worksheets:
            # per-sheet grids for context lookups
            grid = {}
            for row in ws.iter_rows():
                for c in row:
                    v = c.value
                    if v is None:  # fall back to recalculated formula results
                        v = self._solved.get((ws.title.upper(), c.coordinate))
                        if v is None:
                            continue
                    grid[(c.row, c.column)] = v
                    if isinstance(c.value, str):
                        if c.value.strip() in ERROR_VALUES:
                            self.errors.append((ws.title, c.coordinate, c.value.strip()))
                        else:
                            self.strings.append((ws.title, c.coordinate, c.value))
            for (r, col), v in grid.items():
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    # numeric content inside strings ("18%") is handled in finders
                    continue
                ctx = [ws.title]
                # nearest 3 string cells to the left in the same row
                found = 0
                for cc in range(col - 1, 0, -1):
                    lv = grid.get((r, cc))
                    if isinstance(lv, str):
                        ctx.append(lv)
                        found += 1
                        if found >= 3:
                            break
                # nearest 2 string cells above in the same column (headers)
                found = 0
                for rr in range(r - 1, max(0, r - 12), -1):
                    hv = grid.get((rr, col))
                    if isinstance(hv, str):
                        ctx.append(hv)
                        found += 1
                        if found >= 2:
                            break
                self.numbers.append((ws.title, f"{ws.title}!{r},{col}", float(v),
                                     " | ".join(ctx).lower()))

    # -- finders ------------------------------------------------------------

    def has_token(self, *tokens):
        return any(t.lower() in self.all_text for t in tokens)

    def find_number(self, lo, hi, ctx_pattern=None, allow_pct_scale=True):
        """Numbers v with lo<=v<=hi (also v/100 if allow_pct_scale and the raw
        value looks like a percent written as 18 not 0.18), whose context
        matches ctx_pattern (regex, case-insensitive) if given."""
        rex = re.compile(ctx_pattern, re.I) if ctx_pattern else None
        hits = []
        for sheet, coord, v, ctx in self.numbers:
            cands = [v]
            if allow_pct_scale and 1 < abs(v) <= 100:
                cands.append(v / 100.0)
            if not any(lo - 1e-9 <= c <= hi + 1e-9 for c in cands):
                continue
            if rex and not rex.search(ctx):
                continue
            hits.append({"where": coord, "value": v, "context": ctx[:160]})
        # percent-like strings ("18%") near-match
        if rex or True:
            srex = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")
            for sheet, coord, text in self.strings:
                for m in srex.finditer(text):
                    c = float(m.group(1)) / 100.0
                    if lo - 1e-9 <= c <= hi + 1e-9:
                        if rex and not rex.search((sheet + " " + text).lower()):
                            continue
                        hits.append({"where": f"{sheet}!{coord}", "value": text.strip()[:60],
                                     "context": text[:160].lower()})
        return hits

    def find_exact(self, targets, tol, ctx_pattern=None):
        rex = re.compile(ctx_pattern, re.I) if ctx_pattern else None
        hits = []
        for sheet, coord, v, ctx in self.numbers:
            if any(abs(v - t) <= tol for t in targets):
                if rex and not rex.search(ctx):
                    continue
                hits.append({"where": coord, "value": v, "context": ctx[:160]})
        return hits

    def scenario_names(self):
        """Candidate scenario labels: strings on scenario/transition-named tabs
        sitting in header positions (first 6 rows or first 2 columns)."""
        names = {}
        for sheet, coord, text in self.strings:
            if not re.search(r"scenario|transition", sheet, re.I):
                continue
            m = re.match(r"([A-Z]+)(\d+)$", coord)
            col_letters, row = m.group(1), int(m.group(2))
            t = text.strip()
            if not (2 < len(t) < 46) or t.startswith("="):
                continue
            headerish = row <= 6 or len(col_letters) == 1 and col_letters in "AB"
            if not headerish:
                continue
            # exclude obvious non-names
            if re.search(r"fy\s?20\d\d|^\d|total|source|assumption|note|%|nz\$|\busd\b",
                         t, re.I):
                continue
            if re.search(r"scenario|conversion|gap|collapse|slip|glut|transition|orderly|"
                         r"managed|premium|capacity|delay|ban|deadline", t, re.I):
                names[t] = not bool(GENERIC_SCENARIO.match(t))
        return names  # {label: substantive?}


# ---------------------------------------------------------------------------
# The checklist registry.  Each check: id, rubric_id, title, criteria (the
# prose the checklist arms receive, thresholds included), fn(corpus)
# -> (passed, measured, note).
# ---------------------------------------------------------------------------

def k_scenarios(c):
    names = c.scenario_names()
    substantive = [n for n, ok in names.items() if ok]
    passed = len(substantive) >= 3
    return passed, {"scenario_labels_found": sorted(names),
                    "substantive": sorted(substantive)}, \
        "needs >=3 substantively named scenarios on a scenarios/transition tab"


def k_aurora(c):
    birds = c.find_number(600_000, 650_000, r"aurora")
    share = c.find_number(0.17, 0.19, r"aurora")
    passed = bool(birds or share)
    return passed, {"aurora_birds_600k_650k": birds[:3], "aurora_share_17_19pct": share[:3]}, \
        "Aurora sized ~600-650k birds or 17-19% volume share, net-additive"


def k_producers(c):
    ranges = {"mainland": (0.25, 0.29), "heyden": (0.11, 0.15),
              "better eggs": (0.08, 0.10), "zeagold": (0.06, 0.08),
              "henergy": (0.05, 0.07)}
    found = {}
    for name, (lo, hi) in ranges.items():
        found[name] = bool(c.find_number(lo, hi, name.replace(" ", r"\s*")))
    flock = (c.find_number(3_700_000, 3_850_000, r"flock|hen|bird|layer")
             or c.find_number(3.7, 3.85, r"flock|hen|bird|layer"))
    n_ok = sum(found.values())
    passed = n_ok >= 4 and bool(flock)
    return passed, {"producer_share_in_range": found, "n_in_range": n_ok,
                    "flock_total_3p7_3p85m": flock[:2]}, \
        "producer shares within Calthorpe central ranges; national flock 3.7-3.85M"


def k_fx(c):
    usd = c.has_token("usd", "us$")
    rate = c.find_exact(CENTRAL_NZD_RATES, 0.0005)
    cites = c.has_token("central_financial_assumptions", "central financial assumptions")
    passed = usd and bool(rate) and cites
    return passed, {"usd_presented": usd,
                    "central_workbook_nzd_rate_found": rate[:3],
                    "central_workbook_cited": cites}, \
        "figures in USD using the FY-aligned NZD rates from the central assumptions workbook, cited"


def k_demand(c):
    diet = c.find_number(0.003, 0.006, r"diet|flexit|vegetar|per[\s-]?capita|drift")
    plant = c.has_token("plant-based", "plant based", "substitution")
    passed = bool(diet) and plant
    return passed, {"dietary_uplift_0p3_0p6pct": diet[:3], "plant_based_sensitivity": plant}, \
        "dietary-mix uplift ~0.3-0.6% pa as an explicit driver; plant-based substitution as downside sensitivity"


def k_channels(c):
    chans = [t for t in ("retail", "foodservice", "industrial") if c.has_token(t)]
    two_col = c.has_token("wholesale") and c.has_token("shelf")
    markup = c.find_number(1.33, 1.54, r"markup|mark-up|factor|shelf|wholesale|uplift")
    margin = c.find_number(0.25, 0.35, r"margin|markup|uplift|retail")
    passed = len(chans) == 3 and two_col and bool(markup or margin)
    return passed, {"channels_present": chans, "wholesale_and_shelf_columns": two_col,
                    "markup_1p33_1p54": markup[:2], "gross_margin_25_35pct": margin[:2]}, \
        "3 channel price levels; retail as wholesale+shelf; standards markup 1.33-1.54 (or 25-35% GM)"


def k_elasticity(c):
    own = c.find_exact((-1.34,), 0.06)
    cross = c.find_number(0.62, 0.71, r"elast|cross|substitut|barn")
    token = c.has_token("elastic")
    mech = c.has_token("substitution", "switch", "trade down", "trade-down")
    passed = (bool(own) or bool(cross) or token) and mech
    return passed, {"own_price_minus1p34": own[:2], "cross_0p62_0p71": cross[:2],
                    "elasticity_referenced": token, "substitution_mechanism": mech}, \
        "RetailIQ elasticities referenced; free-range->barn substitution mechanic in a compression scenario"


def k_integrity(c):
    default_tabs = [t for t in c.tabs if DEFAULT_TABS.match(t)]
    ratio = c.formula_count / max(1, c.formula_count + c.hardcode_count)
    has_assumptions = any(re.search(r"assumption|input", t, re.I) for t in c.tabs)
    passed = (not c.errors and not default_tabs and has_assumptions
              and c.formula_count >= 50 and ratio >= 0.25)
    return passed, {"error_cells": c.errors[:5], "default_named_tabs": default_tabs,
                    "tabs": c.tabs, "formula_cells": c.formula_count,
                    "hardcoded_numeric_cells": c.hardcode_count,
                    "formula_ratio": round(ratio, 3),
                    "assumptions_tab": has_assumptions}, \
        "no error cells; descriptive tab names incl. an Assumptions tab; calculations formula-driven (not pasted values)"


def k_traceability(c):
    src_cites = sum(1 for _, _, t in c.strings
                    if re.search(r"\.(pdf|xlsx|docx|eml|html)\b", t, re.I)
                    or re.search(r"calthorpe|retailiq|scan.?data|industry brief|memorandum",
                                 t, re.I))
    vintage = sum(1 for _, _, t in c.strings
                  if re.search(r"\b(20[12]\d|fy2\d|q[1-4]\s*20[12]\d|vintage|as at|as of)\b",
                               t, re.I))
    passed = src_cites >= 8 and vintage >= 8
    return passed, {"source_attribution_cells": src_cites, "date_vintage_cells": vintage}, \
        "every key assumption carries a source-file attribution and a date/vintage annotation"


def k_language(c):
    recs = sorted({tok for tok in RECOMMENDATION_TOKENS if tok in c.all_text})
    uk = {tok for tok in UK_SPELLINGS if tok in c.all_text}
    if ANALYSE_RE.search(c.all_text):
        uk.add("analyse")
    uk = sorted(uk)
    passed = not recs and not uk
    return passed, {"recommendation_language": recs, "uk_spellings": uk}, \
        "no invest/pass/continue-diligence recommendation language; US English spelling"


def k_recon(c):
    td = c.has_token("top-down", "top down")
    bu = c.has_token("bottom-up", "bottom up")
    recon = c.has_token("reconcil")
    nzd_m = c.find_number(513, 567, r"bottom|market|total|sizing|revenue|nz\$|nzd|\bm\b|million")
    nzd_full = c.find_number(513e6, 567e6)
    usd_m = c.find_number(300, 355, r"usd|us\$")
    anchor = nzd_m or nzd_full or usd_m
    passed = td and bu and recon and bool(anchor)
    return passed, {"top_down_present": td, "bottom_up_present": bu,
                    "reconciliation_present": recon,
                    "bottom_up_anchor_nz513_567m": (nzd_m + nzd_full)[:3],
                    "usd_equivalent_300_355m": usd_m[:2]}, \
        "explicit top-down vs bottom-up reconciliation, both retained; bottom-up lands NZ$513-567M"


def k_premium(c):
    split = (c.has_token("pasture") and c.has_token("commodity"))
    fy26 = c.find_number(0.17, 0.23, r"premium|free.?range|barn")
    compress = c.has_token("compress", "compression", "erod", "narrow")
    passed = split and bool(fy26) and compress
    return passed, {"premium_vs_commodity_split": split,
                    "fy26_premium_17_23pct": fy26[:3],
                    "compression_modelled": compress}, \
        "free-range split premium(pasture)/commodity with distinct prices; FY26 commodity premium 17-23%; forward compression"


CHECKS = [
    dict(id="K-SCEN", rubric="W1-T2-A-01/A-09", title="Named transition scenarios", fn=k_scenarios,
         criteria="At least three cage-ban transition scenarios with substantive descriptive "
                  "names (e.g. 'Managed Conversion' / 'Capacity Gap' / 'Premium Collapse' — "
                  "not 'Scenario A/B/C' or 'Base/Upside/Downside'), on a scenarios/transition "
                  "tab, spanning success through material slippage, with pricing varying "
                  "across scenarios."),
    dict(id="K-AURORA", rubric="W1-T2-A-02", title="Aurora sizing", fn=k_aurora,
         criteria="Aurora Eggs sized at ~600-650k birds (or 17-19% of national volume) and "
                  "added NET-ADDITIVE to the public producer roster (do not shrink other "
                  "producers to fit Aurora in)."),
    dict(id="K-PROD", rubric="W1-T2-C-01", title="Producer roster vs expert series", fn=k_producers,
         criteria="Bottom-up producer shares within the Calthorpe expert central ranges "
                  "(Mainland 25-29%, Heyden 11-15%, Better Eggs 8-10%, Zeagold 6-8%, "
                  "Henergy 5-7%) and national flock 3.7-3.85M birds. Do not anchor on the "
                  "retired consultant's (Whitlock) commentary."),
    dict(id="K-FX", rubric="W1-T2-A-03", title="USD presentation & FX sourcing", fn=k_fx,
         criteria="Financial figures presented in USD (alone or alongside NZD), converted "
                  "with the FY-aligned NZD rates from halberd_central_financial_assumptions"
                  ".xlsx (FY2025 0.585, FY2026 0.595, FY2027 0.61, FY2028 0.62 ...), with "
                  "the workbook cited — no flat unsourced spot rate."),
    dict(id="K-DEMAND", rubric="W1-T2-A-04", title="Demand drivers", fn=k_demand,
         criteria="Dietary-mix (flexitarian/vegetarian) shift as an explicit per-capita "
                  "driver at ~0.3-0.6% pa uplift, and plant-based substitution surfaced as "
                  "a downside sensitivity to forward egg demand."),
    dict(id="K-CHAN", rubric="W1-T2-A-05", title="Channel pricing discipline", fn=k_channels,
         criteria="At least three distinct channel price levels (retail wholesale, "
                  "foodservice, industrial), retail as TWO columns (wholesale gate + shelf), "
                  "using the standards convention: shelf->wholesale via markup factor "
                  "1.33-1.54 (equivalently 25-35% gross margin), not a flat % subtraction."),
    dict(id="K-ELAST", rubric="W1-T2-A-06", title="Elasticity & substitution", fn=k_elasticity,
         criteria="RetailIQ scan-data elasticity referenced (own-price free-range commodity "
                  "~-1.34 or cross-elasticity barn<->free-range 0.62-0.71) and at least one "
                  "premium-compression scenario implements free-range->barn substitution "
                  "mechanically (tier mix not flat)."),
    dict(id="K-INTEG", rubric="W1-T2-A-07", title="Workbook integrity", fn=k_integrity,
         criteria="No error values (#REF!, #DIV/0!, ...), no default Sheet1/2/3 tab names, "
                  "an Assumptions tab, and calculations formula-driven so the model stays "
                  "refreshable (not pasted constants)."),
    dict(id="K-TRACE", rubric="W1-T2-A-07", title="Assumption traceability", fn=k_traceability,
         criteria="Key inputs on the Assumptions tab each carry a source-file attribution "
                  "and a date/vintage annotation."),
    dict(id="K-LANG", rubric="W1-T2-A-08", title="House language rules", fn=k_language,
         criteria="No invest/pass/continue-diligence recommendation language or thesis-pillar "
                  "framing anywhere in the workbook; US English spelling throughout."),
    dict(id="K-RECON", rubric="W1-T2-C-02", title="Top-down vs bottom-up reconciliation", fn=k_recon,
         criteria="Explicit top-down vs bottom-up market-size reconciliation retaining BOTH "
                  "figures, gap explained as structural channel coverage, bottom-up landing "
                  "NZ$513-567M (~USD 300-355M at central rates)."),
    dict(id="K-PREM", rubric="W1-T2-C-03", title="Free-range premium structure", fn=k_premium,
         criteria="Free-range split into premium (pasture-raised) and commodity sub-segments "
                  "with different prices; Aurora's free-range treated as commodity tier; FY26 "
                  "commodity free-range premium over barn 17-23% with forward compression "
                  "(not the broker's 30%+ bull case)."),
]

CHECK_IDS = [chk["id"] for chk in CHECKS]


def rubric_ids(chk):
    """'W1-T2-A-01/A-09' -> ['W1-T2-A-01', 'W1-T2-A-09'] (K-check -> rubric items)."""
    first, *rest = chk["rubric"].split("/")
    prefix = first.rsplit("-", 2)[0]  # 'W1-T2'
    return [first] + [f"{prefix}-{r}" for r in rest]


# K-check -> the rubric item(s) it approximates. K-SCEN covers A-01 and A-09;
# K-INTEG and K-TRACE both cover A-07; everything else is 1:1.
K_TO_RUBRIC = {chk["id"]: rubric_ids(chk) for chk in CHECKS}


def evaluate(path):
    """Run the registry; return (result_dict, corpus). ``result_dict`` is
    exactly the paper's ``run_checks`` output."""
    c = Corpus(path)
    results = []
    for chk in CHECKS:
        try:
            passed, measured, note = chk["fn"](c)
        except Exception as e:  # noqa: BLE001
            passed, measured, note = False, {"checker_error": str(e)}, "checker raised"
        results.append({"id": chk["id"], "rubric": chk["rubric"], "title": chk["title"],
                        "pass": bool(passed), "measured": measured, "note": note})
    res = {
        "workbook": str(path),
        "checks": results,
        "summary": {"passed": sum(r["pass"] for r in results),
                    "total": len(results),
                    "failed_ids": [r["id"] for r in results if not r["pass"]]},
        "consistency": {
            "tabs": c.tabs,
            "formula_cells": c.formula_count,
            "hardcoded_numeric_cells": c.hardcode_count,
            "formula_ratio": round(c.formula_count /
                                   max(1, c.formula_count + c.hardcode_count), 3),
            "error_cells": len(c.errors),
        },
    }
    return res, c


def run_checks(path):
    return evaluate(path)[0]


def recalc_info(corpus):
    """What the recalculation step did (diff-bwm-1 addition)."""
    return {"recalc_status": corpus.recalc_status,
            "uncached_formula_cells": corpus.uncached_formula_cells,
            "recalculated": corpus.recalculated,
            "recalc_error": corpus.recalc_error}


# recalc_smoke() statuses (diff-bwm-1 addition)
SMOKE_OK = "ok"            # `formulas` recalculated a 2-cell workbook correctly
SMOKE_MISSING = "missing"  # `formulas` is not importable (optional extra absent)
SMOKE_BROKEN = "broken"    # importable, but recalculation raised or was wrong


def recalc_smoke():
    """Can ``formulas`` actually recalculate a workbook here? -> (status, error).

    Recalculates a two-cell workbook (A1 = 2, A2 = ``=SUM(A1,A1)*3``) and
    checks A2 == 12. Importing ``formulas`` is not enough: its Excel function
    table imports scipy, and where scipy cannot load (a dlopen failure was seen
    in one of the build venvs) every formula that names a function fails while
    pure arithmetic still works, so the smoke uses a function on purpose.
    ``status`` is SMOKE_OK, SMOKE_MISSING (``error`` = the ImportError) or
    SMOKE_BROKEN (``error`` = what failed). Not cached: tests monkeypatch the
    import, and after the first call the cost is a few milliseconds
    (diff-bwm-1 addition, review finding M4).
    """
    import tempfile
    import warnings

    prev = logging.root.manager.disable
    os.environ.setdefault("TQDM_DISABLE", "1")
    logging.disable(logging.CRITICAL)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                import formulas
            except ImportError as e:
                return SMOKE_MISSING, f"{type(e).__name__}: {e}"
            try:
                import openpyxl
                from formulas.functions import get_functions

                get_functions()
                with tempfile.TemporaryDirectory(prefix="dbwm_eval4_smoke_") as td:
                    path = Path(td) / "smoke.xlsx"
                    wb = openpyxl.Workbook()
                    ws = wb.active
                    ws.title = "S"
                    ws["A1"] = 2
                    ws["A2"] = "=SUM(A1,A1)*3"
                    wb.save(path)
                    sol = formulas.ExcelModel().loads(str(path)).finish().calculate()
                got = None
                for k, v in sol.items():
                    if str(k).upper().endswith("S'!A2"):
                        got = v.value[0, 0] if hasattr(v, "value") else v
                if got != 12:
                    return SMOKE_BROKEN, f"recalculated =SUM(A1,A1)*3 with A1=2 to {got!r}, expected 12"
            except Exception as e:  # noqa: BLE001 - any failure means recalculation is unusable here
                return SMOKE_BROKEN, f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        logging.disable(prev)
    return SMOKE_OK, None


def formulas_available():
    """Whether the optional ``formulas`` recalculation package is usable here.

    True only if ``recalc_smoke()`` passes. An install that imports but cannot
    recalculate (e.g. its scipy dependency fails to load) is NOT usable: every
    recalculation would fail and the workbook would be scored on missing
    values. Callers that need to say why use ``recalc_smoke()`` directly
    (diff-bwm-1 addition; core maintainer and review finding M4, 2026-10-02).
    """
    return recalc_smoke()[0] == SMOKE_OK


def recalc_unusable_reason():
    """None if recalculation works here, else a one-line, actionable reason."""
    status, err = recalc_smoke()
    if status == SMOKE_OK:
        return None
    if status == SMOKE_MISSING:
        return ("the optional 'formulas' package is not installed "
                f"(pip install 'diff-bwm-1[recalc]'; {err})")
    return ("'formulas' is installed but cannot recalculate in this environment "
            f"({err}); fix the environment (scipy must import), e.g. reinstall scipy")


def uncached_formula_cells(path):
    """Formula cells without a cached value (what would need recalculation).
    Cheap: two openpyxl loads, no recalculation (diff-bwm-1 addition)."""
    import openpyxl
    values = openpyxl.load_workbook(path, data_only=True)
    forms = openpyxl.load_workbook(path, data_only=False)
    n = 0
    for ws_f, ws_v in zip(forms.worksheets, values.worksheets, strict=False):
        for row in ws_f.iter_rows():
            for c in row:
                if (isinstance(c.value, str) and c.value.startswith("=")
                        and ws_v.cell(c.row, c.column).value is None):
                    n += 1
    return n


def format_report(res, mode):
    """The exact text the paper checker's CLI printed for ``res`` in ``mode``
    ('figures' or 'verdict'), including the trailing newline of the last print."""
    s = res["summary"]
    lines = [f"CHECKLIST RESULT: {s['passed']}/{s['total']} PASS"]
    for r in res["checks"]:
        lines.append(f"  {'PASS' if r['pass'] else 'FAIL'}  {r['id']:9s} {r['title']}")
        if mode == "figures":
            lines.append(f"        criteria: {r['note']}")
            for k, v in r["measured"].items():
                lines.append(f"        {k}: {json.dumps(v, default=str)[:220]}")
    if mode == "figures":
        lines.append(f"  consistency: {json.dumps(res['consistency'])[:300]}")
    return "\n".join(lines) + "\n"


def serialize(path):
    """Corpus -> JSON-able cell map for the LLM judge (judge.py)."""
    from openpyxl.utils import get_column_letter
    c = Corpus(path)
    cells = {}
    for s, co, t in c.strings:
        cells.setdefault(s, {})[co] = t
    for _, co, v, _ctx in c.numbers:
        sheet, rc = co.split("!")
        r, col = rc.split(",")
        cells.setdefault(sheet, {})[get_column_letter(int(col)) + r] = v
    # formatting summary — the text dump alone makes visual-formatting rubric
    # items (fill categories, bold headers, number formats) unjudgeable
    def _rgb(color):
        v = getattr(color, "rgb", None) if color else None
        return v if isinstance(v, str) and re.fullmatch(
            r"[0-9A-Fa-f]{6,8}", v) else None
    styles = {}
    for ws in c.formulas.worksheets:
        combos = {}
        numfmts = {}
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is None:
                    continue
                fill = (_rgb(cell.fill.start_color)
                        if cell.fill and cell.fill.fill_type == "solid" else None)
                key = (fill, bool(cell.font and cell.font.bold),
                       _rgb(cell.font.color if cell.font else None))
                d = combos.setdefault(key, {"count": 0, "example": cell.coordinate})
                d["count"] += 1
                nf = cell.number_format
                if nf and nf != "General":
                    numfmts[nf] = numfmts.get(nf, 0) + 1
        styles[ws.title] = {
            "distinct_style_combos": [
                {"fill_rgb": k[0], "bold": k[1], "font_rgb": k[2], **v}
                for k, v in sorted(combos.items(), key=lambda x: -x[1]["count"])[:12]],
            "number_formats": dict(sorted(numfmts.items(), key=lambda x: -x[1])[:10]),
        }
    return {"tabs": c.tabs, "cells": cells, "recalculated": c.recalculated,
            "formula_cells": c.formula_count, "styles": styles}


def prose_checklist():
    lines = [
        "QA CHECKLIST for market_model.xlsx (Halberd deal-team review standard).",
        "The reviewed workbook is expected to satisfy EVERY item:", ""]
    for i, chk in enumerate(CHECKS, 1):
        lines.append(f"{i:2d}. [{chk['title']}] {chk['criteria']}")
    return "\n".join(lines) + "\n"


def checklist_md_bytes():
    """CHECKLIST.md exactly as the paper staged it: the stdout of
    ``checker.py --mode prose``, i.e. ``print(prose_checklist())`` -- the
    checklist text plus print()'s own trailing newline."""
    return (prose_checklist() + "\n").encode("utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workbook")
    ap.add_argument("--mode", default="figures",
                    choices=["figures", "verdict", "prose", "serialize"])
    ap.add_argument("--json", help="also write full JSON results here")
    args = ap.parse_args(argv)

    if args.mode == "prose":
        print(prose_checklist())
        return 0
    if not args.workbook or not Path(args.workbook).exists():
        print("error: --workbook missing or not found", file=sys.stderr)
        return 2
    if args.mode == "serialize":
        data = serialize(args.workbook)
        if args.json:
            Path(args.json).write_text(json.dumps(data, default=str) + "\n")
        else:
            print(json.dumps(data, default=str))
        return 0
    res = run_checks(args.workbook)
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=2) + "\n")
    sys.stdout.write(format_report(res, args.mode))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
