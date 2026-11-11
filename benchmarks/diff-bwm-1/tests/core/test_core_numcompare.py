"""dbwm.core.numcompare: byte-exact or last-bit float noise, nothing else (fresh-clone finding 2026-10-03)."""

from __future__ import annotations

import math

from dbwm.core.numcompare import PLATFORM_REL_TOL, describe, text_numbers_close, values_close


def test_exact_and_last_bit_noise():
    assert text_numbers_close('{"a": 1.5}', '{"a": 1.5}') == (True, 0.0)
    ok, worst = text_numbers_close('{"smb": 19470643.076857388}', '{"smb": 19470643.076857384}')
    assert ok and 0 < worst < 1e-15
    assert text_numbers_close("x 1498.8466653519856\n", "x 1498.8466653519854\n")[0]


def test_real_differences_fail():
    assert not text_numbers_close('{"a": 1.5}', '{"a": 1.6}')[0]
    assert not text_numbers_close('{"n": 12}', '{"n": 13}')[0]          # integers exactly
    assert not text_numbers_close('{"pick": "w1"}', '{"pick": "w2"}')[0]  # integer token inside an id
    assert not text_numbers_close('{"a": true}', '{"a": false}')[0]
    assert not text_numbers_close("1.0 2.0", "1.0 2.0 3.0")[0]
    assert text_numbers_close('{"a": 1.0}', '{"a": 1.0000001}')[1] > PLATFORM_REL_TOL


def test_values_close():
    a = {"x": [1.0, 2, "s", None, True], "y": {"z": 3.0000000000000004}}
    b = {"x": [1.0, 2, "s", None, True], "y": {"z": 3.0}}
    assert values_close(a, b)
    assert not values_close({"x": 1}, {"x": 2})
    assert not values_close({"x": True}, {"x": 1.0})
    assert not values_close({"x": 1.0}, {"x": None})
    assert not values_close({"x": 1.0}, {"x": 1.0, "y": 0})
    assert not values_close([1.0], [1.0, 2.0])
    assert not values_close(1.0, math.nan)


def test_describe():
    assert describe(True, True, 0.0, "E") == "byte-identical to E"
    assert "within rel" in describe(False, True, 2e-16, "E") and "2.0e-16" in describe(False, True, 2e-16, "E")
    assert describe(False, False, math.inf, "E") == "DIFFERS from E"
