"""RT-H: per-eval oracle handler file access — symlink, escape and swap resistance.

The checker/model oracles read files from the live sandbox the agent controls.
These tests confirm every one of them:
  * refuses a path the agent could point outside the sandbox (absolute, ``..``,
    a symlink to a file outside, a symlink to a scorer-only file);
  * ignores a ``mode``/``path`` field in the payload (the mode is sealed);
  * never grades a reference submission a bash-arm agent symlinked in.

The eval 1/2/3 oracle arms are no-bash (``read``/``write`` only), so the agent
cannot create a symlink at all; the readers are still confined, which these
tests pin. The eval 4/5 oracle arms are bash arms, so symlink resistance is
load-bearing there.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dbwm.core import paths
from dbwm.core.types import Cell, OracleContext

from .conftest import load_eval


def _ctx(ev, arm_name, sb):
    cell = Cell(eval_id=ev.eval_id, instance=ev.instances()[0], arm=arm_name, rep=0)
    arm = ev.arms[arm_name]
    return OracleContext(cell=cell, arm=arm, mode=arm.oracle_mode, sandbox=Path(sb), state={})


# -- eval 2: world_query file argument ---------------------------------------

def test_eval2_world_query_file_arg_confined(tmp_path):
    from dbwm.eval2 import world_query as wq
    sb = tmp_path / "work"
    sb.mkdir()
    (sb / "candidate.json").write_text('{"allocations": {}}', encoding="utf-8")
    secret = tmp_path / "secret.json"
    secret.write_text('{"stolen": true}', encoding="utf-8")
    ctx = _ctx(load_eval("eval2")[1], "grounded-readonly", sb)

    for bad in ("../secret.json", "/etc/passwd", str(secret), "a/../../secret.json"):
        r = wq.handle_world_query({"file": bad}, ctx)
        assert isinstance(r, dict) and r.get("ok") is False, (bad, r)
        assert str(tmp_path) not in str(r.get("error", "")), f"error echoed a host path for {bad!r}"

    # a symlink inside the sandbox pointing outside must not be followed
    link = sb / "candidate.json"
    link.unlink()
    os.symlink(secret, link)
    r = wq.handle_world_query({"file": "candidate.json"}, ctx)
    # either refused, or the escape is rejected on resolve; never returns the secret's content graded as valid
    assert "stolen" not in str(r)


# -- eval 3: model_eval file argument ----------------------------------------

def test_eval3_model_eval_file_arg_confined(tmp_path):
    from dbwm.eval3 import oracle as o3
    sb = tmp_path / "work"
    sb.mkdir()
    (sb / "model.json").write_text('{"entities": []}', encoding="utf-8")
    secret = tmp_path / "reference.json"
    secret.write_text('{"answer": 42}', encoding="utf-8")
    ctx = _ctx(load_eval("eval3")[1], "gen-figures", sb)

    for bad in ("../reference.json", "/etc/hostname", str(secret), "x/../../reference.json", "model.json\x00"):
        r = o3.handle_model_eval({"file": bad}, ctx)
        assert isinstance(r, dict) and r.get("ok") is False, (bad, r)
        assert str(tmp_path) not in str(r.get("error", "")), f"error echoed a host path for {bad!r}"

    os.symlink(secret, sb / "sneaky.json")
    r = o3.handle_model_eval({"file": "sneaky.json"}, ctx)
    assert r.get("ok") is False, r
    assert "42" not in json.dumps(r) and "answer" not in json.dumps(r) and str(tmp_path) not in json.dumps(r)


# -- eval 4: qa_check reads only market_model.xlsx, symlink-safe -------------

def test_eval4_qa_check_rejects_symlinked_workbook(tmp_path):
    from dbwm.eval4 import oracle as o4
    sb = tmp_path / "work"
    sb.mkdir()
    outside = tmp_path / "ref.xlsx"
    outside.write_bytes(b"PK\x03\x04 not really a workbook")
    os.symlink(outside, sb / o4.WORKBOOK)
    ctx = _ctx(load_eval("eval4")[1], "checklist-verdict", sb)
    with pytest.raises(o4.QACheckError) as ei:
        o4.qa_check({"mode": "figures", "path": str(outside)}, ctx)
    assert "symlink" in str(ei.value)
    # the sealed mode is unchanged by the payload's mode/path keys
    assert ctx.mode == "verdict"


def test_eval4_qa_check_ignores_payload_path(tmp_path):
    from dbwm.eval4 import oracle as o4
    sb = tmp_path / "work"
    sb.mkdir()
    ctx = _ctx(load_eval("eval4")[1], "checklist-verdict", sb)
    # no market_model.xlsx present -> the handler reports the missing file,
    # never the attacker's `path`
    with pytest.raises(o4.QACheckError) as ei:
        o4.qa_check({"path": "/etc/passwd", "file": "/etc/passwd", "mode": "figures"}, ctx)
    msg = str(ei.value)
    assert "/etc/passwd" not in msg and o4.WORKBOOK in msg


# -- eval 5: SafeSubmission rejects symlinks / escapes / non-regular ---------

def test_eval5_safe_submission_rejects_symlink_and_escape(tmp_path):
    from dbwm.eval5 import engine as E
    sb = tmp_path / "work"
    sb.mkdir()
    (sb / "good.md").write_text("real deliverable", encoding="utf-8")
    secret = tmp_path / "registry_v1.0.jsonl"
    secret.write_text("SCORER ONLY", encoding="utf-8")
    os.symlink(secret, sb / "linked.md")
    os.symlink(secret, sb / "abs.md")

    sub = E.SafeSubmission(sb, allowed=("good.md", "linked.md", "abs.md", "nested.md", "missing.md"))
    assert sub.exists("good.md") is True
    assert sub.exists("linked.md") is False and sub.rejected["linked.md"] == "symlink"
    assert sub.text("linked.md") is None
    assert sub.text("abs.md") is None
    # a nested path is not a plain file name
    assert sub.exists("a/b.md") is False
    assert sub.exists("..") is False


def test_eval5_safe_submission_revalidates_on_read(tmp_path):
    """A regular file that is swapped for a symlink after the existence check is
    still caught: ``_read`` re-runs ``_ok`` immediately before reading, so the
    only residual window is the microseconds between that check and ``read_text``
    (RT finding: close it by reading from an O_NOFOLLOW fd as eval4 does)."""
    from dbwm.eval5 import engine as E
    sb = tmp_path / "work"
    sb.mkdir()
    (sb / "good.md").write_text("real deliverable", encoding="utf-8")
    secret = tmp_path / "registry_v1.0.jsonl"
    secret.write_text("SCORER-ONLY-SECRET", encoding="utf-8")
    sub = E.SafeSubmission(sb, allowed=("good.md",))
    assert sub.exists("good.md") is True
    # concurrent swap to a symlink pointing at a scorer-only file
    (sb / "good.md").unlink()
    os.symlink(secret, sb / "good.md")
    assert sub.text("good.md") is None, "symlink swapped in after the check was followed"


def test_eval5_qa_check_output_has_no_scorer_internals(tmp_path):
    """The v1 figures rendering must not echo the grader's thresholds/regex/token lists."""
    from dbwm.eval5 import oracle as o5
    _mod, ev = load_eval("eval5")
    inst = ev.instances()[0]
    sb = tmp_path / "work"
    sb.mkdir()
    meta = __import__("dbwm.eval5.dataset", fromlist=["scenario_meta"]).scenario_meta(inst)
    for name in meta["deliverables"]:
        (sb / name).write_text("placeholder\n", encoding="utf-8")
    ctx = _ctx(ev, "checklist-executable", sb)
    text = o5.qa_check({"mode": "verdict"}, ctx)  # payload mode ignored; arm is figures
    assert isinstance(text, str) and text
    assert ctx.mode == "figures"
    # the private registry's own file name must never surface in agent-facing text
    assert "registry_v1.0.jsonl" not in text and "_qa_impl" not in text


