"""RT-S: static leak checks over every eval x arm x instance sandbox.

For each cell the sandbox is built exactly as the runner builds it
(``EVAL.stage`` -> ``sandbox.build_sandbox``) and checked independently of
``sandbox.precheck`` (which is also run): listing == manifest, no canary, no
answer-key value, no verbatim copy of any private/ file, no
scorer-only name, no host path / '..' / '~/' in TASK.md or the prompt, the
sandbox is outside any repository, and the oracle shims / mcp.json carry no
paths, no mode and are identical across arms that differ only in mode.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import pytest

from dbwm.core import canary, paths, sandbox
from dbwm.core.types import CANARY_GUID, CANARY_STRING, Cell

from .conftest import EVAL_IDS, load_eval

DEFAULT_SCORER_ONLY = ("reference.json", "world.json", "ANSWER_KEY", "checks.jsonl", "answer_key",
                       "grader", "private", "CANARY.txt")


def _cells(eval_id):
    _mod, ev = load_eval(eval_id)
    return [(eval_id, inst, arm) for arm in ev.arms for inst in ev.instances()]


ALL_CELLS = [c for e in EVAL_IDS for c in _cells(e)]


def _hashes_under(root: Path) -> dict[str, str]:
    out = {}
    if not root.is_dir():
        return out
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            p = Path(dirpath) / fn
            if p.is_file() and p.stat().st_size <= 64 * 1024 * 1024:
                out[hashlib.sha256(p.read_bytes()).hexdigest()] = str(p.relative_to(paths.PKG_ROOT))
    return out


_PRIVATE_HASHES: dict[str, dict[str, str]] = {}


def private_hashes(eval_id: str) -> dict[str, str]:
    if eval_id not in _PRIVATE_HASHES:
        h = _hashes_under(paths.private_root() / eval_id)
        _PRIVATE_HASHES[eval_id] = h
    return _PRIVATE_HASHES[eval_id]


def host_markers() -> list[str]:
    # Operator-path markers are assembled at run time so `dbwm hygiene` (which greps
    # the shipped tree for these very strings) stays clean.
    ms = {str(paths.PKG_ROOT), str(paths.private_root()), str(Path.home()),
          "conductor" + "/workspaces", "/" + "Users/", "/" + "home/", "/var/" + "folders/",
          "eval" + "-framework"}
    user = os.environ.get("USER")
    if user and len(user) >= 4:
        ms.add(user)
    return sorted(m for m in ms if m)


_DOTDOT = re.compile(r"(^|[\s\"'`(=/])\.\./")
_TILDE = re.compile(r"(?<![\w/])~/")


@pytest.mark.parametrize("eval_id,instance,arm", ALL_CELLS,
                         ids=[f"{e}:{i}:{a}" for e, i, a in ALL_CELLS])
def test_sandbox_cell_is_clean(eval_id, instance, arm, iso_dirs):
    if eval_id == "eval4":
        from dbwm.eval4 import dataset
        if not dataset.is_fetched("data"):  # a fresh clone: the dataset is fetched, never vendored
            pytest.skip("SKIPPED-NO-DATASET: run `dbwm fetch eval4`")
    mod, ev = load_eval(eval_id)
    cell = Cell(eval_id=ev.eval_id, instance=instance, arm=arm, rep=0)
    spec = ev.arms[arm]
    manifest = ev.stage(cell)
    prompt = ev.prompt(cell)
    answer_values = [str(v) for v in (ev.answer_key_values(cell) or [])]

    # Staged sources never come from private/.
    for f in manifest.files:
        if f.src is not None:
            src = Path(f.src).resolve()
            assert not str(src).startswith(str(paths.private_root().resolve()) + os.sep), (f.dst, src)

    handle = sandbox.build_sandbox(manifest, cell, spec.tools, spec.tools.oracle_tools)
    try:
        sb = handle.path
        # Outside every repository / Claude Code project.
        assert paths.repo_markers_above(sb) == []
        # The runner's own pre-check is clean.
        assert sandbox.precheck(handle, answer_values=answer_values, prompt=prompt) == []
        # Listing equals the manifest exactly.
        assert sandbox.listing(sb) == handle.expected
        priv = private_hashes(eval_id)
        names_lower = {n.lower() for n in (*DEFAULT_SCORER_ONLY, *getattr(mod, "SCORER_ONLY_NAMES", ()))}
        for rel in handle.listing:
            for seg in rel.rstrip("/").split("/"):
                low = seg.lower()
                assert low not in names_lower and low.split(".", 1)[0] not in names_lower, \
                    f"scorer-only name staged: {rel}"
            full = sb / rel.rstrip("/")
            if rel.endswith("/") or full.is_symlink():
                continue
            data = full.read_bytes()
            assert hashlib.sha256(data).hexdigest() not in priv, \
                f"{rel} is a verbatim copy of {priv.get(hashlib.sha256(data).hexdigest())}"
            assert CANARY_GUID.encode() not in data and CANARY_STRING.encode() not in data, rel
        assert canary.scan_for_values(sb, answer_values) == []
        # Agent-facing text: no host path, no '..' segment, no home-relative path.
        task = (sb / sandbox.TASK_FILENAME).read_text(encoding="utf-8")
        for label, text in (("TASK.md", task), ("prompt", prompt)):
            for m in host_markers():
                assert m not in text, f"{label} contains host marker {m!r}"
            assert str(sb) not in text and str(sb.parent) not in text
            assert not _DOTDOT.search(text), f"{label} contains a '../' path"
            assert not _TILDE.search(text), f"{label} contains '~/'"
            assert CANARY_GUID not in text
            for v in canary.filter_values(answer_values):
                assert v not in text, f"{label} contains an answer-key value"
            for n in ("reference.json", "world.json", "checks.jsonl", "ANSWER_KEY", "CANARY.txt"):
                assert n not in text, f"{label} names scorer-only file {n}"
        # CLI shims (bash arms): only the baked spec, no address/token/mode/path.
        for name in handle.shims:
            src = (sb / name).read_text(encoding="utf-8")
            _assert_shim_clean(src, spec.oracle_mode)
    finally:
        sandbox.cleanup(handle)


def _assert_shim_clean(src: str, mode: str | None) -> None:
    for m in host_markers():
        assert m not in src, f"shim contains host marker {m!r}"
    for root in (paths.data_root(), paths.private_root()):
        assert str(root) not in src
    assert not re.search(r"tcp://[\w.\[\]-]+:\d+", src), "shim has a baked oracle address"
    assert "127.0.0.1" not in src and "dbwm-sidecar" not in src and "DBWM_ORACLE_TOKEN=" not in src
    assert '"mode"' not in src and "--mode" not in src
    if mode:
        # the sealed mode word never appears as a value in the baked spec
        baked = [ln for ln in src.splitlines() if ln.startswith("SPEC = ") or ln.startswith("SPECS = ")]
        assert len(baked) == 1
        assert f'\\"{mode}\\"' not in baked[0] and f'"{mode}"' not in baked[0]


def _mcp_arms():
    out = []
    for e in EVAL_IDS:
        _mod, ev = load_eval(e)
        for name, a in ev.arms.items():
            if a.tools.transport == "mcp" and a.tools.oracle_tools:
                out.append((e, name))
    return out


@pytest.mark.parametrize("eval_id,arm", _mcp_arms(), ids=[f"{e}:{a}" for e, a in _mcp_arms()])
def test_mcp_tools_dir_carries_no_paths_or_mode(eval_id, arm, tmp_path):
    _mod, ev = load_eval(eval_id)
    spec = ev.arms[arm]
    tools = tmp_path / "attempt" / "tools"
    token = "T" * 32
    cfg_path = sandbox.write_mcp_tools(tools, spec.tools.oracle_tools, address="tcp://127.0.0.1:5", token=token)
    cfg = json.loads(cfg_path.read_text())
    server = cfg["mcpServers"][sandbox.MCP_SERVER_NAME]
    assert set(cfg["mcpServers"]) == {sandbox.MCP_SERVER_NAME}
    assert set(server["env"]) == {"DBWM_ORACLE", "DBWM_ORACLE_TOKEN"}
    assert server["args"] == [str(tools / sandbox.MCP_SHIM_FILENAME)]
    assert oct(cfg_path.stat().st_mode & 0o777) == "0o600"
    text = cfg_path.read_text()
    for root in (paths.data_root(), paths.private_root()):
        assert str(root) not in text
    assert "mode" not in text.lower().replace("mcpservers", "")
    _assert_shim_clean((tools / sandbox.MCP_SHIM_FILENAME).read_text(), spec.oracle_mode)


def _groups(transport):
    """Arms of one eval that serve the same ops (by name) under different sealed modes."""
    groups = []
    for e in EVAL_IDS:
        _mod, ev = load_eval(e)
        by_ops: dict[tuple, list[str]] = {}
        for name, a in ev.arms.items():
            if a.tools.oracle_tools and a.tools.transport == transport:
                by_ops.setdefault(tuple(s.name for s in a.tools.oracle_tools), []).append(name)
        for ops, arms in by_ops.items():
            modes = {ev.arms[a].oracle_mode for a in arms}
            if len(arms) > 1 and len(modes) > 1:
                groups.append((e, ops, tuple(arms)))
    return groups


@pytest.mark.parametrize("eval_id,ops,arms", _groups("mcp") + _groups("cli"),
                         ids=[f"{e}:{'+'.join(o)}" for e, o, _a in _groups("mcp") + _groups("cli")])
def test_tool_surface_identical_across_modes(eval_id, ops, arms):
    """An agent cannot tell its sealed mode from the tool it is given: shim source,
    MCP tools/list text and CLI --help are byte-identical across arms that differ only in mode."""
    _mod, ev = load_eval(eval_id)
    mcp_src = {a: sandbox.render_mcp_shim(ev.arms[a].tools.oracle_tools) for a in arms}
    cli_src = {a: tuple(sandbox.render_cli_shim(s) for s in ev.arms[a].tools.oracle_tools) for a in arms}
    assert len(set(mcp_src.values())) == 1, {a: hashlib.sha256(s.encode()).hexdigest()[:12] for a, s in mcp_src.items()}
    assert len(set(cli_src.values())) == 1
