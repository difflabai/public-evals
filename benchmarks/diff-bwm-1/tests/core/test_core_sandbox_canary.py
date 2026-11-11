"""Sandbox build/pre-check/collect and the canary scanners: every leak class is caught."""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from dbwm.core import canary, paths, sandbox
from dbwm.core.types import CANARY_GUID, CANARY_STRING, Cell, StagedFile, StagingManifest, ToolPolicy

CELL = Cell("toy", "alpha", "no-tool", 0)
RW = ToolPolicy(frozenset({"read", "write"}))
ANSWER = "73.1415"


def manifest(task="Read input.json and write answer.json.\n", files=None, dirs=("scratch",)):
    return StagingManifest(files=files if files is not None else
                           [StagedFile(dst="input.json", content=b'{"x": 10}'),
                            StagedFile(dst="docs/notes.md", content=b"notes")],
                           task_md=task, writable_dirs=list(dirs))


def test_build_listing_modes_and_location(iso_dirs, toy):
    sb_root, _ = iso_dirs
    h = sandbox.build_sandbox(manifest(), CELL, RW)
    try:
        assert h.path.parent == sb_root and h.path.name.startswith("dbwm_")
        assert h.listing == ["TASK.md", "docs/notes.md", "input.json", "docs/", "scratch/"]
        assert h.listing == h.expected
        assert oct(os.stat(h.path / "input.json").st_mode & 0o777) == "0o444"
        assert os.access(h.path, os.W_OK)  # deliverables are written at the root
        assert os.access(h.path / "scratch", os.W_OK)
        assert sandbox.precheck(h, answer_values=[ANSWER], prompt="Follow TASK.md.") == []
        assert len(h.manifest_sha256) == 64
    finally:
        sandbox.cleanup(h)
    assert not h.path.exists()


def test_cli_shims_staged_only_for_bash_arms(iso_dirs, toy):
    arm = toy.arms["bash-oracle"]
    h = sandbox.build_sandbox(toy.stage(Cell("toy", "alpha", "bash-oracle", 0)), CELL, arm.tools,
                              arm.tools.oracle_tools)
    try:
        assert "toy_model" in h.listing and h.shims == ["toy_model"]
        src = (h.path / "toy_model").read_text()
        assert src.startswith("#!/usr/bin/env python3")
        for bad in ("\"mode\"", "numeric", str(paths.PKG_ROOT), "private", "toy_eval", "--backend"):
            assert bad not in src.split("SPEC = ", 1)[1].split("\n", 1)[0], bad
        assert os.access(h.path / "toy_model", os.X_OK)
    finally:
        sandbox.cleanup(h)
    arm = toy.arms["mcp-oracle"]
    h = sandbox.build_sandbox(toy.stage(Cell("toy", "alpha", "mcp-oracle", 0)), CELL, arm.tools,
                              arm.tools.oracle_tools)
    try:
        assert "toy_model" not in h.listing and "dbwm_mcp.py" not in h.listing
    finally:
        sandbox.cleanup(h)


def test_sandbox_refused_inside_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setenv("DBWM_SANDBOX_ROOT", str(repo / "sandboxes"))
    with pytest.raises(paths.InsideRepoError):
        sandbox.build_sandbox(manifest(), CELL, RW)


def test_malformed_manifests_rejected(iso_dirs):
    with pytest.raises(sandbox.SandboxError):
        sandbox.build_sandbox(manifest(files=[StagedFile(dst="TASK.md", content=b"x")]), CELL, RW)
    with pytest.raises(sandbox.SandboxError):
        sandbox.build_sandbox(manifest(files=[StagedFile(dst="a", content=b"x"),
                                              StagedFile(dst="a", content=b"y")]), CELL, RW)
    with pytest.raises(ValueError):
        StagedFile(dst="../escape", content=b"x")
    with pytest.raises(ValueError):
        StagedFile(dst="/abs", content=b"x")
    with pytest.raises(sandbox.SandboxError):
        sandbox.build_sandbox(manifest(files=[StagedFile(dst="a", src=Path("/nonexistent/file"))]), CELL, RW)
    # a failed build leaves nothing behind
    assert list(iso_dirs[0].iterdir()) == []