# -- check-then-read windows (RT-T) --------------------------------------------
#
# Each reader below used to validate a path and then open it again by name, so
# a symlink swapped in between the check and the read was followed. The tests
# make the race deterministic by swapping inside the validation step (the
# moment a concurrent swapper wins). FIXED 2026-10-02 (integration): the reads
# now go through one descriptor opened with O_NOFOLLOW and checked with fstat
# (eval5 SafeSubmission._read; eval2/eval3 via dbwm.core.safe_io.read_nofollow),
# so these are plain regression tests.
#
# eval 5 is the exploitable one: its qa_check arms are bash arms at CONTAINER,
# the handler runs in the sidecar where /dbwm (private/) is mounted, and a
# symlink target string written in the agent container resolves there.
# Verified empirically (scratch/redteam/toctou): 37/20000 host reads and
# 77 reads in 25 s across two containers on Docker Desktop returned the secret.
# eval 2/3 file arguments are served only to no-bash HOST arms (the agent cannot
# create a symlink), so there it is defence in depth.


def _swap_to_symlink(path: Path, target: Path) -> None:
    path.unlink()
    os.symlink(target, path)


def test_eval5_safe_submission_swap_after_check_is_not_followed(tmp_path, monkeypatch):
    from dbwm.eval5 import engine as E
    sb = tmp_path / "work"
    sb.mkdir()
    (sb / "good.md").write_text("real deliverable", encoding="utf-8")
    secret = tmp_path / "registry_v1.0.jsonl"
    secret.write_text("SCORER-ONLY-SECRET", encoding="utf-8")
    orig_ok = E.SafeSubmission._ok
    swapped = []

    def racing_ok(self, name):
        ok = orig_ok(self, name)
        if ok and name == "good.md" and not swapped:
            _swap_to_symlink(sb / "good.md", secret)  # the agent's swapper wins here
            swapped.append(True)
        return ok

    monkeypatch.setattr(E.SafeSubmission, "_ok", racing_ok)
    text = E.SafeSubmission(sb, allowed=("good.md",)).text("good.md")
    assert swapped
    assert text is None or "SCORER-ONLY" not in text, "followed a symlink swapped in after the check"


