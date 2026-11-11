"""Adapter registry: name -> adapter factory.

``openai_compat`` is owned by dbwm.core.adapters.openai_compat (core-analysis)
and imported lazily so a missing or broken module never breaks the others.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable

from dbwm.core.adapters.base import AgentAdapter


def _claude_code(**kw: Any) -> AgentAdapter:
    from dbwm.core.adapters.claude_code import ClaudeCodeAdapter
    return ClaudeCodeAdapter(claude_bin=kw.get("claude_bin"))


def _command(**kw: Any) -> AgentAdapter:
    from dbwm.core.adapters.command import CommandAdapter
    return CommandAdapter(command=kw.get("command"))


def _stub(**kw: Any) -> AgentAdapter:
    from dbwm.core.adapters.stub import StubAdapter
    return StubAdapter(eval_def=kw.get("eval_def"))


def _replay(**kw: Any) -> AgentAdapter:
    from dbwm.core.adapters.replay import ReplayAdapter
    return ReplayAdapter(source=kw.get("replay_dir"))


def _openai_compat(**kw: Any) -> AgentAdapter:
    mod = importlib.import_module("dbwm.core.adapters.openai_compat")
    for attr in ("OpenAICompatAdapter", "OpenAICompatibleAdapter", "Adapter"):
        cls = getattr(mod, attr, None)
        if cls is not None:
            try:
                return cls(**{k: v for k, v in kw.items() if k in ("base_url", "model", "api_key_env")})
            except TypeError:
                return cls()
    factory = getattr(mod, "make_adapter", None)
    if factory is not None:
        return factory(**kw)
    raise ImportError("dbwm.core.adapters.openai_compat exposes no adapter class")


ADAPTERS: dict[str, Callable[..., AgentAdapter]] = {
    "claude_code": _claude_code,
    "command": _command,
    "stub": _stub,
    "replay": _replay,
    "openai_compat": _openai_compat,
}

# Adapters that run inside this Python process; they ignore the isolation level.
IN_PROCESS = frozenset({"stub", "replay"})


def get_adapter(name: str, **kwargs: Any) -> AgentAdapter:
    try:
        factory = ADAPTERS[name]
    except KeyError:
        raise ValueError(f"unknown adapter {name!r}; choose one of {', '.join(ADAPTERS)}") from None
    return factory(**kwargs)


def available() -> list[str]:
    return list(ADAPTERS)
