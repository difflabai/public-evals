"""The paper's eval-1 report renderer (report.py of the frozen harness), ported with marked edits.

:mod:`dbwm.eval1.report` feeds a run's scored cells to :func:`render_rows`, so a
new run is summarized by the paper's aggregation, bootstrap and pre-registered
decision rule (PROVED/PARTIAL/DISPROVED) and rendered by its HTML code.

Everything from the constants to :func:`render_rows` is the paper's report.py
(L22-1492) unchanged except for marked edits. (1) Float sums go through
:func:`_sum_seq` (left-to-right addition): CPython 3.12 switched ``sum()`` of
floats to compensated summation, which would move the last binary digit of the
bootstrap CIs between interpreters. (2) The chart captions describe what each
chart shows and assert no result: the paper printed six static narrative
captions whatever the data said (docs/instrument-changes/eval1.md E1-R01).
(3) ``main()`` and its file loaders are replaced by :func:`render_rows`, which
takes already-loaded rows and returns the outputs instead of writing them.

The paper's definitions here are NOT the pre-registration's
(docs/instrument-changes/eval1.md E1-R05): violation_rate is the FRACTION of
plans with at least one violation, outcome_cv is computed on
goal_multiple_actual, and every rate is over the verdicts present (a missing
trial is not a failure here; the release report prints both denominators).

Pure standard library. The bootstrap uses ``random.Random(12345)`` and must see
rows in sorted verdict-file order (``<company>__<tier>__<arm>__r<rep>.json``).
"""

import html
import json
import math
import random
import statistics
from collections import defaultdict
from decimal import Decimal, InvalidOperation


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BOOTSTRAP_B = 2000
BOOTSTRAP_SEED = 12345

# Canonical condition ordering (the "dose" axis, then the ablations).
CONDITION_ORDER = [
    "ungrounded",
    "grounded-readonly",
    "grounded-iterate",
    "prose-feedback",
    "raw-figures",
    "broken-model",
]
DOSE_ORDER = ["ungrounded", "grounded-readonly", "grounded-iterate"]
TIER_ORDER = ["soft", "hard", "hostile"]

CONTROL = "ungrounded"
TREATMENT = "grounded-iterate"

CONDITION_LABEL = {
    "ungrounded": "Ungrounded (prose only)",
    "grounded-readonly": "Grounded read-only (what-if)",
    "grounded-iterate": "Grounded iterate (edit-run-test-fix)",
    "prose-feedback": "Prose-feedback (ablation: verdict, no figures)",
    "raw-figures": "Raw-figures (ablation: figures, no verdict)",
    "broken-model": "Broken-model (lesion)",
}


# ---------------------------------------------------------------------------
# Small numeric helpers
# ---------------------------------------------------------------------------