@pytest.mark.parametrize("leak,needle", [
    ("extra_file", "listing differs"),
    ("canary_content", "canary GUID"),
    ("canary_name", "canary GUID"),
    ("answer_value", "answer-key value"),
    ("answer_in_xlsx", "answer-key value"),
    ("symlink", "symlink"),
])
def test_precheck_file_leaks(iso_dirs, leak, needle):
    files = [StagedFile(dst="input.json", content=b'{"x": 10}')]
    if leak == "canary_content":
        files.append(StagedFile(dst="data.txt", content=CANARY_STRING.encode()))
    if leak == "answer_value":
        files.append(StagedFile(dst="data.txt", content=f"y = {ANSWER}".encode()))
    if leak == "answer_in_xlsx":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("xl/sharedStrings.xml", f"<si><t>{ANSWER}</t></si>" * 50)
        files.append(StagedFile(dst="book.xlsx", content=buf.getvalue()))
    h = sandbox.build_sandbox(manifest(files=files), CELL, RW)
    try:
        if leak == "extra_file":
            (h.path / "stray.json").write_text("{}")
        if leak == "canary_name":
            (h.path / "scratch" / f"{CANARY_GUID}.txt").write_text("x")
        if leak == "symlink":
            os.symlink("/etc/hosts", h.path / "scratch" / "link")
        probs = sandbox.precheck(h, answer_values=[ANSWER])
        assert any(needle in p for p in probs), probs
    finally:
        sandbox.cleanup(h)


@pytest.mark.parametrize("text,needle", [
    ("Read " + "/" + "Users/alice/repo/private/eval2/world.json", "absolute path"),  # assembled: dbwm hygiene
    ("Use C:\\data\\x.json", "absolute path"),
    ("See ~/notes.txt", "home-relative"),
    ("Look in ../world for hints", "'..' path segment"),
    ("The private/ directory has more", "breadcrumb 'private/'"),
    ("Do not open ANSWER_KEY", "breadcrumb 'ANSWER_KEY'"),
    ("compare with reference.json", "breadcrumb 'reference.json'"),
    (f"Answer is {ANSWER}", "answer-key value"),
    (f"guid {CANARY_GUID}", "canary"),
])
def test_precheck_text_leaks_in_task_and_prompt(iso_dirs, text, needle):
    h = sandbox.build_sandbox(manifest(task="Do the task.\n" + text + "\n"), CELL, RW)
    try:
        probs = sandbox.precheck(h, answer_values=[ANSWER])
        assert any(needle in p and p.startswith(("TASK.md", "answer-key", "canary")) for p in probs), probs
    finally:
        sandbox.cleanup(h)
    h = sandbox.build_sandbox(manifest(), CELL, RW)
    try:
        probs = sandbox.precheck(h, answer_values=[ANSWER], prompt=text)
        assert any(needle in p for p in probs), probs
    finally:
        sandbox.cleanup(h)


def test_precheck_flags_own_sandbox_path_and_allows_prose(iso_dirs):
    h = sandbox.build_sandbox(manifest(task="Wait... then write answer.json. Ratio 3/4 is fine.\n"), CELL, RW)
    try:
        assert sandbox.precheck(h, prompt="Fine prose... and a/b ratio.") == []
        probs = sandbox.precheck(h, prompt=f"cd {h.path} first")
        assert any("host path" in p or "absolute path" in p for p in probs)
    finally:
        sandbox.cleanup(h)


def test_short_answer_values_not_scanned(iso_dirs):
    h = sandbox.build_sandbox(manifest(), CELL, RW)
    try:
        assert sandbox.precheck(h, answer_values=["10", 10, None, "x"]) == []
    finally:
        sandbox.cleanup(h)


