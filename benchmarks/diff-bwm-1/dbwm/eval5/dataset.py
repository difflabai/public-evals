"""Eval-5 dataset access: manifest, registries (v0.1 / v0.1-fixed / v1.0), answer keys, sources.

Layout (package-relative, resolved through dbwm.core.paths so every root can be
moved with DBWM_DATA_DIR / DBWM_PRIVATE_DIR):

    data/eval5/                         AGENT-VISIBLE
      manifest.json                     scenario ids, deliverable names, headline-figure keys,
                                        checklist ids (never staged; read by the harness)
      scenarios/<id>/scenario_overview.md, tasks/t1.md, sources/{shared,engagement}/**
      scenarios/<id>/CHECKLIST.md       the checklist text the three checklist arms receive
                                        (rendered from registry_v1.0; a test keeps it in sync)
    private/eval5/                      SCORER / ORACLE ONLY (+ CANARY.txt)
      scenarios/<id>/registry_v0.1.jsonl   the as-run registry (ruleset "as-run"; auto specs
                                           byte-identical to the paper's, see as-run-pins.json)
      solvara-market-access/registry_v0.1-fixed.jsonl
                                           v0.1 with the authors' 2026-10-03 SOLVARA-A-13 line (ruleset
                                           "paper"; the other three scenarios have no fixed file and
                                           read registry_v0.1.jsonl under "paper")
      scenarios/<id>/registry_v1.0.jsonl  the release registry (ruleset "v1")
      scenarios/<id>/answer_key/{blueprint,anchors,contradictions}.json
      probes.json                          golden probe suite (validate.py)

The registry a ruleset uses: paper -> v0.1-fixed (falling back to v0.1 where a
scenario has no fixed file), as-run -> v0.1, v1 -> v1.0. Answer keys are shared
(the anchor bands and headline keys did not change between versions; a test
pins them against the as-run hashes in as-run-pins.json).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from dbwm.core import paths as _paths

from . import engine

EVAL_ID = "eval5"
SCENARIOS = ("solvara-market-access", "meridian-soc2", "castellan-software", "bluestem-demand")
RULESETS = engine.RULESETS
REGISTRY_FILE = {engine.RULESET_PAPER: "registry_v0.1-fixed.jsonl", engine.RULESET_AS_RUN: "registry_v0.1.jsonl",
                 engine.RULESET_V1: "registry_v1.0.jsonl"}
# A scenario without its own file for a ruleset reads this one instead (paper: only
# solvara-market-access carries a fixed line; the other three are v0.1 verbatim).
REGISTRY_FALLBACK = {engine.RULESET_PAPER: "registry_v0.1.jsonl"}
REGISTRY_VERSION = {engine.RULESET_PAPER: "0.1+A13-fix", engine.RULESET_AS_RUN: "0.1", engine.RULESET_V1: "1.0"}
# Every registry file shipped under private/eval5/scenarios/ (scorer-only names).
REGISTRY_FILES = tuple(dict.fromkeys([*REGISTRY_FILE.values(), *REGISTRY_FALLBACK.values()]))


def data(*parts: str) -> Path:
    return _paths.data(EVAL_ID, *parts)


def private(*parts: str) -> Path:
    return _paths.private(EVAL_ID, *parts)


def scenario_dir(sid: str) -> Path:
    return data("scenarios", sid)


def private_scenario_dir(sid: str) -> Path:
    return private("scenarios", sid)


def manifest() -> dict[str, Any]:
    return json.loads(data("manifest.json").read_text())


def scenario_meta(sid: str) -> dict[str, Any]:
    for s in manifest()["scenarios"]:
        if s["id"] == sid:
            return s
    raise KeyError(f"unknown eval5 scenario {sid!r}")


def deliverable_names(sid: str) -> tuple[str, str]:
    m = scenario_meta(sid)
    return m["prose_deliverable"], m["structured_deliverable"]


def registry_path(sid: str, ruleset: str) -> Path:
    try:
        p = private_scenario_dir(sid) / REGISTRY_FILE[ruleset]
    except KeyError:
        raise ValueError(f"unknown ruleset {ruleset!r} (eval 5 rulesets: {', '.join(RULESETS)})") from None
    if not p.exists() and ruleset in REGISTRY_FALLBACK:
        return private_scenario_dir(sid) / REGISTRY_FALLBACK[ruleset]
    return p


def raw_checks(sid: str, ruleset: str) -> list[dict[str, Any]]:
    """Full registry rows (check_id, descriptions, score text, auto, ...)."""
    out = []
    for line in registry_path(sid, ruleset).read_text().splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def registry(sid: str, ruleset: str) -> list[dict[str, Any]]:
    """[{id, title, criteria, auto}] as the engine consumes it."""
    return engine.load_registry(registry_path(sid, ruleset))


def answer_key(sid: str, name: str) -> Any:
    return json.loads((private_scenario_dir(sid) / "answer_key" / f"{name}.json").read_text())


def anchors(sid: str) -> dict[str, Any]:
    return answer_key(sid, "anchors")


def blueprint(sid: str) -> dict[str, Any]:
    return answer_key(sid, "blueprint")


def headline_figures(sid: str) -> list[str]:
    """Headline key-figure names (from the manifest, so prompt building never opens answer_key/)."""
    return list(scenario_meta(sid)["headline_figures"])


def checklist_ids(sid: str) -> list[str]:
    return list(scenario_meta(sid)["checklist_ids"])


def checklist_text(sid: str) -> str:
    return (scenario_dir(sid) / "CHECKLIST.md").read_text()


def overview(sid: str) -> str:
    return (scenario_dir(sid) / "scenario_overview.md").read_text()


def task_brief(sid: str) -> str:
    return (scenario_dir(sid) / "tasks" / "t1.md").read_text()


def source_files(sid: str) -> list[Path]:
    """Every agent-visible source file (shared/ and engagement/), sorted."""
    root = scenario_dir(sid) / "sources"
    return sorted(p for p in root.rglob("*") if p.is_file())


@lru_cache(maxsize=None)
def _as_run_pins() -> dict[str, Any]:
    p = private("as-run-pins.json")
    return json.loads(p.read_text()) if p.exists() else {}


def as_run_pins() -> dict[str, Any]:
    return _as_run_pins()
