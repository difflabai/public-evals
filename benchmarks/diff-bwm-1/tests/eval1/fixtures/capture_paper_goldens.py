"""Capture eval-1 golden outputs from the paper's own harness code (provenance for paper_goldens.json).

Run against an UNMODIFIED copy of the frozen paper harness laid out as it was
in its repository (``<root>/examples/scripts/bwm_theory/*.py`` with the data
under ``<root>/examples/data/{bwm-scenario-matrix,bwm-scenario-matrix-calibration,
bwm-theory-tasks}``). Nothing from dbwm is imported: the outputs are what the
paper code printed, so tests can hold the port to them.

    python capture_paper_goldens.py --harness <root>/examples/scripts/bwm_theory

Captured (inputs from paper_goldens_inputs.json):
  score_cases   oracle.score_plan for 5 companies x 3 tiers x every named plan x
                every named claims object (condition grounded-iterate, rep 1):
                sha256 of ``json.dumps(verdict, indent=2)`` (what planner.py
                wrote, minus the trailing newline), or the exception the paper
                scorer raised (planner.py then wrote no verdict).
  tool_cases    bwm_query.py main(), local backend, for 5 companies x 3 tiers x
                every named plan x 4 modes: sha256 of stdout and the return
                code; an uncaught exception is recorded by type.
  prompts       planner.build_prompt for the 15 published briefs x 6 conditions
                (backend local), with the harness directory replaced by <HARNESS>.
  full_*        complete texts for a northwind-hostile subset (diff-friendly).
  mode_override_escape  BWM_QUERY_MODE=broken in the environment, ``--mode numeric``
                on the command line: the paper tool served numeric (ledger E1-D3).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import platform
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPANIES = ("atlas-advisory", "gigbridge-marketplace", "northwind-analytics", "sprout-crm", "voltcore-devices")
TIERS = ("soft", "hard", "hostile")
MODES = ("numeric", "prose", "raw", "broken")
CONDITIONS = ("ungrounded", "grounded-readonly", "grounded-iterate", "prose-feedback", "raw-figures",
              "broken-model")
FULL_COMPANY, FULL_TIER = "northwind-analytics", "hostile"
FULL_PLANS = ("base", "careful", "naive", "bad_value", "nonlever_zero_costs", "wrong_length", "spike_q5")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def capture(harness: Path) -> dict:
    harness = harness.resolve()
    sys.path.insert(0, str(harness))
    import bwm_query  # noqa: E402  (the paper's tool)
    import oracle  # noqa: E402  (the paper's scorer; sets prec 50 on import)
    import planner  # noqa: E402  (the paper's prompt builder)

    inputs = json.loads((HERE / "paper_goldens_inputs.json").read_text(encoding="utf-8"))
    plans, claims = inputs["plans"], inputs["claims"]
    out = {"schema": "dbwm.eval1.paper-goldens/2",
           "provenance": (f"captured by capture_paper_goldens.py from an unmodified copy of the frozen "
                          f"paper harness (oracle.py, bwm_query.py local backend, planner.build_prompt) "
                          f"under CPython {platform.python_version()}; harness directory replaced by "
                          f"<HARNESS> in prompts"),
           "score_condition": "grounded-iterate", "score_rep": 1,
           "score_cases": [], "tool_cases": [], "prompts": [],
           "full_verdicts": {}, "full_tool_stdout": {}, "full_prompts": {}}

    for company in COMPANIES:
        model, tiers_doc, _ = oracle.load_model_and_tiers(company)
        for tier in TIERS:
            for pname in sorted(plans[company]):
                for cname in sorted(claims):
                    rec = {"company": company, "tier": tier, "plan": pname, "claims": cname}
                    try:
                        v = oracle.score_plan(model, tiers_doc, tier, plans[company][pname],
                                              claims[cname], condition="grounded-iterate", rep=1)
                        text = json.dumps(v, indent=2)
                        rec["sha256"] = _sha(text)
                        if company == FULL_COMPANY and tier == FULL_TIER and pname in FULL_PLANS \
                                and cname in ("honest_true", "strings_false"):
                            out["full_verdicts"][f"{company}|{tier}|{pname}|{cname}"] = text
                    except Exception as e:  # noqa: BLE001 - the paper pipeline wrote no verdict
                        rec["error"] = type(e).__name__
                    out["score_cases"].append(rec)

    with tempfile.TemporaryDirectory() as td:
        for company in COMPANIES:
            for tier in TIERS:
                for pname in sorted(plans[company]):
                    pf = Path(td) / "candidate.json"
                    pf.write_text(json.dumps(plans[company][pname]), encoding="utf-8")
                    for mode in MODES:
                        rec = {"company": company, "tier": tier, "plan": pname, "mode": mode}
                        buf = io.StringIO()
                        argv = sys.argv
                        sys.argv = ["bwm_query.py", "--plan", str(pf), "--model", company, "--tier", tier,
                                    "--mode", mode, "--backend", "local"]
                        try:
                            with contextlib.redirect_stdout(buf):
                                rc = bwm_query.main()
                            rec["rc"] = rc
                        except Exception as e:  # noqa: BLE001 - the CLI died with a traceback
                            rec["rc"] = 1
                            rec["error"] = type(e).__name__
                        finally:
                            sys.argv = argv
                        rec["sha256"] = _sha(buf.getvalue())
                        out["tool_cases"].append(rec)
                        if company == FULL_COMPANY and tier == FULL_TIER and pname in ("careful", "naive"):
                            out["full_tool_stdout"][f"{company}|{tier}|{pname}|{mode}"] = buf.getvalue()
        # Mode override: env says broken, the command line says numeric.
        pf = Path(td) / "candidate.json"
        pf.write_text(json.dumps(plans[FULL_COMPANY]["naive"]), encoding="utf-8")
        env_keep = {k: os.environ.get(k) for k in ("BWM_QUERY_MODE", "BWM_MODEL_ID", "BWM_TIER")}
        os.environ.update({"BWM_QUERY_MODE": "broken", "BWM_MODEL_ID": FULL_COMPANY, "BWM_TIER": FULL_TIER})
        buf = io.StringIO()
        argv = sys.argv
        sys.argv = ["bwm_query.py", "--plan", str(pf), "--mode", "numeric"]
        try:
            with contextlib.redirect_stdout(buf):
                bwm_query.main()
        finally:
            sys.argv = argv
            for k, v in env_keep.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        out["mode_override_escape"] = {"env_mode": "broken", "cli_mode": "numeric", "stdout": buf.getvalue()}

    tasks = harness.parents[1] / "data" / "bwm-theory-tasks"
    for company in COMPANIES:
        for tier in TIERS:
            brief = json.loads((tasks / f"{company}__{tier}.json").read_text(encoding="utf-8"))
            for cond in CONDITIONS:
                text = planner.build_prompt(brief, cond, Path(td), "local").replace(str(harness), "<HARNESS>")
                out["prompts"].append({"instance": f"{company}__{tier}", "condition": cond, "sha256": _sha(text)})
                if company == FULL_COMPANY and tier == FULL_TIER and cond in ("ungrounded", "grounded-readonly"):
                    out["full_prompts"][cond] = text
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--harness", required=True, help="the paper's examples/scripts/bwm_theory directory")
    ap.add_argument("--out", default=str(HERE / "paper_goldens.json"))
    args = ap.parse_args(argv)
    res = capture(Path(args.harness))
    Path(args.out).write_text(json.dumps(res, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: {len(res['score_cases'])} score cases, {len(res['tool_cases'])} tool cases, "
          f"{len(res['prompts'])} prompts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
