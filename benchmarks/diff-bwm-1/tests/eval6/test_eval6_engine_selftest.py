"""The ported engine's own selftest (about 45 s): deterministic, confounded, decision-hard."""

from __future__ import annotations

import contextlib
import io

from dbwm.eval6 import latent_engine as le


def test_latent_engine_selftest():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        le.selftest()
    out = buf.getvalue()
    assert "selftest OK" in out and "sign trap present" in out
