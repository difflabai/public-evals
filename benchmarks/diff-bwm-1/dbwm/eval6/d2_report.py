"""Render a saved D2 offline report (from d2_signals run) without recomputing V*.

Ported from the paper harness (bwm_latent/d2_report.py): reprints the per-signal
Kendall tau vs V* table and the paired contrasts. Changes: package imports;
``render`` returns the text (the CLI prints it); the judge clause points at
``dbwm eval6-d2 judge``; and the readout is worded neutrally (v1). The paper's
readout ("ledger beats MECHANICAL_NO_LEDGER (private-nowcast content): ...")
named one mechanism, but the contrast also carries the penalty applied to the
no-ledger control (Appendix A entry 22), so ``decision`` now states where each
interval lies and what the contrast computes, and prints the penalty weight at
lambda = 1 and lambda = 0 side by side when the report carries the lambda = 0
block (``d2_signals.run`` with ``lambda_zero=True``, the CLI default) or
VHAT_ONLY (the ledger at lambda = 0).
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

from .d2_signals import LAMBDA_CALIBRATION_NOTE, _print_summary


def _where(lo, hi) -> str:
    return "CI excludes 0" if (lo > 0 or hi < 0) else "CI spans 0"


def decision(report):
    """The offline readout (v1 wording): each registered contrast with where its interval lies and
    what it computes; no mechanism is inferred. The judge clause needs a judge run."""
    con = report["contrasts"]
    agg = report["aggregate"]
    lam = report.get("config", {}).get("lambda")
    lines = []
    for key, what in (("LEDGER_MECHANICAL_minus_MECHANICAL_NO_LEDGER",
                       "registered primary contrast; at lambda = 1 most of it is the penalty's cost on the "
                       "no-ledger control, not the nowcast"),
                      ("LEDGER_MECHANICAL_minus_MYOPIC_ACTUALS", "KILL-2 guard (actuals alone)"),
                      ("LEDGER_MECHANICAL_minus_VHAT_ONLY", "the penalty's effect on the ledger itself")):
        c = con[key]
        lines.append(f"{key}: {c['mean_tau_diff']:+.3f} [{c['ci95'][0]:+.3f}, {c['ci95'][1]:+.3f}] "
                     f"({_where(*c['ci95'])}); {what}")
    lz = report.get("lambda_zero")
    led = agg["LEDGER_MECHANICAL"]
    v0 = (lz or {}).get("LEDGER_MECHANICAL", {}).get("lambda_0") or agg.get("VHAT_ONLY")
    lines.append(f"ledger at lambda = {lam}: {led['mean_tau']:+.3f}; at lambda = 0 (VHAT_ONLY): "
                 f"{v0['mean_tau']:+.3f}")
    if lz:
        nl, n0 = lz["MECHANICAL_NO_LEDGER"]["lambda_used"], lz["MECHANICAL_NO_LEDGER"]["lambda_0"]
        lines.append(f"no-ledger at lambda = {lam}: {nl['mean_tau']:+.3f}; at lambda = 0 (sort of the handed "
                     f"projection): {n0['mean_tau']:+.3f}")
    else:
        lines.append("no-ledger at lambda = 0 (sort of the handed projection): not in this report "
                     "(dbwm eval6-d2 signals run without --paper-format adds it)")
    lines.append(LAMBDA_CALIBRATION_NOTE)
    lines.append("no mechanism is inferred: the ledger signals and the no-ledger control all run the true "
                 "engine (ledger E6-13), so these contrasts measure starting state and penalty, not access")
    lines.append("judge clause (judge minus ledger): needs a judge run "
                 "(dbwm eval6-d2 judge run --model M --offline-report <this report>)")
    return lines


def render(report) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _print_summary(report)
        print("\n  D2 readout (offline signals only; v1 wording, no mechanism inferred):")
        for line in decision(report):
            print("    -", line)
    return buf.getvalue()


def configure_parser(ap):
    ap.add_argument("report", help="a d2_signals report JSON")
    return ap


def run_cli(args) -> int:
    print(render(json.loads(Path(args.report).read_text())), end="")
    return 0
