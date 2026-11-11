"""Package-relative locations of data, scorer-only files and runs.

Why this module exists: the paper harness located everything with
``Path(__file__).parents[N] / "examples" / "data"`` (oracle.py, batch.py,
run_confirmatory.py:37-40, briefcase_planner.py, consulting_planner.py), which
broke as soon as the scripts were copied anywhere else and put the answer keys
on a path the agent could guess. Here every root is derived from the installed
package location once, and each can be overridden by an environment variable so
a user can keep scorer-only material outside the checkout.

Roots (defaults relative to the package checkout PKG):

    DATA       PKG/data         agent-visible files            DBWM_DATA_DIR
    PRIVATE    PKG/private      scorer/oracle-only files       DBWM_PRIVATE_DIR
    RUNS       PKG/runs         run output (gitignored)        DBWM_RUNS_DIR
    SANDBOXES  system temp dir  per-attempt agent sandboxes    DBWM_SANDBOX_ROOT

The module-level constants are evaluated at import time; the functions re-read
the environment on every call (tests and the sidecar rely on that).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = PKG_ROOT / "dbwm"

_ENV = {
    "data": "DBWM_DATA_DIR",
    "private": "DBWM_PRIVATE_DIR",
    "runs": "DBWM_RUNS_DIR",
}

# Files whose presence in a directory or any ancestor means "this is a repo or
# a Claude Code project": an agent started below it could pick up instructions,
# settings or git history that are not part of the regime.
REPO_MARKERS = (".git", "CLAUDE.md", "CLAUDE.local.md")


def _root(kind: str) -> Path:
    override = os.environ.get(_ENV[kind])
    if override:
        return Path(override).expanduser().resolve()
    return PKG_ROOT / kind


def missing_roots() -> list[str]:
    """Roots (data, private) that exist neither at PKG_ROOT nor via their override.

    diff-bwm-1 runs from a checkout: a non-editable ``pip install .`` copies only
    the ``dbwm`` package into site-packages, so PKG_ROOT/data etc. do not exist
    there. The CLI refuses to start with a clear message instead of failing later
    with a missing-file error (fresh-clone check, 2026-10-03).
    """
    return [kind for kind in ("data", "private") if not _root(kind).is_dir()]


def data_root() -> Path:
    return _root("data")


def private_root() -> Path:
    return _root("private")


def runs_root() -> Path:
    return _root("runs")


def sandbox_root() -> Path:
    """Parent directory for fresh sandboxes (DBWM_SANDBOX_ROOT or the system temp dir)."""
    override = os.environ.get("DBWM_SANDBOX_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(tempfile.gettempdir()).resolve()


def eval_name(eval_id: str | int) -> str:
    """Normalize ``2``, ``"2"`` and ``"eval2"`` to ``"eval2"``; other names pass through."""
    s = str(eval_id).strip()
    if s.isdigit():
        return f"eval{s}"
    return s


def data(eval_id: str | int, *parts: str) -> Path:
    """Path under ``data/<evalN>/`` (agent-visible material)."""
    return data_root().joinpath(eval_name(eval_id), *parts)


def private(eval_id: str | int, *parts: str) -> Path:
    """Path under ``private/<evalN>/`` (scorer and oracle only; never staged)."""
    return private_root().joinpath(eval_name(eval_id), *parts)


def eval_package_dir(eval_id: str | int) -> Path:
    return PACKAGE_DIR / eval_name(eval_id)


DATA = data_root()
PRIVATE = private_root()
RUNS = runs_root()


class InsideRepoError(RuntimeError):
    """A sandbox (or other agent-facing directory) sits below a repo or Claude project."""


def repo_markers_above(p: Path) -> list[Path]:
    """Every repo/project marker found in ``p`` or any of its ancestors.

    Checked: ``.git`` (file or dir), ``CLAUDE.md``, ``CLAUDE.local.md`` and
    ``.claude/CLAUDE.md``. The user's own ``~/.claude/CLAUDE.md`` is the
    global user memory, not a project marker, so it is skipped for the home
    directory itself (the claude_code adapter's ``--restricted`` flag is what
    keeps that file away from the agent).
    """
    p = Path(p).resolve()
    home = Path.home().resolve()
    found: list[Path] = []
    for d in (p, *p.parents):
        for name in REPO_MARKERS:
            m = d / name
            if os.path.lexists(m):
                found.append(m)
        if d != home and (d / ".claude" / "CLAUDE.md").exists():
            found.append(d / ".claude" / "CLAUDE.md")
    return found


def assert_outside_repos(p: Path) -> None:
    """Raise InsideRepoError if ``p`` is inside a git repo or a Claude Code project.

    The paper's harness started agents with cwd inside the harness repo (coupled_planner.py:359),
    which made prior-run outputs, the oracle and project settings reachable
    (ledger N04, N06, N07). Every sandbox is checked with this before launch.
    """
    found = repo_markers_above(p)
    if found:
        listed = ", ".join(str(f) for f in found[:5])
        raise InsideRepoError(
            f"{p} is inside a repository or Claude Code project (found {listed}); "
            "set DBWM_SANDBOX_ROOT to a directory outside any repo")
