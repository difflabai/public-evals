"""Review finding M3: a private eval-2 split can be generated and certified without code changes.

The shipped worlds are a public dev split (the generator regenerates their answer keys).
There is no seed to salt: a held-out split is a set of private configurations.
"""

from __future__ import annotations

import copy
import json

import pytest

from dbwm.core import paths
from dbwm.eval2 import WORLD_IDS
from dbwm.eval2 import make_worlds as mw


def _private_orion() -> dict:
    """The pilot world's shape with different economics, under a public id (ids are labels)."""
    c = copy.deepcopy(mw.CONFIGS[0])
    c["units"][0]["acv"] = 540
    c["units"][1]["churn"] = "0.16"
    c["growth_cap"] = 225000
    return c


@pytest.mark.parametrize("bad,needle", [
    ([], "non-empty"),
    ([{"id": "x"}], "lacks"),
    ([{**mw.CONFIGS[0], "id": "../escape"}], "must match"),
    ([mw.CONFIGS[0], mw.CONFIGS[0]], "duplicate"),
    ([{**mw.CONFIGS[0], "units": [{"id": "u"}]}], "a unit lacks"),
])
def test_validate_configs_rejects(bad, needle):
    with pytest.raises(ValueError, match=needle):
        mw.validate_configs(bad)


def test_private_split_generates_and_certifies(tmp_path, monkeypatch):
    cfg = _private_orion()
    cfile = tmp_path / "private.json"
    cfile.write_text(json.dumps({"configs": [cfg]}))
    out = tmp_path / "split"
    assert mw.main(["--configs", str(cfile), "--out", str(out)]) == 0
    ref = json.loads((out / "private/eval2/worlds/orion-holdings/reference.json").read_text())
    shipped = json.loads(paths.private("eval2", "worlds", "orion-holdings", "reference.json").read_text())
    assert ref["target_multiple"] != shipped["target_multiple"]  # a different answer key
    assert (out / "data/eval2/worlds/orion-holdings/brief.json").is_file()
    assert (out / "private/eval2/base_models/sprout-crm.operating-model.json").is_file()  # self-contained
    assert json.loads((out / "private/eval2/confirmatory-worlds.json").read_text()) == ["orion-holdings"]
    assert "orion-holdings" in WORLD_IDS  # runnable with `dbwm run --eval 2` + the env overrides

    monkeypatch.setenv("DBWM_DATA_DIR", str(out / "data"))
    monkeypatch.setenv("DBWM_PRIVATE_DIR", str(out / "private"))
    cert = mw.certify_v1(configs=[cfg], log=lambda *_: None)
    assert cert["ok"], cert["worlds"]["orion-holdings"]["checks"]
    assert cert["worlds"]["orion-holdings"]["target"] == ref["target_multiple"]
