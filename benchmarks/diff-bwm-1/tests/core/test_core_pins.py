"""Review finding m3: the package's pins and the image's lock must agree.

The sidecar runs eval oracles (eval 4's qa_check, eval 5's qa_check with pyyaml)
inside the dbwm-agent image, so a package pinned differently in pyproject.toml and
docker/requirements.txt would make the in-container oracle a different instrument
from the host one (it once had pyyaml 6.0.2 vs 6.0.3). Every exact pin in a
pyproject extra must equal the lock; every range must contain the locked version.
"""

from __future__ import annotations

import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[2]


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4])


def pyproject_requirements() -> list[str]:
    """Every requirement string in [project.optional-dependencies] (no tomllib: Python 3.10)."""
    text = (PKG / "pyproject.toml").read_text(encoding="utf-8")
    sec = text.split("[project.optional-dependencies]", 1)[1].split("\n[", 1)[0]
    reqs: list[str] = []
    for line in sec.splitlines():
        line = line.split("#", 1)[0]
        if "=" in line and "[" in line:
            reqs += re.findall(r'"([^"]+)"', line)
    return reqs


def image_lock() -> dict[str, str]:
    out = {}
    for line in (PKG / "docker" / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, ver = line.split("==", 1)
            out[_norm(name)] = ver.strip()
    return out


def _satisfies(ver: str, spec: str) -> bool:
    for clause in spec.split(","):
        m = re.match(r"\s*(==|>=|<=|<|>)\s*([\w.]+)", clause)
        assert m, clause
        op, want = m.groups()
        a, b = _vtuple(ver), _vtuple(want)
        if not {"==": a == b, ">=": a >= b, "<=": a <= b, "<": a < b, ">": a > b}[op]:
            return False
    return True


def test_pyproject_pins_match_the_image_lock():
    lock = image_lock()
    assert lock, "docker/requirements.txt has no pins"
    problems = []
    for req in pyproject_requirements():
        m = re.match(r"\s*([A-Za-z0-9_.\-]+)\s*(.*)$", req)
        name, spec = _norm(m.group(1)), m.group(2).strip()
        if name == "pytest" or name not in lock:
            continue  # dev-only, or not in the image (not used by any in-container oracle)
        if spec and not _satisfies(lock[name], spec):
            problems.append(f"{name}: pyproject {spec!r} vs image {lock[name]}")
    assert not problems, problems


def test_every_pinned_runtime_extra_is_in_the_image():
    """Exact pins of the runtime extras (eval4, recalc, eval5) must be installed in the image."""
    lock = image_lock()
    missing = [r for r in pyproject_requirements() if "==" in r and _norm(r.split("==")[0]) not in lock]
    assert not missing, missing


def test_requirements_in_agrees_with_lock():
    lock = image_lock()
    for line in (PKG / "docker" / "requirements.in").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, ver = line.split("==", 1)
            assert lock.get(_norm(name)) == ver.strip(), line
