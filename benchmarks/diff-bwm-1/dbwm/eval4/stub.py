"""Deterministic stub agent for eval 4 (no LLM).

It exercises the whole pipeline -- sandbox, oracle round trips, deliverable
collection, scoring, judging (with a mock judge), reporting -- without a model.
It builds a SYNTHETIC ``market_model.xlsx`` with openpyxl (it reads nothing from
the sandbox and never touches the dataset or the grader files), so it also runs
in a bare temp dir in tests.

Behaviour per arm (all choices seeded from ``sha256(cell_id)``):

- every arm first writes a DRAFT carrying 2-4 deterministic defects, each of
  which fails exactly one K-check (UK spelling, unsourced FX, generic scenario
  labels, no markup factor, ...);
- ``baseline``: submits the draft and claims it meets the requirements;
- ``checklist-prose``: "self-reviews" without a tool, fixing only the defects
  at even positions, then claims every checklist item passes;
- ``checklist-verdict`` / ``checklist-executable``: run ``qa_check`` through
  ``call_oracle``, fix exactly the items reported FAIL, run it again, and
  self-assess from the last report.

The paper's stub copied a published frontier submission from the grader
directory into the sandbox; that is grader-private material, so v1 does not.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Callable

from .checker import CHECK_IDS

# defect name -> the K-check it breaks
DEFECTS = {
    "uk_spelling": "K-LANG",
    "unsourced_fx": "K-FX",
    "generic_scenarios": "K-SCEN",
    "no_markup": "K-CHAN",
    "no_dietary_driver": "K-DEMAND",
    "pasted_constants": "K-INTEG",
    "no_reconciliation": "K-RECON",
    "aurora_missing": "K-AURORA",
}
DEFECT_ORDER = tuple(DEFECTS)
FIX_FOR_CHECK = {v: k for k, v in DEFECTS.items()}

FAIL_LINE = re.compile(r"^\s+(PASS|FAIL)\s+(K-[A-Z]+)\b", re.M)


def _seed(cell_id: str) -> int:
    return int.from_bytes(hashlib.sha256(cell_id.encode()).digest()[:8], "big")


def draft_defects(cell_id: str) -> list[str]:
    """2-4 defects, deterministic in ``cell_id``."""
    s = _seed(cell_id)
    n = 2 + s % 3
    order = sorted(DEFECT_ORDER, key=lambda d: hashlib.sha256(f"{cell_id}:{d}".encode()).hexdigest())
    return sorted(order[:n], key=DEFECT_ORDER.index)


_CORE_TIMES = re.compile(r"(<dcterms:(?:created|modified) [^>]*>)[^<]*(</dcterms:(?:created|modified)>)")


def _postprocess(path: Path, cached: dict[int, dict[str, float]]) -> None:
    """Make the saved workbook byte-reproducible and (optionally) give formula
    cells a cached value, as Excel would on save.

    openpyxl stamps docProps/core.xml with the save time and the zip entries
    with the current time; both are pinned here. openpyxl writes
    ``<c r="B3"><f>...</f><v /></c>``; without a cached ``<v>`` the checker
    needs the optional ``formulas`` package to see computed outputs, and the v1
    ruleset refuses to score such a workbook when that package is absent, so by
    default the stub's workbook carries its formula results."""
    zin = zipfile.ZipFile(io.BytesIO(path.read_bytes()))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            m = re.fullmatch(r"xl/worksheets/sheet(\d+)\.xml", info.filename)
            if m and int(m.group(1)) in cached:
                xml = data.decode("utf-8")
                for coord, val in cached[int(m.group(1))].items():
                    pat = re.compile(r'(<c r="%s"[^>]*><f>[^<]*</f>)<v ?/>' % re.escape(coord))
                    xml, n = pat.subn(lambda mm, v=val: f"{mm.group(1)}<v>{v!r}</v>", xml)
                    if n != 1:
                        raise RuntimeError(f"stub workbook: no formula cell {coord} in {info.filename}")
                data = xml.encode("utf-8")
            if info.filename == "docProps/core.xml":
                data = _CORE_TIMES.sub(lambda mm: f"{mm.group(1)}2026-01-01T00:00:00Z{mm.group(2)}",
                                       data.decode("utf-8")).encode("utf-8")
            zi = zipfile.ZipInfo(info.filename, date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(zi, data)
    path.write_bytes(buf.getvalue())


def build_workbook(path: Path, defects: set[str] | frozenset[str] = frozenset(),
                   cached: bool = True) -> None:
    """Write a synthetic NZ egg-market workbook. With no defects it passes all
    12 K-checks; each defect fails exactly one check. ``cached=True`` stores
    formula results in the file (scorable without ``formulas``); with
    ``cached=False`` formula cells have no cached value, exactly as openpyxl
    writes them. Either way the bytes are reproducible."""
    try:
        import openpyxl
    except ImportError as e:  # pragma: no cover - eval4 extra missing
        raise RuntimeError("the eval-4 stub agent needs openpyxl (pip install 'diff-bwm-1[eval4]')") from e
    wb = openpyxl.Workbook()
    fixed = _dt.datetime(2026, 1, 1)
    wb.properties.created = fixed
    wb.properties.modified = fixed
    note = "Workbook colour key: blue inputs" if "uk_spelling" in defects else "Workbook color key: blue inputs"

    cover = wb.active
    cover.title = "Cover"
    cover["A1"] = "NZ egg market model (synthetic stub workbook)"
    cover["A2"] = note
    cover["A3"] = "Figures in NZD and USD"

    a = wb.create_sheet("Assumptions")
    a.append(["Input", "Value", "Unit", "Source"])
    src = "transcript_industry_expert_part3.pdf (Calthorpe), as of 2026-05"
    rows = [
        ("National layer flock", 3_780_000, "birds", src),
        ("Dietary shift per-capita uplift", 0.0045, "pa", "consumer_survey.pdf, as of 2026"),
        ("Retail shelf to wholesale markup factor", 1.43, "x", "retail_standards.pdf, as of 2025"),
        ("Own-price elasticity free-range", -1.34, "", "RetailIQ scan data, as of Q1 2026"),
        ("FY26 free-range premium over barn", 0.19, "", "RetailIQ scan data, as of Q1 2026"),
        ("Population growth", 0.011, "pa", "stats_release.pdf, as of 2025"),
        ("Eggs per capita", 229, "eggs", "consumer_survey.pdf, as of 2026"),
        ("Average wholesale price", 0.42, "NZD/egg", "retail_standards.pdf, as of 2025"),
        ("Pasture-raised share of free-range", 0.5, "", src),
        ("Commodity free-range share", 0.5, "", src),
    ]
    if "no_markup" in defects:
        rows[2] = ("Retail shelf to wholesale price step", 9.0, "x", "retail_standards.pdf, as of 2025")
    if "no_dietary_driver" in defects:
        rows[1] = ("Population drift", 0.0, "pa", "consumer_survey.pdf, as of 2026")
    for r in rows:
        a.append(list(r))
    if "unsourced_fx" in defects:
        a.append(["NZD to USD spot", 0.6, "", "spot rate, 2026"])
    else:
        a.append(["NZD to USD FY2026", 0.595, "",
                  "halberd_central_financial_assumptions.xlsx, as of FY2026"])

    p = wb.create_sheet("Producer_Roster")
    p.append(["Producer", "Metric", "Share", "Birds"])
    for name, share in (("Mainland Poultry", 0.27), ("Heyden Farms", 0.13), ("Better Eggs", 0.09),
                        ("Zeagold", 0.07), ("Henergy", 0.06)):
        p.append([name, "share", share])
    if "aurora_missing" not in defects:
        p.append(["Aurora Eggs (net-additive)", "birds", None, 625_000])

    c = wb.create_sheet("Channel_Pricing")
    c.append(["Channel", "Wholesale price", "Shelf price"])
    c.append(["Retail", 0.42, "=B2*Assumptions!B4"])
    values: dict[str, dict[str, float]] = {"Channel_Pricing": {"C2": 0.42 * rows[2][1]},
                                           "Demand_Forecast": {}, "Reconciliation": {}}
    c.append(["Foodservice", 0.38, None])
    c.append(["Industrial", 0.31, None])

    d = wb.create_sheet("Demand_Forecast")
    d.append(["Year", "Demand index", "Plant-based substitution downside"])
    d.append(["FY2026", 1.0, 0.99])
    pasted = "pasted_constants" in defects
    growth = rows[5][1]
    bi, ci = 1.0, 0.99
    for i in range(3, 33):
        if pasted:
            d.append([f"FY20{24 + i}", 1.0 + 0.01 * i, 0.98])
        else:
            d.append([f"FY20{24 + i}", f"=B{i - 1}*(1+Assumptions!$B$7)", f"=C{i - 1}*0.999"])
            bi, ci = bi * (1 + growth), ci * 0.999
            values["Demand_Forecast"][f"B{i}"] = bi
            values["Demand_Forecast"][f"C{i}"] = ci

    s = wb.create_sheet("Scenarios_Transition")
    names = (("Scenario A", "Scenario B", "Scenario C") if "generic_scenarios" in defects
             else ("Managed Conversion", "Capacity Gap", "Premium Collapse"))
    s["A1"], s["B1"], s["C1"] = names
    # mechanism notes sit outside the header band the scenario-name finder reads
    s["D10"] = "Barn substitution after premium compression"
    s["D11"] = "Free-range premium compression path"

    r = wb.create_sheet("Reconciliation")
    if "no_reconciliation" in defects:
        r.append(["Market size", "NZ$m"])
        r.append(["Market size estimate", 480])
    else:
        r.append(["Top-down vs bottom-up reconciliation", "NZ$m"])
        r.append(["Bottom-up market size NZ$m", 540])
        r.append(["Top-down market size NZ$m", 455])
        r.append(["Gap: structural channel coverage", "=B2-B3"])
        values["Reconciliation"]["B4"] = 540 - 455
    wb.save(path)
    idx = {ws.title: i for i, ws in enumerate(wb.worksheets, 1)}
    _postprocess(path, {idx[t]: v for t, v in values.items() if v} if cached else {})


def _report_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for k in ("result", "text", "report"):
            if isinstance(result.get(k), str):
                return result[k]
    return json.dumps(result, default=str)


def parse_qa_report(text: str) -> dict[str, bool]:
    """{'K-SCEN': True, ...} from a qa_check report (verdict or figures)."""
    return {m.group(2): m.group(1) == "PASS" for m in FAIL_LINE.finditer(text)}


def _claims(believes: bool, self_assessment: dict[str, bool] | None, qa_runs: int | None,
            defects: set[str], note: str) -> dict:
    c: dict[str, Any] = {"believes_meets_requirements": believes}
    if self_assessment is not None:
        c["checklist_self_assessment"] = self_assessment
        c["qa_runs"] = qa_runs
    c["key_figures"] = {"bottom_up_market_size_nzd_m": 480 if "no_reconciliation" in defects else 540,
                        "aurora_volume_share_pct": 14.2,
                        "fy26_free_range_premium_pct": 19}
    c["rationale"] = note
    return c


def stub_agent(cell, sandbox: Path, call_oracle: Callable[[str, dict], Any]) -> None:
    sandbox = Path(sandbox)
    wb = sandbox / "market_model.xlsx"
    defects = set(draft_defects(cell.cell_id))
    build_workbook(wb, defects)
    arm = cell.arm
    if arm == "baseline":
        claims = _claims(True, None, None, defects, "stub: draft submitted without review")
    elif arm == "checklist-prose":
        ordered = [d for d in DEFECT_ORDER if d in defects]
        remaining = {d for i, d in enumerate(ordered) if i % 2 == 1}
        build_workbook(wb, remaining)
        claims = _claims(True, {k: True for k in CHECK_IDS}, 0, remaining,
                         "stub: self-reviewed against CHECKLIST.md without a tool")
        defects = remaining
    elif arm in ("checklist-verdict", "checklist-executable"):
        runs = 0
        first = parse_qa_report(_report_text(call_oracle("qa_check", {})))
        runs += 1
        failed = {k for k, ok in first.items() if not ok}
        remaining = {d for d in defects if DEFECTS[d] not in failed}
        build_workbook(wb, remaining)
        last = parse_qa_report(_report_text(call_oracle("qa_check", {})))
        runs += 1
        sa = {k: bool(last.get(k, False)) for k in CHECK_IDS}
        claims = _claims(all(sa.values()), sa, runs, remaining,
                         "stub: iterated with qa_check until the reported failures were fixed")
        defects = remaining
    else:
        raise ValueError(f"unknown eval4 arm {arm!r}")
    (sandbox / "claims.json").write_text(json.dumps(claims, indent=2) + "\n")