def test_eval5_qa_check_survives_a_vanishing_deliverable(tmp_path, monkeypatch):
    from dbwm.eval5 import engine as E
    sb = tmp_path / "work"
    sb.mkdir()
    (sb / "good.md").write_text("real deliverable", encoding="utf-8")
    orig_ok = E.SafeSubmission._ok

    def racing_ok(self, name):
        ok = orig_ok(self, name)
        if ok and (sb / name).exists():
            (sb / name).unlink()  # deleted between check and read
        return ok

    monkeypatch.setattr(E.SafeSubmission, "_ok", racing_ok)
    assert E.SafeSubmission(sb, allowed=("good.md",)).text("good.md") is None


def test_eval2_candidate_file_swap_after_check_is_not_followed(tmp_path, monkeypatch):
    import pathlib

    from dbwm.eval2 import world_query as wq
    sb = tmp_path / "work"
    sb.mkdir()
    (sb / "candidate.json").write_text('{"allocations": {}}', encoding="utf-8")
    secret = tmp_path / "secret.json"
    secret.write_text('{"stolen": true}', encoding="utf-8")
    orig_is_file = pathlib.Path.is_file
    swapped = []

    def racing_is_file(self, *a, **k):
        ok = orig_is_file(self, *a, **k)
        if ok and self.name == "candidate.json" and not swapped:
            _swap_to_symlink(sb / "candidate.json", secret)
            swapped.append(True)
        return ok

    monkeypatch.setattr(pathlib.Path, "is_file", racing_is_file)
    try:
        text = wq._read_candidate_file(sb, "candidate.json")
    except wq.CandidateError:
        text = None
    assert swapped
    assert text is None or "stolen" not in text


def test_eval3_model_file_swap_after_check_is_not_followed(tmp_path, monkeypatch):
    import pathlib

    from dbwm.eval3 import oracle as o3
    sb = tmp_path / "work"
    sb.mkdir()
    (sb / "model.json").write_text('{"entities": []}', encoding="utf-8")
    secret = tmp_path / "reference.json"
    secret.write_text('{"answer": 42}', encoding="utf-8")
    orig_is_file = pathlib.Path.is_file
    swapped = []

    def racing_is_file(self, *a, **k):
        ok = orig_is_file(self, *a, **k)
        if ok and self.name == "model.json" and not swapped:
            _swap_to_symlink(sb / "model.json", secret)
            swapped.append(True)
        return ok

    monkeypatch.setattr(pathlib.Path, "is_file", racing_is_file)
    try:
        data = o3._read_sandbox_json(sb, "model.json")
    except o3.PayloadError:
        data = None
    assert swapped
    assert data is None or "answer" not in json.dumps(data)
