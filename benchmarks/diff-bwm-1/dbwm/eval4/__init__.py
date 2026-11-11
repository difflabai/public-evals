"""Eval 4 -- AA-Briefcase-Lite transfer (w1_t2, ``market_model.xlsx``).

Does a runnable QA checklist help an agent build a better spreadsheet than the
same checklist as prose? Four arms (the paper's names): ``baseline``,
``checklist-prose``, ``checklist-verdict``, ``checklist-executable``. The task,
its 147 source files and the 12-item rubric are Artificial Analysis's
AA-Briefcase-Lite (Apache-2.0), fetched at a pinned revision (``dbwm fetch
eval4``), never redistributed.

Modules: ``dataset`` (pinned fetch + verification), ``checker`` (the
deterministic 12-check registry, byte-for-byte the paper's), ``arms``,
``prompt`` (TASK.md), ``oracle`` (the sealed harness-side ``qa_check``),
``score`` (deterministic scoring, rulesets ``paper`` and ``v1``), ``judge``
(the dataset's LLM-judge protocol, ``judge_run``), ``report`` (new runs),
``selftest``, ``stub``.
"""

from __future__ import annotations

import argparse
from typing import Any

from .evaldef import EVAL, EVAL_ID, SCORER_ONLY_NAMES, Eval4  # noqa: F401


def _configure_fetch(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--from-dir", help="install from a local copy of the dataset instead of the network")
    ap.add_argument("--force", action="store_true", help="re-fetch files that already verify")
    ap.add_argument("--verify-only", action="store_true", help="only verify what is installed")


def _fetch(args: argparse.Namespace) -> int:
    from . import dataset

    if args.verify_only:
        return dataset.main(["verify"])
    argv = ["fetch"] + (["--from-dir", args.from_dir] if args.from_dir else []) + (["--force"] if args.force else [])
    return dataset.main(argv)


def _configure_judge(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--run", required=True, help="run directory (runs/<run_id>)")
    ap.add_argument("--adapter", default=None, choices=["claude_cli", "anthropic_api", "mock-pass", "mock-fail"],
                    help="judge adapter (default: the run regime's judge_adapter, else claude_cli); "
                         "mock-pass / mock-fail are offline pipeline checks whose output is never a result")
    ap.add_argument("--model", default=None, help="judge model (default: the regime's judge_model)")
    ap.add_argument("--passes", type=int, default=None, help="judge passes per workbook (default 3)")
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--style", default="v1", choices=["v1", "paper"])
    ap.add_argument("--force", action="store_true", help="re-judge passes that already exist")
    ap.add_argument("--auth", default="session", help="claude_cli auth mode (session|api_key|oauth_token|...)")


def _judge(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    from dbwm.core import judge as core_judge

    from . import judge as j

    run_dir = Path(args.run).resolve()
    regime: dict[str, Any] = {}
    mp = run_dir / "manifest.json"
    if mp.is_file():
        regime = (json.loads(mp.read_text(encoding="utf-8")).get("regime") or {})
    name = args.adapter or regime.get("judge_adapter") or "claude_cli"
    raw_dir = run_dir / "judge_raw"
    if name == "mock-pass":
        adapter = core_judge.MockJudge(lambda s, u, sc, m: {"passed": True, "reasoning": "mock"}, out_dir=raw_dir)
    elif name == "mock-fail":
        adapter = core_judge.MockJudge(lambda s, u, sc, m: {"passed": False, "reasoning": "mock"}, out_dir=raw_dir)
    elif name == "claude_cli":
        adapter = core_judge.ClaudeCliJudge(raw_dir, auth_mode=args.auth)
    else:
        adapter = core_judge.get_judge(name, raw_dir)
    passes = args.passes or int((regime.get("extra") or {}).get("judge_passes") or j.DEFAULT_PASSES)
    model = args.model or ("mock" if name.startswith("mock") else None)  # never label a mock as a real judge
    summary = j.judge_run(run_dir, adapter, model=model, passes=passes, retries=args.retries,
                          force=args.force, style=args.style, concurrency=args.concurrency)
    print(json.dumps({"cells_judged": len(summary["cells"]), "not_judged": summary["not_judged"],
                      "model": summary["model"], "adapter": summary["judge_adapter"]}, indent=2, sort_keys=True))
    return 0


EXTRA_COMMANDS = {
    "fetch": (_configure_fetch, _fetch),
    "eval4-judge": (_configure_judge, _judge),
}
