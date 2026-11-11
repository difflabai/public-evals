"""Compare regenerated outputs with committed ones, allowing last-bit floating-point noise.

Why: the committed outputs (e.g. eval-6 world files) were generated on macOS (arm64). On Linux
(``python:3.x-slim``, glibc libm, OpenBLAS) the same code with the same numpy
version produces the same numbers except in the last one or two bits of some
floats (largest relative difference seen in the 2026-10-03 fresh-clone check:
2.3e-14; e.g. ``19470643.076857388`` vs ``19470643.076857384``), because
transcendental functions and BLAS reductions are not bit-identical across
platforms. A byte-exact comparison therefore fails on a correct Linux install.

``text_numbers_close`` accepts two texts that are identical except for
floating-point tokens within ``PLATFORM_REL_TOL`` (relative): every
non-number character must match exactly, integer tokens must match exactly,
and the number of tokens must be equal. Discrete outcomes (counts, ids, picks,
verdict booleans) are therefore still compared exactly. Callers report
"byte-identical" when the texts are equal and name the tolerance otherwise.
Stdlib only.
"""

from __future__ import annotations

import math
import re
from typing import Any

PLATFORM_REL_TOL = 1e-9
_NUM = re.compile(r"-?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")


def _is_float_token(tok: str) -> bool:
    return "." in tok or "e" in tok or "E" in tok


def _rel(a: float, b: float) -> float:
    if a == b:
        return 0.0
    if math.isnan(a) or math.isnan(b) or math.isinf(a) or math.isinf(b):
        return math.inf
    return abs(a - b) / max(abs(a), abs(b), 1e-300)


def text_numbers_close(a: str, b: str, rel: float = PLATFORM_REL_TOL) -> tuple[bool, float]:
    """(same up to float noise, largest relative difference among float tokens)."""
    if a == b:
        return True, 0.0
    ta, tb = _NUM.findall(a), _NUM.findall(b)
    if len(ta) != len(tb) or _NUM.sub("#", a) != _NUM.sub("#", b):
        return False, math.inf
    worst = 0.0
    for x, y in zip(ta, tb):
        if x == y:
            continue
        if not (_is_float_token(x) or _is_float_token(y)):
            return False, math.inf  # integers (counts, ids, seeds) must match exactly
        worst = max(worst, _rel(float(x), float(y)))
        if worst > rel:
            return False, worst
    return True, worst


def values_close(a: Any, b: Any, rel: float = PLATFORM_REL_TOL) -> bool:
    """Deep equality of JSON-like values; floats may differ by ``rel`` (relative), nothing else may."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if a is None or b is None or isinstance(a, str):
        return a == b
    if isinstance(a, float) or isinstance(b, float):
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            return False
        return _rel(float(a), float(b)) <= rel
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(values_close(a[k], b[k], rel) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(values_close(x, y, rel) for x, y in zip(a, b))
    return a == b


def describe(exact: bool, ok: bool, worst: float, what: str = "expected") -> str:
    """'byte-identical to X' / 'equal to X within rel 1e-9 (...)' / 'DIFFERS from X'."""
    if exact:
        return f"byte-identical to {what}"
    if ok:
        return (f"equal to {what} within rel {PLATFORM_REL_TOL:g} (platform floating point; "
                f"max rel diff {worst:.1e})")
    return f"DIFFERS from {what}"
