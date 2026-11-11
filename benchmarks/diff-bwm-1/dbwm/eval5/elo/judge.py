"""Pairwise ELO judging of eval-5 submissions through the core judge interface.

Port of scripts/difflab_consulting/pairwise_judge.py. The judge sees ONLY the
scenario overview, the task brief and the two submissions' deliverables (prose
+ structured, each capped at 45,000 characters). It never sees claims.json,
the checklist, the registry, the sources or the answer key. Every pair is
judged in BOTH presentation orders so position bias is measurable.

What changed (change-ledger/eval5.md, ledger E5-D16/D17):

* the judge is a ``dbwm.core.judge`` adapter: ``claude_cli`` (tool-less
  ``claude -p --tools "" --restricted ...``, prompt on stdin, cwd = a fresh
  empty directory, stream saved, any tool use = judge_error), ``anthropic_api``
  (forced structured output) or ``mock``. The harness's judge ran ``claude -p
  <prompt as argv> --allowedTools ""`` from the operator's cwd, which does not
  withhold file tools;
* trials are discovered with an explicit ``competitor_key`` (``condition``,
  ``agent_model``, ``competitor`` or ``dir``) and pairs are formed within the
  same (scenario, rep): the harness's discover_trials fell back to the grand-
  parent directory name, which on the eval-5 ``<cond>/<scenario>/rep<k>``
  layout is the scenario, so every rep of a scenario collapsed to one key and
  no comparison was written, with exit 0;
* the default judge model is pinned to ``claude-sonnet-4-6`` (the published
  grader; the snapshot default was ``claude-sonnet-5``); ``passes`` > 1 runs a
  panel; failed judgments are recorded in judge_failures.jsonl and reported,
  never silently skipped; the dead ``--max-usd`` cap is gone (the API adapter
  reports real cost);
* the output instruction no longer tells the model to "call
  record_comparison" (that tool does not exist under the core adapters; the
  structured output channel carries the same fields).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import dataset as D

DEFAULT_JUDGE_MODEL = "claude-sonnet-4-6"
MAX_DOC_CHARS = 45_000
MAX_CONTEXT_CHARS = 20_000
DIMENSIONS = ("accuracy_grounding", "completeness", "actionability",
              "internal_consistency", "clarity_professionalism")

JUDGE_SYSTEM = """\
You are an exacting principal at a top consulting firm reviewing two competing
versions of the same client deliverable package. You will pick the one you
would actually send to the client.

