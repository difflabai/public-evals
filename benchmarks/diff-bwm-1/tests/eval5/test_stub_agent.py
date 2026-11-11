"""The deterministic stub agent, driven directly (call_oracle wired to the handlers), then scored."""

from __future__ import annotations

import hashlib

import pytest

from dbwm.eval5 import EVAL
from dbwm.eval5.arms import ARMS_WITH_TOOL

from .conftest import ALL_CELLS, cell, direct_caller

EXPECT_SUCCESS = {"baseline": False, "checklist-prose": False, "checklist-verdict": True, "checklist-executable": True}


@pytest.mark.parametrize("sid,arm", ALL_CELLS)
def test_stub_agent_end_to_end_without_runner(tmp_path, sid, arm):
    calls: list = []
    c = cell(sid, arm)
    EVAL.stub_agent(c, tmp_path, direct_caller(sid, arm, tmp_path, calls))
    if arm in ARMS_WITH_TOOL:
        assert [op for op, _ in calls] == ["qa_check", "qa_check"]  # check, repair, re-check
        assert all(p == {} for _, p in calls)
    else:
        assert calls == []
    r = EVAL.score(tmp_path, c, "v1")
    assert r.deliverable_present
    assert r.success is EXPECT_SUCCESS[arm], (sid, arm, r.details.get("checker_failed_ids"))
    assert r.claimed_success is True
    assert r.overclaim is (not EXPECT_SUCCESS[arm])


def test_stub_agent_is_deterministic(tmp_path):
    digests = []
    for k in range(2):
        d = tmp_path / f"run{k}"
        d.mkdir()
        EVAL.stub_agent(cell("meridian-soc2", "checklist-executable"), d,
                        direct_caller("meridian-soc2", "checklist-executable", d))
        digests.append({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir())})
    assert digests[0] == digests[1]