def to_decimal(value):
    """Parse a decimal string -> Decimal, or None on null/missing/garbage."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def to_float(value):
    """Parse a (possibly Decimal-string) value -> float, or None."""
    d = to_decimal(value)
    return None if d is None else float(d)


def _sum_seq(values):
    """Left-to-right float addition, i.e. ``sum()`` as the paper's interpreter computed it.

    Port note (the only arithmetic change in this module): CPython 3.12 made
    the built-in ``sum()`` of floats use compensated (Neumaier) summation, so
    on 3.12+ the bootstrap CIs and means would differ in the last binary digit.
    Adding in order gives the same bytes on every supported interpreter.
    """
    total = 0
    for v in values:
        total = total + v
    return total


def mean(values):
    vals = [v for v in values if v is not None]
    return _sum_seq(vals) / len(vals) if vals else None


def fraction(predicate_hits, n):
    return (predicate_hits / n) if n else None


def fmt_pct(x, digits=0):
    if x is None:
        return "n/a"
    return f"{100 * x:.{digits}f}%"


def fmt_num(x, digits=2):
    if x is None:
        return "n/a"
    return f"{x:.{digits}f}"


def fmt_ci(ci):
    if ci is None or ci[0] is None or ci[1] is None:
        return "n/a"
    return f"[{ci[0]:.3f}, {ci[1]:.3f}]"


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _bool(row, key):
    return bool(row.get(key))


def aggregate_group(rows):
    """Compute the standard metric bundle over a list of verdict rows."""
    n = len(rows)
    if n == 0:
        return {
            "n": 0,
            "overclaim_rate": None,
            "claim_accuracy": None,
            "claimed_success_rate": None,
            "valid_rate": None,
            "goal_rate": None,
            "violation_rate": None,
            "mean_violations": None,
            "forecast_mae": None,
            "forecast_within_10": None,
            "outcome_cv": None,
            "queried_model_rate": None,
            "mean_query_calls": None,
            "mean_agent_turns": None,
            "mean_agent_cost_usd": None,
        }

    forecast_errors = [
        to_float(r.get("forecast_pct_error"))
        for r in rows
        if to_float(r.get("forecast_pct_error")) is not None
    ]
    within_10 = [e for e in forecast_errors if e <= 10]

    # Tool-utilization (mechanism evidence) — present only on live-agent rows.
    traced = [r for r in rows if r.get("queried_model") is not None]
    query_calls = [int(r.get("bwm_query_calls") or 0) for r in traced]
    turns = [r.get("agent_turns") for r in traced if r.get("agent_turns") is not None]
    costs = [to_float(r.get("agent_cost_usd")) for r in traced
             if to_float(r.get("agent_cost_usd")) is not None]

    return {
        "n": n,
        "overclaim_rate": fraction(sum(_bool(r, "overclaim") for r in rows), n),
        "claim_accuracy": fraction(sum(_bool(r, "claim_correct") for r in rows), n),
        "claimed_success_rate": fraction(
            sum(_bool(r, "claimed_success") for r in rows), n
        ),
        "valid_rate": fraction(sum(_bool(r, "fully_valid") for r in rows), n),
        "goal_rate": fraction(sum(_bool(r, "goal_met") for r in rows), n),
        "violation_rate": fraction(
            sum(1 for r in rows if int(r.get("hard_violation_count") or 0) > 0), n
        ),
        "mean_violations": mean(
            [int(r.get("hard_violation_count") or 0) for r in rows]
        ),
        "forecast_mae": (mean(forecast_errors) if forecast_errors else None),
        "forecast_within_10": (
            len(within_10) / len(forecast_errors) if forecast_errors else None
        ),
        "outcome_cv": compute_outcome_cv(rows),
        "queried_model_rate": (
            fraction(sum(1 for r in traced if r.get("queried_model")), len(traced))
            if traced else None
        ),
        "mean_query_calls": (mean(query_calls) if query_calls else None),
        "mean_agent_turns": (mean(turns) if turns else None),
        "mean_agent_cost_usd": (mean(costs) if costs else None),
    }


def compute_outcome_cv(rows):
    """Average coefficient of variation of goal_multiple_actual.

    For each (company, tier) cell, compute stdev/mean of the goal multiple
    across reps (skip null); average those CVs across cells. Returns None if no
    cell has >= 2 usable reps.
    """
    cells = defaultdict(list)
    for r in rows:
        val = to_float(r.get("goal_multiple_actual"))
        if val is None:
            continue
        cells[(r.get("company"), r.get("tier"))].append(val)

    cvs = []
    for values in cells.values():
        if len(values) < 2:
            continue
        cell_mean = statistics.fmean(values)
        if cell_mean == 0:
            continue
        cell_sd = statistics.stdev(values)
        cvs.append(cell_sd / abs(cell_mean))

    return (_sum_seq(cvs) / len(cvs)) if cvs else None


def aggregate_by_condition(rows):
    by_cond = defaultdict(list)
    for r in rows:
        by_cond[r.get("condition")].append(r)
    return {cond: aggregate_group(group) for cond, group in by_cond.items()}


def aggregate_by_condition_tier(rows):
    by_ct = defaultdict(list)
    for r in rows:
        by_ct[(r.get("condition"), r.get("tier"))].append(r)
    return {key: aggregate_group(group) for key, group in by_ct.items()}


# ---------------------------------------------------------------------------
# Bootstrap statistics
# ---------------------------------------------------------------------------

def _bootstrap_rng():
    return random.Random(BOOTSTRAP_SEED)


def bootstrap_ci_rate(values, rng, b=BOOTSTRAP_B, alpha=0.05):
    """Percentile bootstrap CI for the mean of a 0/1 (or numeric) list."""
    values = [v for v in values if v is not None]
    n = len(values)
    if n == 0:
        return (None, None)
    means = []
    for _ in range(b):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(_sum_seq(sample) / n)
    means.sort()
    lo = means[int((alpha / 2) * b)]
    hi = means[min(b - 1, int((1 - alpha / 2) * b))]
    return (lo, hi)


def bootstrap_ci_difference(group_a, group_b, rng, b=BOOTSTRAP_B, alpha=0.05):
    """Percentile bootstrap CI for mean(group_a) - mean(group_b).

    Resamples each group independently. Returns (point, (lo, hi)).
    """
    a = [v for v in group_a if v is not None]
    bb = [v for v in group_b if v is not None]
    na, nb = len(a), len(bb)
    if na == 0 or nb == 0:
        return (None, (None, None))
    point = (_sum_seq(a) / na) - (_sum_seq(bb) / nb)
    diffs = []
    for _ in range(b):
        sa = _sum_seq(a[rng.randrange(na)] for _ in range(na)) / na
        sb = _sum_seq(bb[rng.randrange(nb)] for _ in range(nb)) / nb
        diffs.append(sa - sb)
    diffs.sort()
    lo = diffs[int((alpha / 2) * b)]
    hi = diffs[min(b - 1, int((1 - alpha / 2) * b))]
    return (point, (lo, hi))


def ci_excludes_zero(ci):
    if ci is None or ci[0] is None or ci[1] is None:
        return False
    return ci[0] > 0 or ci[1] < 0


# Per-trial value extractors for bootstrap inputs ----------------------------

def overclaim_values(rows):
    return [1.0 if _bool(r, "overclaim") else 0.0 for r in rows]


def violation_values(rows):
    return [1.0 if int(r.get("hard_violation_count") or 0) > 0 else 0.0 for r in rows]


def valid_values(rows):
    return [1.0 if _bool(r, "fully_valid") else 0.0 for r in rows]


def claim_correct_values(rows):
    return [1.0 if _bool(r, "claim_correct") else 0.0 for r in rows]


def claimed_success_values(rows):
    return [1.0 if _bool(r, "claimed_success") else 0.0 for r in rows]


def goal_values(rows):
    return [1.0 if _bool(r, "goal_met") else 0.0 for r in rows]


def forecast_error_values(rows):
    return [
        to_float(r.get("forecast_pct_error"))
        for r in rows
        if to_float(r.get("forecast_pct_error")) is not None
    ]


def compute_condition_cis(rows_by_cond):
    """95% bootstrap CIs for the main rates, per condition (fixed RNG)."""
    rng = _bootstrap_rng()
    cis = {}
    for cond in sorted(rows_by_cond):
        rows = rows_by_cond[cond]
        cis[cond] = {
            "overclaim_rate": bootstrap_ci_rate(overclaim_values(rows), rng),
            "violation_rate": bootstrap_ci_rate(violation_values(rows), rng),
            "valid_rate": bootstrap_ci_rate(valid_values(rows), rng),
            "claim_accuracy": bootstrap_ci_rate(claim_correct_values(rows), rng),
            "claimed_success_rate": bootstrap_ci_rate(
                claimed_success_values(rows), rng
            ),
            "goal_rate": bootstrap_ci_rate(goal_values(rows), rng),
        }
    return cis


def compute_primary_differences(rows_by_cond):
    """Bootstrap the control - treatment differences on the primary metrics."""
    rng = _bootstrap_rng()
    control = rows_by_cond.get(CONTROL, [])
    treatment = rows_by_cond.get(TREATMENT, [])

    diffs = {}
    oc_point, oc_ci = bootstrap_ci_difference(
        overclaim_values(control), overclaim_values(treatment), rng
    )
    vr_point, vr_ci = bootstrap_ci_difference(
        violation_values(control), violation_values(treatment), rng
    )
    fe_point, fe_ci = bootstrap_ci_difference(
        forecast_error_values(control), forecast_error_values(treatment), rng
    )
    diffs["overclaim_rate"] = {
        "point": oc_point,
        "ci": oc_ci,
        "significant": ci_excludes_zero(oc_ci),
    }
    diffs["violation_rate"] = {
        "point": vr_point,
        "ci": vr_ci,
        "significant": ci_excludes_zero(vr_ci),
    }
    diffs["forecast_mae"] = {
        "point": fe_point,
        "ci": fe_ci,
        "significant": ci_excludes_zero(fe_ci),
    }
    return diffs


# ---------------------------------------------------------------------------
# Pre-registered hypotheses
# ---------------------------------------------------------------------------

def get_rate(by_cond, cond, key):
    agg = by_cond.get(cond)
    return None if agg is None else agg.get(key)


def get_tier_rate(by_ct, cond, tier, key):
    agg = by_ct.get((cond, tier))
    return None if agg is None else agg.get(key)


def evaluate_hypotheses(by_cond, by_ct, primary_diffs):
    """Apply the pre-registered decision rules. Returns (rows, verdict)."""
    results = []

    def add(name, claim, numbers, passed, na=False):
        results.append(
            {
                "name": name,
                "claim": claim,
                "numbers": numbers,
                "pass": (None if na else bool(passed)),
                "na": na,
            }
        )

    ung_oc = get_rate(by_cond, CONTROL, "overclaim_rate")
    gi_oc = get_rate(by_cond, TREATMENT, "overclaim_rate")
    ung_vr = get_rate(by_cond, CONTROL, "violation_rate")
    gi_vr = get_rate(by_cond, TREATMENT, "violation_rate")
    ung_mae = get_rate(by_cond, CONTROL, "forecast_mae")
    gi_mae = get_rate(by_cond, TREATMENT, "forecast_mae")
    ung_cv = get_rate(by_cond, CONTROL, "outcome_cv")
    gi_cv = get_rate(by_cond, TREATMENT, "outcome_cv")

    oc_diff = primary_diffs["overclaim_rate"]
    vr_diff = primary_diffs["violation_rate"]

    # --- H1: testability -> constraint compliance -----------------------
    h1_pass = (
        ung_vr is not None
        and gi_vr is not None
        and gi_vr <= 0.5 * ung_vr
        and vr_diff["significant"]
    )
    add(
        "H1 -- testability -> compliance",
        "Iterating against a testable model at least halves the hard-constraint "
        "violation rate vs. prose only.",
        (
            f"violation_rate(grounded-iterate)={fmt_pct(gi_vr, 1)} "
            f"≤ 0.5×violation_rate(ungrounded)="
            f"{fmt_pct(None if ung_vr is None else 0.5 * ung_vr, 1)}; "
            f"diff CI {fmt_ci(vr_diff['ci'])} "
            f"({'excludes' if vr_diff['significant'] else 'includes'} 0)"
        ),
        h1_pass,
    )

    # --- H2: consistency -> calibrated claims (HEADLINE) ----------------
    h2_pass = (
        ung_oc is not None
        and gi_oc is not None
        and ung_oc >= 0.30
        and gi_oc <= 0.05
        and oc_diff["significant"]
    )
    add(
        "H2 -- consistency -> calibrated claims (HEADLINE)",
        "Prose-only agents over-claim success they didn't earn (≥30%); the "
        "grounded-iterate agent almost never does (≤5%).",
        (
            f"overclaim_rate(ungrounded)={fmt_pct(ung_oc, 1)} ≥ 30%; "
            f"overclaim_rate(grounded-iterate)={fmt_pct(gi_oc, 1)} ≤ 5%; "
            f"diff CI {fmt_ci(oc_diff['ci'])} "
            f"({'excludes' if oc_diff['significant'] else 'includes'} 0)"
        ),
        h2_pass,
    )

    # --- H2b: forecast accuracy -----------------------------------------
    h2b_pass = (
        gi_mae is not None
        and ung_mae is not None
        and gi_mae <= 10
        and ung_mae >= 25
    )
    add(
        "H2b -- forecast accuracy",
        "Reading the forecast off a consistent model is accurate (MAE ≤10%); "
        "guessing in prose is not (MAE ≥25%).",
        (
            f"forecast_mae(grounded-iterate)={fmt_num(gi_mae)}% ≤ 10; "
            f"forecast_mae(ungrounded)={fmt_num(ung_mae)}% ≥ 25"
        ),
        h2b_pass,
    )

    # --- H3: output stability -------------------------------------------
    if ung_cv is None or gi_cv is None:
        add(
            "H3 -- output stability",
            "Iterating yields more reproducible outcomes (lower CV across reps).",
            "outcome_cv unavailable (reps < 2 in every cell)",
            None,
            na=True,
        )
        h3_pass = None
    else:
        h3_pass = gi_cv < ung_cv
        add(
            "H3 -- output stability",
            "Iterating yields more reproducible outcomes (lower CV across reps).",
            (
                f"outcome_cv(grounded-iterate)={fmt_num(gi_cv, 4)} < "
                f"outcome_cv(ungrounded)={fmt_num(ung_cv, 4)}"
            ),
            h3_pass,
        )

    # --- H4: multi-step feasibility on hostile --------------------------
    ung_valid_hostile = get_tier_rate(by_ct, CONTROL, "hostile", "valid_rate")
    gi_valid_hostile = get_tier_rate(by_ct, TREATMENT, "hostile", "valid_rate")
    h4_pass = (
        gi_valid_hostile is not None
        and ung_valid_hostile is not None
        and gi_valid_hostile >= 0.5
        and gi_valid_hostile >= 3 * ung_valid_hostile
    )
    add(
        "H4 -- multi-step feasibility (hostile)",
        "On the hardest (hostile) tier the grounded-iterate agent still produces "
        "fully-valid plans (≥50%, and ≥3× the prose-only rate).",
        (
            f"valid_rate(grounded-iterate, hostile)={fmt_pct(gi_valid_hostile, 1)} "
            f"≥ 50% and ≥ 3× valid_rate(ungrounded, hostile)="
            f"{fmt_pct(ung_valid_hostile, 1)}"
        ),
        h4_pass,
    )

    # --- Dose-response ---------------------------------------------------
    eps = 0.0
    dose_valid = [get_rate(by_cond, c, "valid_rate") for c in DOSE_ORDER]
    dose_oc = [get_rate(by_cond, c, "overclaim_rate") for c in DOSE_ORDER]
    dose_present = all(v is not None for v in dose_valid + dose_oc)
    if not dose_present:
        add(
            "Dose-response",
            "More grounding monotonically helps: validity goes up, over-claiming "
            "goes down across [ungrounded ≤ read-only ≤ iterate].",
            "Not all dose conditions present.",
            None,
            na=True,
        )
        dose_pass = None
    else:
        valid_nondec = all(
            dose_valid[i + 1] >= dose_valid[i] - eps
            for i in range(len(dose_valid) - 1)
        )
        oc_noninc = all(
            dose_oc[i + 1] <= dose_oc[i] + eps for i in range(len(dose_oc) - 1)
        )
        dose_pass = valid_nondec and oc_noninc
        add(
            "Dose-response",
            "More grounding monotonically helps: validity goes up, over-claiming "
            "goes down across [ungrounded ≤ read-only ≤ iterate].",
            (
                "valid_rate: "
                + " ≤ ".join(fmt_pct(v, 1) for v in dose_valid)
                + " (non-decreasing="
                + str(valid_nondec)
                + "); overclaim_rate: "
                + " ≥ ".join(fmt_pct(v, 1) for v in dose_oc)
                + " (non-increasing="
                + str(oc_noninc)
                + ")"
            ),
            dose_pass,
        )

    # --- Mechanism (lesion / ablation) ----------------------------------
    bm_oc = get_rate(by_cond, "broken-model", "overclaim_rate")
    pf_oc = get_rate(by_cond, "prose-feedback", "overclaim_rate")
    mech_present = (
        bm_oc is not None and pf_oc is not None and ung_oc is not None
    )
    if not mech_present:
        add(
            "Mechanism (lesion / ablation)",
            "Access without consistency (broken-model) and feedback without "
            "numbers (prose-feedback) do NOT close the over-claim gap.",
            "Ablation conditions absent -- mechanism check skipped.",
            None,
            na=True,
        )
        mech_pass = None  # absent -> treated as satisfied for the verdict
    else:
        mech_pass = (bm_oc >= 0.5 * ung_oc) and (pf_oc >= 0.5 * ung_oc)
        add(
            "Mechanism (lesion / ablation)",
            "Access without consistency (broken-model) and feedback without "
            "numbers (prose-feedback) do NOT close the over-claim gap "
            "(consistency is the active ingredient).",
            (
                f"overclaim_rate(broken-model)={fmt_pct(bm_oc, 1)} and "
                f"overclaim_rate(prose-feedback)={fmt_pct(pf_oc, 1)} "
                f"both ≥ 0.5×overclaim_rate(ungrounded)="
                f"{fmt_pct(None if ung_oc is None else 0.5 * ung_oc, 1)}"
            ),
            mech_pass,
        )

    # --- Final verdict ---------------------------------------------------
    h1 = h1_pass
    h2 = h2_pass
    # Mechanism passes if conditions absent (mech_pass is None) or True.
    mech_ok = (mech_pass is None) or bool(mech_pass)

    if h1 and h2 and mech_ok:
        verdict = "PROVED"
    elif (not h1) and (not h2):
        verdict = "DISPROVED"
    else:
        verdict = "PARTIAL"

    return results, verdict


# ---------------------------------------------------------------------------
# Worked examples
# ---------------------------------------------------------------------------

def pick_worked_examples(rows):
    """Return (overclaim_row, valid_row) for the same company+tier, or Nones."""
    overclaimers = [
        r
        for r in rows
        if r.get("condition") == CONTROL
        and _bool(r, "overclaim")
        and r.get("tier") in ("hard", "hostile")
    ]
    # Prefer hostile (the most dramatic) then hard.
    overclaimers.sort(key=lambda r: (0 if r.get("tier") == "hostile" else 1,
                                     str(r.get("company")), int(r.get("rep") or 0)))
    for oc in overclaimers:
        comp, tier = oc.get("company"), oc.get("tier")
        matches = [
            r
            for r in rows
            if r.get("condition") == TREATMENT
            and r.get("company") == comp
            and r.get("tier") == tier
            and _bool(r, "fully_valid")
        ]
        if matches:
            matches.sort(key=lambda r: int(r.get("rep") or 0))
            return oc, matches[0]
    return (overclaimers[0] if overclaimers else None), None


# ---------------------------------------------------------------------------
# SVG chart helpers
# ---------------------------------------------------------------------------

PALETTE = [
    "#5b9bd5",  # blue
    "#70ad47",  # green
    "#ffc000",  # amber
    "#ed7d31",  # orange
    "#a05195",  # purple
]
INK = "#e6e9ef"
MUTED = "#9aa3b2"
GRID = "#2b3242"
PANEL = "#1b202b"


def _svg_text(x, y, text, *, size=12, anchor="middle", fill=INK, weight="normal"):
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" '
        f'text-anchor="{anchor}" fill="{fill}" font-weight="{weight}" '
        f'font-family="-apple-system,Segoe UI,Roboto,sans-serif">'
        f"{html.escape(str(text))}</text>"
    )


def simple_bar_chart(labels, values, *, title="", width=720, height=300,
                     value_fmt=lambda v: f"{v:.0f}", y_max=None,
                     ref_line=None, ref_label="", colors=None):
    """A single-series vertical bar chart. values may contain None."""
    pad_l, pad_r, pad_t, pad_b = 48, 24, 40, 56
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    finite = [v for v in values if v is not None]
    if y_max is None:
        y_max = max(finite) * 1.15 if finite else 1.0
    if y_max <= 0:
        y_max = 1.0

    n = len(labels)
    slot = plot_w / max(n, 1)
    bar_w = slot * 0.6
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'role="img" preserveAspectRatio="xMidYMid meet" '
        f'style="max-width:{width}px">',
        f'<rect x="0" y="0" width="{width}" height="{height}" '
        f'fill="{PANEL}" rx="8"/>',
    ]
    if title:
        parts.append(_svg_text(width / 2, 22, title, size=14, weight="600"))

    # gridlines
    for i in range(5):
        gy = pad_t + plot_h * i / 4
        val = y_max * (1 - i / 4)
        parts.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" '
            f'y2="{gy:.1f}" stroke="{GRID}" stroke-width="1"/>'
        )
        parts.append(_svg_text(pad_l - 6, gy + 4, f"{val:.0f}", size=10,
                               anchor="end", fill=MUTED))

    cols = colors or [PALETTE[0]] * n
    for i, (lab, val) in enumerate(zip(labels, values)):
        cx = pad_l + slot * i + slot / 2
        if val is not None:
            bh = plot_h * (min(val, y_max) / y_max)
            by = pad_t + plot_h - bh
            parts.append(
                f'<rect x="{cx - bar_w / 2:.1f}" y="{by:.1f}" '
                f'width="{bar_w:.1f}" height="{bh:.1f}" rx="3" '
                f'fill="{cols[i % len(cols)]}"/>'
            )
            parts.append(_svg_text(cx, by - 6, value_fmt(val), size=11,
                                   weight="600"))
        else:
            parts.append(_svg_text(cx, pad_t + plot_h - 6, "n/a", size=10,
                                   fill=MUTED))
        parts.append(_svg_text(cx, pad_t + plot_h + 18, lab, size=10,
                               fill=MUTED))

    if ref_line is not None and y_max > 0:
        ry = pad_t + plot_h * (1 - min(ref_line, y_max) / y_max)
        parts.append(
            f'<line x1="{pad_l}" y1="{ry:.1f}" x2="{width - pad_r}" '
            f'y2="{ry:.1f}" stroke="#ff6b6b" stroke-width="1.5" '
            f'stroke-dasharray="6 4"/>'
        )
        parts.append(_svg_text(width - pad_r, ry - 5, ref_label, size=10,
                               anchor="end", fill="#ff9d9d"))

    parts.append("</svg>")
    return "".join(parts)


def grouped_bar_chart(group_labels, series, *, title="", width=760, height=340,
                      value_fmt=lambda v: f"{100 * v:.0f}%", y_max=1.0):
    """Grouped vertical bars.

    series: list of (series_name, color, [values aligned to group_labels]).
    """
    pad_l, pad_r, pad_t, pad_b = 48, 24, 64, 62
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    finite = [v for _, _, vals in series for v in vals if v is not None]
    if y_max is None:
        y_max = max(finite) * 1.15 if finite else 1.0
    if y_max <= 0:
        y_max = 1.0

    n_groups = len(group_labels)
    n_series = len(series)
    slot = plot_w / max(n_groups, 1)
    group_w = slot * 0.74
    bar_w = group_w / max(n_series, 1)

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'role="img" preserveAspectRatio="xMidYMid meet" '
        f'style="max-width:{width}px">',
        f'<rect x="0" y="0" width="{width}" height="{height}" '
        f'fill="{PANEL}" rx="8"/>',
    ]
    if title:
        parts.append(_svg_text(width / 2, 22, title, size=14, weight="600"))

    # legend
    lx = pad_l
    for name, color, _ in series:
        parts.append(
            f'<rect x="{lx}" y="32" width="12" height="12" rx="2" '
            f'fill="{color}"/>'
        )
        parts.append(_svg_text(lx + 17, 42, name, size=11, anchor="start",
                               fill=MUTED))
        lx += 22 + 7.4 * len(name) + 18

    # gridlines
    for i in range(5):
        gy = pad_t + plot_h * i / 4
        val = y_max * (1 - i / 4)
        parts.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" '
            f'y2="{gy:.1f}" stroke="{GRID}" stroke-width="1"/>'
        )
        lab = (f"{100 * val:.0f}%" if y_max <= 1.0 else f"{val:.0f}")
        parts.append(_svg_text(pad_l - 6, gy + 4, lab, size=10, anchor="end",
                               fill=MUTED))

    for gi, glabel in enumerate(group_labels):
        gx = pad_l + slot * gi + (slot - group_w) / 2
        for si, (_, color, vals) in enumerate(series):
            val = vals[gi] if gi < len(vals) else None
            bx = gx + bar_w * si
            cx = bx + bar_w / 2
            if val is not None:
                bh = plot_h * (min(val, y_max) / y_max)
                by = pad_t + plot_h - bh
                parts.append(
                    f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w * 0.86:.1f}" '
                    f'height="{bh:.1f}" rx="2.5" fill="{color}"/>'
                )
                parts.append(_svg_text(cx, by - 4, value_fmt(val), size=9,
                                       weight="600"))
            else:
                parts.append(_svg_text(cx, pad_t + plot_h - 4, "n/a", size=9,
                                       fill=MUTED))
        # group label (wrap on space if long)
        gcx = pad_l + slot * gi + slot / 2
        parts.append(_svg_text(gcx, pad_t + plot_h + 18, glabel, size=10,
                               fill=MUTED))

    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

VERDICT_STYLE = {
    "PROVED": ("#1f7a3d", "#3ddc7e", "The numerical-model agent is internally "
               "consistent and testable -- and it shows in calibrated claims "
               "and constraint compliance."),
    "PARTIAL": ("#7a5a00", "#ffc83d", "Some pre-registered predictions held and "
                "others did not -- the evidence is mixed."),
    "DISPROVED": ("#7a1f1f", "#ff6b6b", "Grounding in a numerical model did not "
                  "deliver the predicted gains in consistency or testability."),
}


def esc(text):
    return html.escape("" if text is None else str(text))


def render_constraint_table(constraints):
    rows = []
    for c in constraints or []:
        ok = bool(c.get("ok"))
        mark = '<span class="ok">✓</span>' if ok else '<span class="bad">✗</span>'
        rows.append(
            "<tr>"
            f"<td>{esc(c.get('id'))}</td>"
            f"<td class='mono'>{esc(c.get('driver'))}</td>"
            f"<td class='mono'>{esc(c.get('limit'))}</td>"
            f"<td class='mono'>{esc(_short_num(c.get('actual')))}</td>"
            f"<td>{mark}</td>"
            "</tr>"
        )
    return (
        "<table class='ctable'><thead><tr>"
        "<th>constraint</th><th>driver</th><th>limit</th><th>actual</th><th>ok</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _short_num(value):
    d = to_decimal(value)
    if d is None:
        return esc(value)
    f = float(d)
    if abs(f) >= 1000 or (f != 0 and abs(f) >= 1):
        return f"{f:,.2f}"
    return f"{f:.4f}"


def render_bindings(bindings):
    if not bindings:
        return "<p class='muted'>(no plan bindings)</p>"
    rows = []
    for driver, series in bindings.items():
        cells = " ".join(_short_num(v) for v in (series or []))
        rows.append(
            f"<tr><td class='mono'>{esc(driver)}</td>"
            f"<td class='mono small'>{esc(cells)}</td></tr>"
        )
    return (
        "<table class='ctable'><thead><tr><th>lever</th>"
        "<th>per-quarter binding</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def render_example_card(row, kind):
    if row is None:
        return f"<div class='card'><p class='muted'>No {esc(kind)} example available.</p></div>"
    overclaim = _bool(row, "overclaim")
    valid = _bool(row, "fully_valid")
    badge_cls = "bad" if kind == "overclaim" else "ok"
    badge_txt = "OVER-CLAIM" if kind == "overclaim" else "FULLY VALID"

    gma = to_float(row.get("goal_multiple_actual"))
    gt = to_float(row.get("goal_target_multiple"))
    fpe = to_float(row.get("forecast_pct_error"))
    claimed_rev = row.get("claimed_final_revenue")
    oracle_rev = row.get("oracle_final_revenue")

    claim_line = (
        "claimed SUCCESS" if _bool(row, "claimed_success") else "claimed failure"
    )
    truth_line = "actually VALID" if valid else "actually INVALID"

    return (
        "<div class='card'>"
        f"<div class='card-head'>"
        f"<span class='pill {badge_cls}'>{badge_txt}</span>"
        f"<span class='card-sub'>{esc(CONDITION_LABEL.get(row.get('condition'), row.get('condition')))}</span>"
        "</div>"
        f"<div class='card-meta'>{esc(row.get('company'))} · "
        f"{esc(row.get('tier'))} tier · rep {esc(row.get('rep'))} · "
        f"{esc(row.get('category'))}</div>"
        f"<p class='rationale'>“{esc(row.get('rationale'))}”</p>"
        "<div class='block-h'>Plan (lever bindings)</div>"
        f"{render_bindings(row.get('plan_bindings'))}"
        "<div class='block-h'>Constraints (oracle-scored)</div>"
        f"{render_constraint_table(row.get('constraints'))}"
        "<div class='kv'>"
        f"<div><span>goal multiple</span><b>{fmt_num(gma)} / target {fmt_num(gt)}"
        f" {'✓' if _bool(row, 'goal_met') else '✗'}</b></div>"
        f"<div><span>agent claim</span><b>{esc(claim_line)}</b></div>"
        f"<div><span>oracle truth</span><b>{esc(truth_line)}</b></div>"
        f"<div><span>claimed revenue</span><b class='mono'>{esc(_short_num(claimed_rev))}</b></div>"
        f"<div><span>oracle revenue</span><b class='mono'>{esc(_short_num(oracle_rev))}</b></div>"
        f"<div><span>forecast error</span><b>{fmt_num(fpe)}%</b></div>"
        "</div>"
        "</div>"
    )


def render_hypotheses_table(hyp_rows, primary_diffs):
    body = []
    for h in hyp_rows:
        if h["na"]:
            verdict_cell = "<span class='pill muted2'>N/A</span>"
        elif h["pass"]:
            verdict_cell = "<span class='pill ok'>PASS</span>"
        else:
            verdict_cell = "<span class='pill bad'>FAIL</span>"
        body.append(
            "<tr>"
            f"<td><b>{esc(h['name'])}</b><div class='muted small'>{esc(h['claim'])}</div></td>"
            f"<td class='small'>{esc(h['numbers'])}</td>"
            f"<td>{verdict_cell}</td>"
            "</tr>"
        )

    def diff_row(label, d):
        sig = "excludes 0 (significant)" if d["significant"] else "includes 0"
        return (
            "<tr>"
            f"<td>{esc(label)}</td>"
            f"<td class='mono'>{fmt_num(d['point'], 3) if d['point'] is not None else 'n/a'}</td>"
            f"<td class='mono'>{fmt_ci(d['ci'])}</td>"
            f"<td class='small'>{esc(sig)}</td>"
            "</tr>"
        )

    diff_table = (
        "<table class='ctable'><thead><tr>"
        "<th>primary difference (ungrounded − grounded-iterate)</th>"
        "<th>point</th><th>95% CI</th><th>significance</th>"
        "</tr></thead><tbody>"
        + diff_row("overclaim_rate", primary_diffs["overclaim_rate"])
        + diff_row("violation_rate", primary_diffs["violation_rate"])
        + diff_row("forecast_mae (pct pts)", primary_diffs["forecast_mae"])
        + "</tbody></table>"
    )

    return (
        "<table class='htable'><thead><tr>"
        "<th>hypothesis</th><th>numbers used</th><th>result</th>"
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table>"
        + diff_table
    )


def render_data_source_banner(data_sources, run_meta):
    sources = set(data_sources)
    if run_meta and run_meta.get("data_source"):
        sources.add(run_meta.get("data_source"))
    if "illustrative-reference-policy" in sources:
        return (
            "<div class='banner amber'>"
            "<b>ILLUSTRATIVE — deterministic reference policies, not "
            "live-agent results.</b> The empirical verdict requires live-agent "
            "runs. These verdicts come from explicit reference policies scored by "
            "the real oracle; they show what the data looks like <em>if the "
            "theory's prediction holds</em>."
            "</div>"
        )
    if "stub-planner" in sources and "live-agent" not in sources:
        return (
            "<div class='banner amber'>"
            "<b>STUB PLANNER — pipeline smoke test, not real-agent results.</b> "
            "The planner ran in deterministic STUB mode (no LLM): ungrounded "
            "policies brute-force and over-claim; grounded policies search the "
            "oracle and report honestly. This proves the harness end-to-end; the "
            "empirical verdict needs a REAL planner (a logged-in Claude Code "
            "session — run <code>claude login</code>)."
            "</div>"
        )
    return (
        "<div class='banner green'>"
        "<b>LIVE-AGENT RESULTS.</b> These verdicts come from real agent runs "
        "scored by the oracle."
        "</div>"
    )


# Chart captions: what each chart shows, asserting no result (E1-R01).
CAPTIONS = {
    "neutral": {
        "headline": ("Per arm: the share of trials that claimed success and the share whose "
                     "plan the scorer found fully valid. Where the first bar exceeds the "
                     "second, the arm claimed more success than it achieved."),
        "violations": ("Share of plans with at least one constraint violation, per arm and "
                       "tier, over the scored trials."),
        "valid": "Share of fully-valid plans, per arm and tier, over the scored trials.",
        "forecast": ("Mean absolute percentage error of the agent's projected final revenue "
                     "against the scorer's, per arm. The dashed line marks 10%."),
        "ran": ("Share of trials in which the agent called the model tool at least once, "
                "per arm (counted by the oracle server). Ungrounded has no tool."),
        "examples": ("Two trials on the same company and tier, picked by a fixed rule: the "
                     "first ungrounded overclaim on hostile (else hard), and the first "
                     "fully-valid grounded-iterate trial on the same cell."),
    },
}


def render_report(*, title, verdict, hyp_rows, by_cond, by_ct, primary_diffs,
                  meta, examples, run_meta, captions="neutral"):
    bg, accent, summary_line = VERDICT_STYLE[verdict]
    cap = CAPTIONS[captions]

    conds_present = [c for c in CONDITION_ORDER if c in by_cond]
    tiers_present = [t for t in TIER_ORDER if t in {tier for _, tier in by_ct}]

    # ---- Headline chart: claimed vs valid ------------------------------
    claimed = [get_rate(by_cond, c, "claimed_success_rate") or 0.0 for c in conds_present]
    valid = [get_rate(by_cond, c, "valid_rate") or 0.0 for c in conds_present]
    cond_short = [c for c in conds_present]
    headline_svg = grouped_bar_chart(
        cond_short,
        [
            ("claimed success", PALETTE[3], claimed),
            ("actually valid", PALETTE[1], valid),
        ],
        title="Claim–reality gap: claimed success vs. fully-valid (by condition)",
        y_max=1.0,
    )

    # ---- Violation rate by condition x tier ----------------------------
    viol_series = []
    for i, tier in enumerate(tiers_present):
        vals = [get_tier_rate(by_ct, c, tier, "violation_rate") for c in conds_present]
        viol_series.append((tier, PALETTE[i % len(PALETTE)], vals))
    viol_svg = grouped_bar_chart(
        cond_short, viol_series,
        title="Hard-constraint violation rate by condition × tier",
        y_max=1.0,
    )

    # ---- Valid rate by condition x tier --------------------------------
    valid_series = []
    for i, tier in enumerate(tiers_present):
        vals = [get_tier_rate(by_ct, c, tier, "valid_rate") for c in conds_present]
        valid_series.append((tier, PALETTE[i % len(PALETTE)], vals))
    valid_svg = grouped_bar_chart(
        cond_short, valid_series,
        title="Fully-valid rate by condition × tier (note the hostile tier)",
        y_max=1.0,
    )

    # ---- Forecast MAE --------------------------------------------------
    mae_vals = [get_rate(by_cond, c, "forecast_mae") for c in conds_present]
    mae_max = max([v for v in mae_vals if v is not None] + [10]) * 1.2
    mae_colors = [
        PALETTE[1] if (v is not None and v <= 10) else PALETTE[3]
        for v in mae_vals
    ]
    mae_svg = simple_bar_chart(
        cond_short, mae_vals,
        title="Forecast error (MAE of percent error, lower is better)",
        value_fmt=lambda v: f"{v:.1f}%",
        y_max=mae_max, ref_line=10, ref_label="±10% target",
        colors=mae_colors,
    )

    # ---- Tool utilization (mechanism): did the agent run the model? -----
    ran_vals = [get_rate(by_cond, c, "queried_model_rate") for c in conds_present]
    any_traced = any(v is not None for v in ran_vals)
    ran_svg = simple_bar_chart(
        cond_short, ran_vals,
        title="Share of trials that actually ran the executable model "
              "(the mechanism in action)",
        value_fmt=lambda v: f"{v*100:.0f}%",
        y_max=1.0,
        colors=[PALETTE[1] if (v or 0) > 0.5 else PALETTE[3] for v in ran_vals],
    ) if any_traced else ""

    # ---- Per-condition summary table -----------------------------------
    metric_cols = [
        ("n", "n", lambda v: str(int(v)) if v is not None else "0"),
        ("overclaim", "overclaim_rate", lambda v: fmt_pct(v, 0)),
        ("claim acc.", "claim_accuracy", lambda v: fmt_pct(v, 0)),
        ("claimed succ.", "claimed_success_rate", lambda v: fmt_pct(v, 0)),
        ("valid", "valid_rate", lambda v: fmt_pct(v, 0)),
        ("goal", "goal_rate", lambda v: fmt_pct(v, 0)),
        ("viol. rate", "violation_rate", lambda v: fmt_pct(v, 0)),
        ("mean viol.", "mean_violations", lambda v: fmt_num(v, 2)),
        ("forecast MAE", "forecast_mae", lambda v: fmt_num(v, 1)),
        ("within ±10%", "forecast_within_10", lambda v: fmt_pct(v, 0)),
        ("outcome CV", "outcome_cv", lambda v: fmt_num(v, 3)),
        ("ran model", "queried_model_rate",
         lambda v: fmt_pct(v, 0) if v is not None else "—"),
        ("mean runs", "mean_query_calls",
         lambda v: fmt_num(v, 1) if v is not None else "—"),
        ("mean turns", "mean_agent_turns",
         lambda v: fmt_num(v, 1) if v is not None else "—"),
        ("mean $", "mean_agent_cost_usd",
         lambda v: f"${v:.3f}" if v is not None else "—"),
    ]
    head = "".join(f"<th>{esc(label)}</th>" for label, _, _ in metric_cols)
    summ_rows = []
    for c in conds_present:
        agg = by_cond[c]
        cells = "".join(
            f"<td class='mono'>{esc(fn(agg.get(key)))}</td>"
            for _, key, fn in metric_cols
        )
        summ_rows.append(
            f"<tr><td><b>{esc(c)}</b></td>{cells}</tr>"
        )
    summary_table = (
        "<table class='htable'><thead><tr><th>condition</th>"
        + head
        + "</tr></thead><tbody>"
        + "".join(summ_rows)
        + "</tbody></table>"
    )

    oc_ex, valid_ex = examples

    data_banner = render_data_source_banner(meta["data_sources"], run_meta)

    parts = []
    parts.append(f"<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>")
    parts.append(
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
    )
    parts.append(f"<title>{esc(title)}</title>")
    parts.append(STYLE)
    parts.append("</head><body><div class='wrap'>")

    # 1. Title + verdict banner
    parts.append(f"<h1>{esc(title)}</h1>")
    parts.append(
        f"<div class='verdict' style='background:{bg};border-color:{accent}'>"
        f"<div class='verdict-badge' style='color:{accent}'>{esc(verdict)}</div>"
        f"<div class='verdict-line'>{esc(summary_line)}</div>"
        "</div>"
    )

    # 2. Data-source banner + counts
    parts.append(data_banner)
    parts.append(
        "<div class='counts'>"
        f"<div><b>{meta['n']}</b><span>verdicts</span></div>"
        f"<div><b>{meta['n_companies']}</b><span>companies</span></div>"
        f"<div><b>{meta['n_tiers']}</b><span>tiers</span></div>"
        f"<div><b>{meta['n_conditions']}</b><span>conditions</span></div>"
        f"<div><b>{meta['reps']}</b><span>reps</span></div>"
        "</div>"
    )

    # 3. Theory + code analogy
    parts.append(
        "<section><h2>The theory</h2>"
        "<p class='lede'>A numerical model of a business gives an agent the same "
        "two affordances that source code gives a programmer: <b>internal "
        "consistency</b> (the parts agree, so a claim can be checked) and "
        "<b>testability</b> (you can run it and see whether it passes). With them, "
        "an agent can plan, run, and self-correct — and it stops claiming "
        "success it did not earn.</p>"
        "<table class='analogy'><thead><tr><th>code</th><th>numerical model</th>"
        "</tr></thead><tbody>"
        "<tr><td>program</td><td>plan (the lever bindings)</td></tr>"
        "<tr><td>run the program</td><td>evaluate the model forward</td></tr>"
        "<tr><td>test suite</td><td>tier limits / constraints</td></tr>"
        "<tr><td>pass / fail verdict</td><td>fully-valid pass/fail from the oracle</td></tr>"
        "</tbody></table></section>"
    )

    # 4. Headline chart
    parts.append(
        "<section><h2>Headline: the claim–reality gap</h2>"
        f"<div class='chart'>{headline_svg}</div>"
        f"<p class='caption'>{cap['headline']}</p></section>"
    )

    # 5. Violation chart
    parts.append(
        "<section><h2>Constraint compliance (testability)</h2>"
        f"<div class='chart'>{viol_svg}</div>"
        f"<p class='caption'>{cap['violations']}</p></section>"
    )

    # 6. Valid chart
    parts.append(
        "<section><h2>Multi-step feasibility</h2>"
        f"<div class='chart'>{valid_svg}</div>"
        f"<p class='caption'>{cap['valid']}</p></section>"
    )

    # 7. Forecast chart
    parts.append(
        "<section><h2>Forecast accuracy</h2>"
        f"<div class='chart'>{mae_svg}</div>"
        f"<p class='caption'>{cap['forecast']}</p>"
        "</section>"
    )

    # 7b. Tool-utilization (mechanism) chart — only when we have a live trace
    if ran_svg:
        parts.append(
            "<section><h2>Did the agent actually run the model?</h2>"
            f"<div class='chart'>{ran_svg}</div>"
            f"<p class='caption'>{cap['ran']}</p></section>"
        )

    # Per-condition summary table
    parts.append(
        "<section><h2>Per-condition aggregates</h2>"
        f"{summary_table}</section>"
    )

    # 8. Hypotheses
    parts.append(
        "<section><h2>Pre-registered hypotheses</h2>"
        f"{render_hypotheses_table(hyp_rows, primary_diffs)}</section>"
    )

    # 9. Worked examples
    parts.append(
        "<section><h2>Worked examples (the mechanism, made concrete)</h2>"
        f"<p class='caption'>{cap['examples']}</p>"
        "<div class='cards'>"
        f"{render_example_card(oc_ex, 'overclaim')}"
        f"{render_example_card(valid_ex, 'valid')}"
        "</div></section>"
    )

    # 10. Methods footer
    parts.append(
        "<section class='methods'><h2>Methods</h2>"
        "<ul>"
        f"<li><b>Companies ({meta['n_companies']}):</b> "
        f"{esc(', '.join(meta['companies']))}</li>"
        f"<li><b>Tiers:</b> {esc(', '.join(tiers_present))} "
        "(soft → hard → hostile = progressively tighter limits).</li>"
        f"<li><b>Conditions:</b> {esc(', '.join(conds_present))}.</li>"
        f"<li><b>Reps per cell:</b> {meta['reps']}; total verdicts {meta['n']}.</li>"
        f"<li><b>Bootstrap:</b> percentile method, B={BOOTSTRAP_B}, "
        f"fixed seed random.Random({BOOTSTRAP_SEED}); 95% CIs. A difference is "
        "“significant” iff its 95% CI excludes 0.</li>"
        "<li><b>Oracle scoring:</b> a plan (lever bindings) is evaluated forward "
        "through the company's numerical model; each constraint (cash floor, "
        "burn ceiling, margin floor, hire cap, …) is checked against its tier "
        "limit; <code>fully_valid</code> = goal met AND zero hard violations. "
        "<code>overclaim</code> = the agent claimed success while "
        "<code>fully_valid</code> is false; forecast error = percent error of "
        "claimed vs. oracle final revenue.</li>"
        "</ul>"
        f"<p class='muted small'>Verdict rule: PROVED iff H1 and H2 both PASS and "
        "the mechanism check passes (or is absent); DISPROVED iff neither H1 nor "
        "H2 passes; PARTIAL otherwise.</p>"
        "</section>"
    )

    parts.append("</div></body></html>")
    return "".join(parts)


STYLE = """<style>
:root{color-scheme:dark;}
*{box-sizing:border-box;}
body{margin:0;background:#11141c;color:#e6e9ef;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
  line-height:1.5;}