def test_collect_present_missing_glob_and_escapes(iso_dirs, tmp_path):
    secret = tmp_path / "host_secret.txt"
    secret.write_text("host secret")
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "passwd").write_text("root:x")
    h = sandbox.build_sandbox(manifest(), CELL, RW)
    try:
        (h.path / "answer.json").write_text('{"y": 1}')
        (h.path / "scratch" / "a.csv").write_text("1")
        (h.path / "scratch" / "b.csv").write_text("2")
        os.symlink(secret, h.path / "plan.json")  # file symlink out of the sandbox
        os.symlink(outside_dir, h.path / "out")  # directory symlink out of the sandbox
        rec = sandbox.collect(h, ["answer.json", "scratch/*.csv", "plan.json", "out/passwd", "out", "nope.json"],
                              tmp_path / "deliv")
        got = sorted(f["path"] for f in rec["files"])
        assert got == ["answer.json", "scratch/a.csv", "scratch/b.csv"]
        assert rec["present"] == {"answer.json": True, "scratch/*.csv": True, "plan.json": False,
                                  "out/passwd": False, "out": False, "nope.json": False}
        assert set(rec["missing"]) == {"plan.json", "out/passwd", "out", "nope.json"}
        reasons = {r["path"]: r["reason"] for r in rec["rejected"]}
        assert "plan.json" in reasons and "out/passwd" in reasons and "out" in reasons
        assert not (tmp_path / "deliv" / "plan.json").exists()
        assert not (tmp_path / "deliv" / "out").exists()
        assert all(len(f["sha256"]) == 64 for f in rec["files"])
    finally:
        sandbox.cleanup(h)


def test_remove_tree_handles_readonly(iso_dirs):
    h = sandbox.build_sandbox(manifest(), CELL, RW)
    os.chmod(h.path / "docs", 0o555)
    sandbox.cleanup(h)
    assert not h.path.exists()


def test_mcp_tools_written_outside_sandbox(iso_dirs, tmp_path, toy):
    spec = toy.arms["mcp-oracle"].tools.oracle_tools
    p = sandbox.write_mcp_tools(tmp_path / "tools", spec, address="tcp://127.0.0.1:9", token="t" * 20)
    cfg = json.loads(p.read_text())
    srv = cfg["mcpServers"]["dbwm"]
    assert srv["env"] == {"DBWM_ORACLE": "tcp://127.0.0.1:9", "DBWM_ORACLE_TOKEN": "t" * 20}
    assert srv["args"] == [str(tmp_path / "tools" / "dbwm_mcp.py")]
    assert oct(os.stat(p).st_mode & 0o777) == "0o600"
    shim = (tmp_path / "tools" / "dbwm_mcp.py").read_text()
    assert "numeric" not in shim and "toy_eval" not in shim and str(paths.PKG_ROOT) not in shim


# -- canary ----------------------------------------------------------------------------


def test_canary_write_and_missing(tmp_path):
    priv = tmp_path / "private"
    (priv / "eval1").mkdir(parents=True)
    (priv / "eval2").mkdir()
    assert sorted(p.name for p in canary.missing_canaries(priv)) == ["eval1", "eval2"]
    written = canary.write_canaries(priv)
    assert len(written) == 2 and canary.missing_canaries(priv) == []
    assert (priv / "eval1" / "CANARY.txt").read_text().strip() == CANARY_STRING


def test_canary_scanners(tmp_path):
    (tmp_path / "a.txt").write_text("nothing")
    (tmp_path / "b.txt").write_text(f"x {CANARY_GUID} y")
    with zipfile.ZipFile(tmp_path / "c.docx", "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", "<w:t>value 987654.321</w:t>" * 20)
    assert [Path(h).name for h in canary.scan_for_guid(tmp_path)] == ["b.txt"]
    hits = canary.scan_for_values(tmp_path, ["987654.321", "12"])
    assert hits == [(str(tmp_path / "c.docx") + "!word/document.xml", "987654.321")]
    assert canary.scan_text_for_values("the 987654.321 thing", ["987654.321"]) == ["987654.321"]


def test_package_private_dirs_carry_canaries():
    """Every private/<evalN>/ shipped in the package has CANARY.txt (dbwm canary --write)."""
    missing = canary.missing_canaries(paths.PKG_ROOT / "private")
    assert missing == [], f"run `dbwm canary --write`: {missing}"
