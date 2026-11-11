"""Canary files and leak scanners.

Every ``private/<evalN>/`` directory carries ``CANARY.txt`` with the
benchmark-wide CANARY_STRING (types.py). The string must never appear in a
sandbox, a prompt, a transcript or a deliverable; the sandbox pre-check and the
audit look for its GUID. The same scanners look for an eval's answer-key
values (literal substrings, at least MIN_VALUE_LEN characters, because short
numbers like "12" occur everywhere).

Why value scans and not filename checks only: the paper's leak detector was a
substring match over six harness tokens (coupled_planner.py:57-58, 375) that
fired on required behaviour and missed copies of answers obtained by other
routes (ledger A11b, N04). A literal scan for the answer values themselves
catches staging mistakes regardless of where the value came from.

Zip containers (xlsx, docx) are scanned member by member after
decompression, since a literal scan of the compressed bytes sees nothing.
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path
from typing import Iterable, Sequence

from dbwm.core import paths
from dbwm.core.types import CANARY_GUID, CANARY_STRING

CANARY_FILENAME = "CANARY.txt"
MIN_VALUE_LEN = 6
_MAX_SCAN_BYTES = 64 * 1024 * 1024  # skip pathological files rather than hang
_MAX_ZIP_MEMBER_BYTES = 64 * 1024 * 1024


def write_canaries(private_root: Path | None = None) -> list[Path]:
    """Write CANARY.txt into every ``private/<evalN>/`` directory (idempotent).

    Returns the paths written or confirmed. Directories are those directly
    under the private root; the root itself is not given a canary because it
    is not an eval's scorer-only area.
    """
    root = Path(private_root) if private_root else paths.private_root()
    written: list[Path] = []
    if not root.is_dir():
        return written
    text = CANARY_STRING + "\n"
    for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        f = d / CANARY_FILENAME
        if not f.exists() or f.read_text(encoding="utf-8", errors="replace") != text:
            f.write_text(text, encoding="utf-8")
        written.append(f)
    return written


def missing_canaries(private_root: Path | None = None) -> list[Path]:
    """Private eval directories that lack a correct CANARY.txt."""
    root = Path(private_root) if private_root else paths.private_root()
    out: list[Path] = []
    if not root.is_dir():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        f = d / CANARY_FILENAME
        if not f.is_file() or CANARY_GUID not in f.read_text(encoding="utf-8", errors="replace"):
            out.append(d)
    return out


def filter_values(values: Iterable[object], min_len: int = MIN_VALUE_LEN) -> list[str]:
    """Distinct string forms of ``values`` that are long enough to scan for."""
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        if v is None:
            continue
        s = str(v)
        if len(s.strip()) < min_len:
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _blobs(path: Path) -> Iterable[tuple[str, bytes]]:
    """(label, bytes) for a file, plus one entry per member if it is a zip container."""
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size > _MAX_SCAN_BYTES:
        yield (str(path), b"")
        return
    try:
        data = path.read_bytes()
    except OSError:
        return
    yield (str(path), data)
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    if info.is_dir() or info.file_size > _MAX_ZIP_MEMBER_BYTES:
                        continue
                    try:
                        yield (f"{path}!{info.filename}", zf.read(info))
                    except Exception:  # noqa: BLE001 - corrupt member: scan what we can
                        continue
        except zipfile.BadZipFile:
            pass


def iter_files(root: Path) -> Iterable[Path]:
    root = Path(root)
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for fn in sorted(filenames):
            yield Path(dirpath) / fn


def scan_text_for_guid(text: str) -> bool:
    return CANARY_GUID in text


def scan_for_guid(root: Path) -> list[str]:
    """Files (or zip members, labelled ``file!member``) under ``root`` containing CANARY_GUID.

    File names are checked too.
    """
    needle = CANARY_GUID.encode()
    hits: list[str] = []
    for f in iter_files(root):
        if CANARY_GUID in f.name:
            hits.append(str(f))
            continue
        for label, blob in _blobs(f):
            if needle in blob:
                hits.append(label)
                break
    return hits


def disclosure_covers(value, disclosed):  # noqa: ANN001, ANN202 - str or bytes throughout
    """[(d, [offsets of value inside d]), ...] for every disclosed string d that contains value."""
    out = []
    for d in disclosed:
        offs, j = [], d.find(value)
        while j != -1:
            offs.append(j)
            j = d.find(value, j + 1)
        if offs:
            out.append((d, offs))
    return out


def occurs_undisclosed(hay, value, covers=()) -> bool:  # noqa: ANN001 - str or bytes
    """True if ``value`` occurs in ``hay`` at least once outside every occurrence of a covering string.

    ``covers`` comes from :func:`disclosure_covers` (same type as ``hay``). With no covers
    this is ``value in hay``. An occurrence is excused only when it lies wholly
    inside an occurrence of a disclosed string that contains the value; a
    disclosed string that is merely a substring of the value excuses nothing.
    """
    if not covers:
        return value in hay
    i = hay.find(value)
    while i != -1:
        if not any(i - o >= 0 and hay[i - o:i - o + len(d)] == d for d, offs in covers for o in offs):
            return True
        i = hay.find(value, i + 1)
    return False


def _disclosed_strings(disclosed: Iterable[object] | None) -> list[str]:
    return [str(d) for d in (disclosed or ()) if d is not None and str(d)]


def scan_text_for_values(text: str, values: Sequence[object], min_len: int = MIN_VALUE_LEN,
                         disclosed: Iterable[object] | None = None) -> list[str]:
    """The answer-key values (>= min_len chars) that occur literally in ``text``.

    ``disclosed``: strings the arm is shown on purpose (``ArmSpec.disclosed_values``);
    an occurrence wholly inside one of them does not count.
    """
    ds = _disclosed_strings(disclosed)
    return [v for v in filter_values(values, min_len) if occurs_undisclosed(text, v, disclosure_covers(v, ds))]


def scan_for_values(root: Path, values: Sequence[object], min_len: int = MIN_VALUE_LEN,
                    disclosed: Iterable[object] | None = None) -> list[tuple[str, str]]:
    """(file-or-member, value) for every answer-key value found under ``root``.

    ``disclosed`` as in :func:`scan_text_for_values`.
    """
    vals = filter_values(values, min_len)
    if not vals:
        return []
    dsb = [d.encode("utf-8") for d in _disclosed_strings(disclosed)]
    enc = [(v, v.encode("utf-8")) for v in vals]
    enc = [(v, b, disclosure_covers(b, dsb)) for v, b in enc]
    hits: list[tuple[str, str]] = []
    for f in iter_files(root):
        for label, blob in _blobs(f):
            for v, b, cov in enc:
                if occurs_undisclosed(blob, b, cov):
                    hits.append((label, v))
    return hits