Judge ONLY what is in the submissions against the engagement brief. Reward:
grounding in the engagement's source material (specific figures, named
sources, resolved data conflicts); completeness against every stated
requirement (including the machine-readable deliverable's required structure);
actionable, decision-ready recommendations; internal consistency between the
prose and the structured artifact; clear professional writing.

Punish: invented or ungrounded figures; generic consulting boilerplate that
could have been written without reading the sources; requirements silently
skipped; contradictions between the two files; padding. Longer is NOT better —
judge substance per requirement, not volume. Do not reward confident tone;
reward evidence.

Call record_comparison exactly once. Use "tie" only when the two are genuinely
indistinguishable in send-to-client quality."""

# v1: identical except the last paragraph (no record_comparison tool exists).
JUDGE_SYSTEM_V1 = JUDGE_SYSTEM.replace(
    "Call record_comparison exactly once.",
    "Record your judgment exactly once, in the required output format.")

JUDGE_USER = """\
# Engagement context

<scenario_overview>
{overview}
</scenario_overview>

<task_brief>
{task}
</task_brief>

# Submission A

{sub_a}

# Submission B

{sub_b}

Compare Submission A and Submission B as complete deliverable packages for
this engagement, then record your judgment."""

COMPARISON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "winner": {"type": "string", "enum": ["A", "B", "tie"]},
        "dimensions": {
            "type": "object",
            "properties": {d: {"type": "string", "enum": ["A", "B", "tie"]} for d in DIMENSIONS},
            "required": list(DIMENSIONS),
        },
        "reasoning": {"type": "string"},
    },
    "required": ["winner", "dimensions", "reasoning"],
}


# ---------------------------------------------------------------------------
# Trials
# ---------------------------------------------------------------------------


@dataclass
class TrialRef:
    scenario: str
    rep: int | None
    competitor: str
    deliverables_dir: Path
    checker_passed: int | None = None
    source: str = ""


@dataclass
class Discovery:
    trials: dict[tuple[str, int | None], dict[str, TrialRef]] = field(default_factory=dict)
    duplicates: list[str] = field(default_factory=list)

    @property
    def n(self) -> int:
        return sum(len(v) for v in self.trials.values())


COMPETITOR_KEYS = ("condition", "agent_model", "competitor", "dir")


def discover_trials(batch, competitor_key: str = "condition", scenario_filter: str | None = None) -> Discovery:
    """Find trials under ``batch`` and group them by (scenario, rep) -> {competitor: TrialRef}.

    ``batch`` is a dbwm run dir (``cells/<cell>/attempt-<k>/verdict.json`` +
    ``deliverables/``, scored attempts only).
    """
    if competitor_key not in COMPETITOR_KEYS:
        raise ValueError(f"competitor_key must be one of {COMPETITOR_KEYS}")
    batch = Path(batch)
    disc = Discovery()

    def add(ref: TrialRef, label: str):
        if scenario_filter and ref.scenario != scenario_filter:
            return
        slot = disc.trials.setdefault((ref.scenario, ref.rep), {})
        if ref.competitor in slot:
            disc.duplicates.append(label)
            return
        slot[ref.competitor] = ref

    for vp in sorted(batch.rglob("verdict*.json")):
        try:
            v = json.loads(vp.read_text())
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(v, dict) or v.get("schema") != "dbwm.verdict/1" or v.get("eval") != "eval5":
            continue
        if not v.get("scored") or vp.name != "verdict.json":
            continue
        cell = v["cell"]
        comp = {"condition": cell["arm"], "agent_model": v.get("model_requested"), "competitor": cell["arm"],
                "dir": vp.parent.parent.name}[competitor_key]
        add(TrialRef(cell["instance"], cell["rep"], str(comp), vp.parent / "deliverables",
                     (v.get("details") or {}).get("checker_passed"), "dbwm-run"),
            vp.parent.relative_to(batch).as_posix())
    return disc


def serialize_submission(trial_dir, scenario: str) -> str:
    parts = []
    for fname in D.deliverable_names(scenario):
        p = Path(trial_dir) / fname
        if p.exists() and not p.is_symlink():
            body = p.read_text(errors="replace")
            if len(body) > MAX_DOC_CHARS:
                body = body[:MAX_DOC_CHARS] + "\n[... truncated ...]"
            parts.append(f"### File: {fname}\n```\n{body}\n```")
        else:
            parts.append(f"### File: {fname}\nNOT SUBMITTED.")
    return "\n\n".join(parts)


def scenario_context(sid: str, source: str = "v1") -> tuple[str, str]:
    """(overview, task brief) the judge sees: ``v1`` = dataset v1.0."""
    if source == "v1":
        return D.overview(sid), D.task_brief(sid)
    raise ValueError(f"unknown context source {source!r}")


def build_user(sid: str, sub_a: str, sub_b: str, source: str = "v1") -> str:
    ov, tk = scenario_context(sid, source)
    return JUDGE_USER.format(overview=ov[:MAX_CONTEXT_CHARS], task=tk[:MAX_CONTEXT_CHARS], sub_a=sub_a, sub_b=sub_b)


# ---------------------------------------------------------------------------
# Running pairs
# ---------------------------------------------------------------------------


def mock_checker_decision(a: TrialRef, b: TrialRef) -> dict[str, Any]:
    """pairwise_judge.mock_judge: the higher deterministic-checker score wins (pipeline tests only)."""
    pa = a.checker_passed if a.checker_passed is not None else -1
    pb = b.checker_passed if b.checker_passed is not None else -1
    w = "A" if pa > pb else "B" if pb > pa else "tie"
    return {"winner": w, "dimensions": {d: w for d in DIMENSIONS}, "reasoning": f"mock judge: checker {pa} vs {pb}"}


class PairwiseRunner:
    """Round-robin within each (scenario, rep), both presentation orders, ``passes`` per order.

    ``adapter`` is any dbwm.core.judge adapter (``judge(system, user, schema, model,
    label=...) -> JudgeResult``). ``mock_checker=True`` wraps a core MockJudge whose
    responses come from mock_checker_decision for the pair being judged.
    """

    def __init__(self, adapter: Any = None, model: str = DEFAULT_JUDGE_MODEL, *, passes: int = 1,
                 context_source: str = "v1", mock_checker: bool = False):
        if mock_checker:
            from dbwm.core.judge import MockJudge
            adapter = MockJudge(lambda system, user, schema, model_: mock_checker_decision(*self._current))
        if adapter is None:
            raise ValueError("a judge adapter is required (or mock_checker=True)")
        self.adapter = adapter
        self.model = model
        self.passes = passes
        self.context_source = context_source
        self._current: tuple[TrialRef, TrialRef] | None = None
        self.system = JUDGE_SYSTEM_V1

    def schedule(self, disc: Discovery):
        for (scen, rep), comps in sorted(disc.trials.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
            names = sorted(comps)
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    for first, second in ((names[i], names[j]), (names[j], names[i])):
                        for k in range(self.passes):
                            yield scen, rep, first, second, k, comps

    def run(self, disc: Discovery, out_dir, *, force: bool = False) -> dict[str, Any]:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "comparisons.jsonl"
        fail_path = out_dir / "judge_failures.jsonl"
        done = set()
        if out_path.exists() and not force:
            for line in out_path.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    done.add((r["scenario"], r.get("rep"), r["first"], r["second"], r.get("pass", 0)))
        written = failed = skipped = 0
        with out_path.open("a") as fh, fail_path.open("a") as ff:
            for scen, rep, first, second, k, comps in self.schedule(disc):
                if (scen, rep, first, second, k) in done:
                    skipped += 1
                    continue
                a, b = comps[first], comps[second]
                self._current = (a, b)
                user = build_user(scen, serialize_submission(a.deliverables_dir, scen),
                                  serialize_submission(b.deliverables_dir, scen), self.context_source)
                label = f"{scen}__r{rep}__{first}__vs__{second}__p{k}"
                res = self.adapter.judge(self.system, user, COMPARISON_SCHEMA, self.model, label=label)
                label_map = {"A": first, "B": second, "tie": "tie"}
                j = res.parsed if res.ok else None
                bad = None
                if j is None:
                    bad = res.error or "no judgment"
                elif j.get("winner") not in label_map or any(v not in label_map for v in (j.get("dimensions") or {}).values()):
                    bad = "malformed judgment"
                if bad:
                    failed += 1
                    ff.write(json.dumps({"scenario": scen, "rep": rep, "first": first, "second": second, "pass": k,
                                         "error": bad, "error_class": getattr(res, "error_class", None),
                                         "tool_use_count": getattr(res, "tool_use_count", None)}) + "\n")
                    continue
                row = {"scenario": scen, "rep": rep, "first": first, "second": second, "pass": k,
                       "winner": label_map[j["winner"]],
                       "dimensions": {d: label_map[v] for d, v in (j.get("dimensions") or {}).items()},
                       "judge": getattr(self.adapter, "name", "?"), "judge_model": self.model,
                       "cost_usd": getattr(res, "cost_usd", None),
                       "raw": res.raw_path.name if getattr(res, "raw_path", None) else None,
                       "reasoning": str(j.get("reasoning", ""))[:2000]}
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                written += 1
        return {"written": written, "failed": failed, "skipped": skipped, "trials": disc.n,
                "duplicates": disc.duplicates, "comparisons": str(out_path.name),
                "failures": str(fail_path.name)}
