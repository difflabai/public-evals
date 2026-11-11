"""Eval-6 D1 arms and their sandbox staging.

Both arms are the paper's two live conditions with the leaks removed
(scope decision 2: only fixed arms are runnable). Both need bash (the task is
statistical identification from a CSV with Python), so both require
Isolation.CONTAINER (decision 4); the paper ran them on the host with the
operator's settings, which exposed WebFetch/WebSearch and the harness tree
(ledger E6-20, H09).

  unaided  (paper "self-supply")  public/history.csv, public/task.json, TASK.md.
  aided    (paper "fitted-model") the same plus fitted_model.json (AIDED_v2:
           seed-free), model_sim.py and heldout_sim.py. The paper staged
           latent_engine.py and the seed-carrying AIDED_v1 model instead, which
           allowed a bit-exact replay of the true latent path (E6-01).

There is no harness-served oracle: the unaided arm has no tool beyond bash, and
the aided arm's simulator is the agent's own fitted model, run inside the
sandbox exactly as in the paper. The true-dynamics oracle scores the committed
plan after the attempt, from private/.
"""

from __future__ import annotations

import re

from dbwm.core.types import ArmSpec, Isolation, StagedFile, StagingManifest, ToolPolicy

from . import _paths

D1_BUILTINS = frozenset({"read", "write", "edit", "bash", "glob", "grep"})

UNAIDED = ArmSpec(
    name="unaided",
    description=("Self-supply: the world's public history.csv and task.json plus the brief; "
                 "bash/python/numpy; no model, no parameters, no latent state, no oracle."),
    tools=ToolPolicy(builtin=D1_BUILTINS),
    oracle_mode=None,
    query_budget=None,
    min_isolation=Isolation.CONTAINER,
    canonical=True,
)

AIDED = ArmSpec(
    name="aided",
    description=("Fitted model: everything the unaided arm gets plus a seed-free calibrated model "
                 "(correct coupling and elasticity signs, ~8%-noised magnitudes, latent nowcast) "
                 "and its simulator (model_sim.py, heldout_sim.py); no oracle."),
    tools=ToolPolicy(builtin=D1_BUILTINS),
    oracle_mode=None,
    query_budget=None,
    min_isolation=Isolation.CONTAINER,
    canonical=True,
)

ARMS = {UNAIDED.name: UNAIDED, AIDED.name: AIDED}

PUBLIC_FILES = ("history.csv", "task.json")
AIDED_TOOLS = ("model_sim.py", "heldout_sim.py")


def _natural_key(name: str):
    m = re.fullmatch(r"w(\d+)", name)
    return (0, int(m.group(1)), name) if m else (1, 0, name)


def list_instances() -> list[str]:
    """World ids present under data/eval6/worlds (default: the 11 certified public worlds)."""
    root = _paths.data("worlds")
    if not root.is_dir():
        return []
    names = [p.name for p in root.iterdir() if (p / "public" / "task.json").is_file()]
    return sorted(names, key=_natural_key)


def staged_files(world_id: str, arm: str) -> list[StagedFile]:
    if arm not in ARMS:
        raise KeyError(f"unknown eval6 arm {arm!r}; arms: {sorted(ARMS)}")
    files = [StagedFile(dst=f"public/{f}", src=_paths.world_public(world_id, f))
             for f in PUBLIC_FILES]
    if arm == "aided":
        files.append(StagedFile(dst="fitted_model.json", src=_paths.world_aided_v2(world_id)))
        files.extend(StagedFile(dst=t, src=_paths.aided_tool(t)) for t in AIDED_TOOLS)
    for f in files:
        if not f.src.is_file():
            raise FileNotFoundError(f"eval6 staging: missing {f.src.name} for {world_id}/{arm}")
    return files


def manifest(world_id: str, arm: str, task_md: str) -> StagingManifest:
    return StagingManifest(files=staged_files(world_id, arm), task_md=task_md, writable_dirs=[])
