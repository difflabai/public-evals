"""Fixtures for the eval-4 tests. Constants, skip marks and plain helpers live in
``_support.py`` (import them with ``from ._support import ...``), never ``from conftest import``."""

from __future__ import annotations

import pytest


@pytest.fixture
def sandbox(tmp_path):
    d = tmp_path / "sandbox"
    d.mkdir()
    return d
