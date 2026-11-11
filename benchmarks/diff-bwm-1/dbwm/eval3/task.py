"""The public task: goal + constraints the agent is already told, as ``task.json``.

``build_task`` is ``coupled_planner.build_task`` (coupled_planner.py:61-74)
verbatim. It carries no true-world economics, only targets that are printed in
the prompt anyway (goal, parent floors, customers-per-head, pool caps), so it
is agent-visible data: shipped under ``data/eval3/worlds/<world>/task.json``
and staged into the gen-both and gen-code sandboxes, exactly as the paper
staged it (coupled_planner.py:84; its design doc said gen-code would get
"only the brief", but the instrument staged task.json too, E23-D17).

The ``model_test`` oracle checks a model against :func:`load_task` (the shipped
file, read on the harness side), never against a ``task.json`` the agent can
edit in its sandbox -- in the paper the agent passed ``--task task.json`` from
its own writable directory, so the "fixed" test was only fixed by convention.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dbwm.core import paths
from dbwm.eval2 import load_world

EVAL_ID = "eval3"
TASK_FILE = "task.json"


def build_task(world) -> dict[str, Any]:
    """The PUBLIC goal + constraints (what the agent is already told), for model_tool's
    test mode. Carries no true-world economics -- only targets the agent knows."""
    caps = {p["id"]: [str(x) for x in p["cap"]] for p in world.pools}
    return {
        "goal": dict(world.goal),
        "parent_constraints": world.parent_constraints,
        "unit_capacity": [{"unit": u["id"], "cph": u["customers_per_head"]}
                          for u in world.units],
        "pool_caps": [
            {"id": "growth_capital", "lever": "marketing", "cap": caps["growth_capital"]},
            {"id": "eng_headcount", "lever": "hires", "cap": caps["eng_headcount"]},
        ],
    }


def render_task(world) -> str:
    """The exact bytes the paper wrote (``json.dumps(..., indent=2) + "\\n"``)."""
    return json.dumps(build_task(world), indent=2) + "\n"


def task_path(world_id: str) -> Path:
    return paths.data(EVAL_ID, "worlds", world_id, TASK_FILE)


def load_task(world_id: str) -> dict[str, Any]:
    return json.loads(task_path(world_id).read_text(encoding="utf-8"))


def write_tasks(world_ids, out_root: Path | None = None) -> list[Path]:
    """(Re)generate ``data/eval3/worlds/<w>/task.json`` from the eval-2 worlds."""
    written = []
    for w in world_ids:
        p = (Path(out_root) / "worlds" / w / TASK_FILE) if out_root else task_path(w)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_task(load_world(w)), encoding="utf-8")
        written.append(p)
    return written
