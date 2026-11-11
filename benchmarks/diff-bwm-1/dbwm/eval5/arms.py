"""Eval-5 arms (the paper's condition names), tool policies and the sealed qa_check tool.

Every arm needs bash: the paper's agents had Write, Edit, Read, Bash, Glob and
Grep and used python3 to validate the YAML/JSON deliverables. Bash is never confined by Claude Code's
``--restricted``, so every arm has ``min_isolation=CONTAINER`` (BUILD-SPEC
decision 4); ``HOST_UNSAFE`` remains available and is recorded as
non-conforming.

Information per arm (v1, "information matching DOWN"; ledger E5-D02/D03):

=====================  =========================================================
baseline               TASK.md (offline instruction, scenario overview, task
                       brief, deliverables, claims.json schema) + the scenario
                       sources under shared/ and engagement/.
checklist-prose        + CHECKLIST.md (one line per check: id, title,
                       description; rendered from registry v1.0).
checklist-verdict      + the SAME CHECKLIST.md (byte-identical) + ``./qa_check``
                       (oracle op qa_check, sealed mode "verdict": PASS/FAIL per
                       item, no figures).
checklist-executable   + the SAME CHECKLIST.md + ``./qa_check`` (sealed mode
                       "figures": PASS/FAIL plus what the checker measured in
                       the submission; the grader's bands, token lists and
                       regexes are redacted).
=====================  =========================================================

No arm receives the registry (_qa_registry.json), the checker source
(_qa_impl.py) or any answer key. The paper staged the full registry and the
string-patched engine in both tool arms only (consulting_planner.py:120-151):
282 matcher literals the prose arm never saw, plus a verdict lock that
``_qa_impl.py --json`` and ``import _qa_impl`` bypassed. Those "legacy" arms
are deliberately NOT recreated (BUILD-SPEC decision 2). CHECKLIST.md itself states an in-band answer for every banded check, so
checklist-vs-baseline remains an answer-disclosure contrast (ledger E5-D04);
the eval card says so.
"""

from __future__ import annotations

from dbwm.core.types import ArmSpec, Isolation, OracleToolSpec, ToolPolicy

# The paper's tool set: --allowedTools Write,Edit,Read,Bash,Glob,Grep.
BUILTINS = frozenset({"read", "write", "edit", "bash", "glob", "grep"})

# Neutral description and --help: no mode, no paths, no rubric breadcrumbs.
QA_CHECK_TOOL = OracleToolSpec(
    name="qa_check",
    description=("Run the client review checklist (CHECKLIST.md) against the deliverables "
                 "in the working directory and print the result."),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    cli_name="qa_check",
    cli_usage="./qa_check",
    cli_args="none",
    cli_payload_key="plan",
)

ARM_ORDER = ("baseline", "checklist-prose", "checklist-verdict", "checklist-executable")
ARMS_WITH_CHECKLIST = ("checklist-prose", "checklist-verdict", "checklist-executable")
ARMS_WITH_TOOL = ("checklist-verdict", "checklist-executable")

ARMS: dict[str, ArmSpec] = {
    "baseline": ArmSpec(
        name="baseline",
        description=("TASK.md (overview, brief, deliverables, claims schema) and the scenario "
                     "sources; file tools and bash; no checklist, no checker."),
        tools=ToolPolicy(builtin=BUILTINS),
        oracle_mode=None,
        min_isolation=Isolation.CONTAINER,
    ),
    "checklist-prose": ArmSpec(
        name="checklist-prose",
        description="baseline + CHECKLIST.md (the client review checklist as text).",
        tools=ToolPolicy(builtin=BUILTINS),
        oracle_mode=None,
        min_isolation=Isolation.CONTAINER,
    ),
    "checklist-verdict": ArmSpec(
        name="checklist-verdict",
        description=("baseline + the same CHECKLIST.md + ./qa_check, a harness-side run of the "
                     "checklist on the deliverables that prints PASS/FAIL per item only."),
        tools=ToolPolicy(builtin=BUILTINS, oracle_tools=(QA_CHECK_TOOL,)),
        oracle_mode="verdict",
        min_isolation=Isolation.CONTAINER,
    ),
    "checklist-executable": ArmSpec(
        name="checklist-executable",
        description=("baseline + the same CHECKLIST.md + ./qa_check, a harness-side run of the "
                     "checklist on the deliverables that prints PASS/FAIL per item plus the "
                     "figures it measured in the submission."),
        tools=ToolPolicy(builtin=BUILTINS, oracle_tools=(QA_CHECK_TOOL,)),
        oracle_mode="figures",
        min_isolation=Isolation.CONTAINER,
    ),
}
