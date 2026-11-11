"""Eval 4 arms (the paper's names), tool policies and the sealed qa_check tool.

All four arms need bash: the agent must script ``market_model.xlsx`` with
python3/openpyxl from 147 PDF/DOCX/XLSX/EML/JSON sources. Bash is never
confined by Claude Code's ``--restricted``, so every arm has
``min_isolation=CONTAINER``. ``HOST_UNSAFE`` remains
possible for users who accept the risk; runs there are recorded as
non-conforming.

Information per arm (v1):

=====================  =========================================================
baseline               TASK.md (scenario + week overview + the w1_t2 brief +
                       deliverable/claims spec) and the 147 source files.
checklist-prose        + CHECKLIST.md (12 criteria rendered from the checker
                       registry, numeric anchors included).
checklist-verdict      + the SAME CHECKLIST.md + ``./qa_check`` (oracle op
                       qa_check, sealed mode "verdict": PASS/FAIL per item).
checklist-executable   + the SAME CHECKLIST.md + ``./qa_check`` (sealed mode
                       "figures": PASS/FAIL plus the figures it measured).
=====================  =========================================================

No arm receives checker source, token lists or thresholds (the paper staged
the full checker as ``_qa_impl.py`` in both tool arms --  / the eval-5
entry-12 grader-spec asymmetry). The two tool arms differ from prose only by
the runnable check and its output richness; the mode is fixed server-side and
no payload key can change it.
"""

from __future__ import annotations

from dbwm.core.types import ArmSpec, Isolation, OracleToolSpec, ToolPolicy

BUILTINS = frozenset({"read", "write", "edit", "bash", "glob", "grep"})

# Neutral description and --help: no mode, no paths, no rubric breadcrumbs.
QA_CHECK_TOOL = OracleToolSpec(
    name="qa_check",
    description=("Run the deal team's QA checklist (CHECKLIST.md) against "
                 "market_model.xlsx in the working directory and print the result."),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    cli_name="qa_check",
    cli_usage="./qa_check",
    cli_args="none",
    cli_payload_key="plan")

ARM_ORDER = ("baseline", "checklist-prose", "checklist-verdict", "checklist-executable")

ARMS: dict[str, ArmSpec] = {
    "baseline": ArmSpec(
        name="baseline",
        description=("TASK.md (scenario, week overview, w1_t2 brief, deliverable and claims "
                     "spec) plus the 147 source files; bash and file tools; no checklist, "
                     "no checker."),
        tools=ToolPolicy(builtin=BUILTINS),
        oracle_mode=None,
        min_isolation=Isolation.CONTAINER),
    "checklist-prose": ArmSpec(
        name="checklist-prose",
        description="baseline + CHECKLIST.md (the 12 QA criteria as text, numeric anchors included).",
        tools=ToolPolicy(builtin=BUILTINS),
        oracle_mode=None,
        min_isolation=Isolation.CONTAINER),
    "checklist-verdict": ArmSpec(
        name="checklist-verdict",
        description=("baseline + the same CHECKLIST.md + ./qa_check, a harness-side run of the "
                     "checklist on market_model.xlsx that prints PASS/FAIL per item only."),
        tools=ToolPolicy(builtin=BUILTINS, oracle_tools=(QA_CHECK_TOOL,)),
        oracle_mode="verdict",
        min_isolation=Isolation.CONTAINER),
    "checklist-executable": ArmSpec(
        name="checklist-executable",
        description=("baseline + the same CHECKLIST.md + ./qa_check, a harness-side run of the "
                     "checklist on market_model.xlsx that prints PASS/FAIL per item plus the "
                     "figures it measured."),
        tools=ToolPolicy(builtin=BUILTINS, oracle_tools=(QA_CHECK_TOOL,)),
        oracle_mode="figures",
        min_isolation=Isolation.CONTAINER),
}

ARMS_WITH_CHECKLIST = ("checklist-prose", "checklist-verdict", "checklist-executable")
ARMS_WITH_TOOL = ("checklist-verdict", "checklist-executable")