.wrap{max-width:980px;margin:0 auto;padding:32px 20px 80px;}
h1{font-size:30px;margin:0 0 18px;letter-spacing:-0.01em;}
h2{font-size:20px;margin:0 0 14px;border-bottom:1px solid #2b3242;padding-bottom:6px;}
section{margin:34px 0;}
p{margin:10px 0;}
.lede{font-size:16px;color:#d4d9e3;}
.caption{color:#9aa3b2;font-size:13.5px;}
.muted{color:#9aa3b2;}
.small{font-size:12px;}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}
.verdict{border:2px solid;border-radius:12px;padding:20px 24px;margin:8px 0 22px;
  display:flex;align-items:center;gap:22px;flex-wrap:wrap;}
.verdict-badge{font-size:46px;font-weight:800;letter-spacing:0.02em;}
.verdict-line{font-size:16px;max-width:640px;}
.banner{border-radius:10px;padding:14px 18px;margin:8px 0 16px;font-size:14px;}
.banner.amber{background:#3a2c05;border:1px solid #ffc83d;color:#ffe6a3;}
.banner.green{background:#0d3320;border:1px solid #3ddc7e;color:#b6f5cf;}
.counts{display:flex;gap:14px;flex-wrap:wrap;margin:6px 0 4px;}
.counts>div{background:#1b202b;border:1px solid #2b3242;border-radius:10px;
  padding:10px 16px;text-align:center;min-width:84px;}
.counts b{display:block;font-size:22px;}
.counts span{font-size:11px;color:#9aa3b2;text-transform:uppercase;letter-spacing:0.04em;}
.chart{background:#1b202b;border:1px solid #2b3242;border-radius:10px;padding:10px;}
table{border-collapse:collapse;width:100%;font-size:13px;}
th,td{padding:7px 10px;text-align:left;border-bottom:1px solid #2b3242;vertical-align:top;}
th{color:#9aa3b2;font-weight:600;font-size:12px;text-transform:uppercase;
  letter-spacing:0.03em;}
.htable,.ctable,.analogy{background:#1b202b;border:1px solid #2b3242;border-radius:10px;
  overflow:hidden;}
.analogy{max-width:520px;}
.analogy td:first-child{color:#9aa3b2;}
.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12px;
  font-weight:700;}
.pill.ok{background:#0d3320;color:#3ddc7e;}
.pill.bad{background:#3a1414;color:#ff7b7b;}
.pill.muted2{background:#23262f;color:#9aa3b2;}
.ok{color:#3ddc7e;font-weight:700;}
.bad{color:#ff7b7b;font-weight:700;}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:16px;}
@media(max-width:760px){.cards{grid-template-columns:1fr;}}
.card{background:#1b202b;border:1px solid #2b3242;border-radius:12px;padding:16px;}
.card-head{display:flex;align-items:center;gap:10px;margin-bottom:6px;}
.card-sub{color:#9aa3b2;font-size:13px;}
.card-meta{font-size:12px;color:#9aa3b2;margin-bottom:8px;}
.rationale{font-style:italic;color:#cdd3df;font-size:13px;margin:8px 0 12px;}
.block-h{font-size:11px;text-transform:uppercase;letter-spacing:0.05em;
  color:#7f8aa0;margin:14px 0 6px;}
.card .ctable{border-radius:8px;}
.card .ctable td,.card .ctable th{padding:5px 8px;font-size:12px;}
.kv{margin-top:12px;display:grid;grid-template-columns:1fr 1fr;gap:6px 14px;}
.kv>div{display:flex;justify-content:space-between;gap:8px;font-size:12.5px;
  border-bottom:1px dotted #2b3242;padding:3px 0;}
.kv span{color:#9aa3b2;}
.methods ul{margin:8px 0;padding-left:18px;}
.methods li{margin:6px 0;font-size:13.5px;}
code{background:#23262f;padding:1px 5px;border-radius:4px;font-size:12px;}
</style>"""


# ---------------------------------------------------------------------------
# summary.json / summary.md
# ---------------------------------------------------------------------------

def _round_metrics(agg):
    out = {}
    for k, v in agg.items():
        if v is None or k == "n":
            out[k] = v
        else:
            out[k] = round(v, 6)
    return out


def build_summary(verdict, by_cond, by_ct, primary_diffs, condition_cis,
                  hyp_rows, meta, run_meta):
    cond_block = {c: _round_metrics(agg) for c, agg in by_cond.items()}
    ct_block = {
        f"{c}|{t}": _round_metrics(agg) for (c, t), agg in by_ct.items()
    }
    cis_block = {
        c: {k: list(v) for k, v in metrics.items()}
        for c, metrics in condition_cis.items()
    }
    diff_block = {
        k: {
            "point": (round(d["point"], 6) if d["point"] is not None else None),
            "ci": list(d["ci"]),
            "significant": d["significant"],
        }
        for k, d in primary_diffs.items()
    }
    hyp_block = [
        {
            "name": h["name"],
            "pass": h["pass"],
            "na": h["na"],
            "claim": h["claim"],
            "numbers": h["numbers"],
        }
        for h in hyp_rows
    ]
    return {
        "verdict": verdict,
        "data_source": meta["data_source"],
        "n": meta["n"],
        "companies": meta["companies"],
        "tiers": meta["tiers_present"],
        "conditions": meta["conds_present"],
        "reps": meta["reps"],
        "hypotheses": hyp_block,
        "primary_differences": diff_block,
        "by_condition": cond_block,
        "by_condition_tier": ct_block,
        "condition_95ci": cis_block,
        "bootstrap": {"B": BOOTSTRAP_B, "seed": BOOTSTRAP_SEED},
        "run_meta": run_meta,
    }


def build_markdown(verdict, by_cond, primary_diffs, hyp_rows, meta):
    lines = []
    lines.append(f"# {meta['title']}")
    lines.append("")
    lines.append(f"**Verdict: {verdict}**")
    lines.append("")
    if meta["data_source"] == "illustrative-reference-policy":
        lines.append(
            "> ILLUSTRATIVE -- deterministic reference policies, not live-agent "
            "results. The empirical verdict requires live-agent runs."
        )
    elif meta["data_source"] == "stub-planner":
        lines.append(
            "> STUB PLANNER -- deterministic, no LLM (pipeline smoke test). The "
            "empirical verdict needs a REAL planner (a logged-in Claude Code "
            "session -- run `claude login`)."
        )
    else:
        lines.append("> LIVE-AGENT RESULTS.")
    lines.append("")
    lines.append(
        f"N={meta['n']} verdicts, {meta['n_companies']} companies, "
        f"{len(meta['tiers_present'])} tiers, {len(meta['conds_present'])} "
        f"conditions, {meta['reps']} reps."
    )
    lines.append("")
    lines.append("## Headline metrics by condition")
    lines.append("")
    lines.append(
        "| condition | n | overclaim | valid | viol. rate | forecast MAE |"
    )
    lines.append("|---|---|---|---|---|---|")
    for c in meta["conds_present"]:
        a = by_cond[c]
        lines.append(
            f"| {c} | {a['n']} | {fmt_pct(a['overclaim_rate'],0)} | "
            f"{fmt_pct(a['valid_rate'],0)} | {fmt_pct(a['violation_rate'],0)} | "
            f"{fmt_num(a['forecast_mae'],1)} |"
        )
    lines.append("")
    lines.append("## Pre-registered hypotheses")
    lines.append("")
    for h in hyp_rows:
        if h["na"]:
            mark = "N/A"
        elif h["pass"]:
            mark = "PASS"
        else:
            mark = "FAIL"
        lines.append(f"- **{h['name']}** -- {mark}. {h['numbers']}")
    lines.append("")
    lines.append("## Primary differences (ungrounded - grounded-iterate)")
    lines.append("")
    for k, d in primary_diffs.items():
        sig = "significant" if d["significant"] else "n.s."
        point = fmt_num(d["point"], 3) if d["point"] is not None else "n/a"
        lines.append(f"- {k}: {point}, 95% CI {fmt_ci(d['ci'])} ({sig})")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Entry point (replaces the paper's argparse main() and file loaders; same computation, no I/O)
# ---------------------------------------------------------------------------

DEFAULT_TITLE = "diff-bwm-1 eval 1: operating plans against a deterministic company model"


def render_rows(rows, *, title=DEFAULT_TITLE, run_meta=None, captions="neutral"):
    """report.py main() from 'aggregate' to 'render', for already-loaded rows.

    ``rows`` must be in sorted verdict-file order. Returns {"html",
    "summary_json", "summary_md", "verdict", "summary"}; ``summary_json`` is the
    text report.py would have written.
    """
    by_cond = aggregate_by_condition(rows)
    by_ct = aggregate_by_condition_tier(rows)
    rows_by_cond = defaultdict(list)
    for r in rows:
        rows_by_cond[r.get("condition")].append(r)

    condition_cis = compute_condition_cis(rows_by_cond)
    primary_diffs = compute_primary_differences(rows_by_cond)

    hyp_rows, verdict = evaluate_hypotheses(by_cond, by_ct, primary_diffs)

    companies = sorted({r.get("company") for r in rows if r.get("company")})
    tiers_present = [t for t in TIER_ORDER if t in {r.get("tier") for r in rows}]
    conds_present = [c for c in CONDITION_ORDER if c in by_cond]
    data_sources = [r.get("data_source") for r in rows if r.get("data_source")]
    reps = (
        (run_meta or {}).get("reps")
        or (max((int(r.get("rep") or 0) for r in rows), default=-1) + 1)
    )
    if "live-agent" in data_sources and "illustrative-reference-policy" not in data_sources:
        primary_source = "live-agent"
    elif data_sources:
        primary_source = (
            "illustrative-reference-policy"
            if "illustrative-reference-policy" in data_sources
            else data_sources[0]
        )
    else:
        primary_source = (run_meta or {}).get("data_source", "unknown")

    meta = {
        "title": title,
        "n": len(rows),
        "companies": companies,
        "n_companies": len(companies),
        "tiers_present": tiers_present,
        "n_tiers": len(tiers_present),
        "conds_present": conds_present,
        "n_conditions": len(conds_present),
        "reps": reps,
        "data_sources": data_sources,
        "data_source": primary_source,
    }

    examples = pick_worked_examples(rows)

    html_doc = render_report(
        title=title,
        verdict=verdict,
        hyp_rows=hyp_rows,
        by_cond=by_cond,
        by_ct=by_ct,
        primary_diffs=primary_diffs,
        meta=meta,
        examples=examples,
        run_meta=run_meta,
        captions=captions,
    )
    summary = build_summary(
        verdict, by_cond, by_ct, primary_diffs, condition_cis, hyp_rows,
        meta, run_meta,
    )
    summary_md = build_markdown(verdict, by_cond, primary_diffs, hyp_rows, meta)
    return {
        "html": html_doc,
        "summary_json": json.dumps(summary, indent=2, sort_keys=False),
        "summary_md": summary_md,
        "verdict": verdict,
        "summary": summary,
    }
