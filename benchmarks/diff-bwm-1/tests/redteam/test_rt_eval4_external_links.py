"""RT-X: eval 4 workbook external-link file read in the oracle (VERIFIED in the image; FIXED).

FIXED 2026-10-02 (integration): dbwm/eval4/checker.py refuses to recalculate a
workbook that names another workbook, runs ``formulas`` through a single-book
model, and discards a solution that touched another book; qa_check raises
QACheckError and v1 scores such a workbook as a fail. These tests are
now plain regression tests.

``dbwm/eval4/checker.py`` recalculates an agent-written ``market_model.xlsx``
with ``formulas.ExcelModel().loads(path).finish().calculate()`` and takes no
step to stop ``formulas`` from following an ``xl/externalLinks`` reference to
another workbook. Verified 2026-10-02 in dbwm-agent:1.0.0 (red-team
scratch, see the red-team report): ``formulas`` opens an absolute external
target (``/dbwm-secret/secret.xlsx``) and relative targets that climb out of
the workbook's directory; a ``file://`` URI is not followed.

Why it matters: in the CONTAINER sidecar the package is mounted read-only at
``/dbwm``; ``private/eval4/aa-briefcase-lite/submissions/<model>/.../
market_model.xlsx`` (scorer-only: other models' submissions) are readable
there at paths an attacker knows from the public source tree. ``qa_check`` copies the bytes to
a sidecar temp dir and recalculates the copy, so an absolute target still
resolves. A checklist-arm agent writes a cell ``=[1]Sheet!B7`` next to a label
the checker measures; in ``figures`` mode the pulled value is printed back
(``measured``); in ``verdict`` mode PASS/FAIL leaks one bit per check per call
(``=IF([1]Sheet!B7>X, 620000, 0)``). Nothing reaches the transcript audit or
the egress proxy: the read is done by the oracle itself.

Fix (owner: eval4): before ``ExcelModel().loads(...)`` (checker
``_recalc_if_needed``, used by qa_check AND the v1 scorer) reject a workbook
that has any ``xl/externalLinks/`` part or ``<externalReferences>`` in
``xl/workbook.xml`` (eval 4 deliverables are self-contained): qa_check raises
QACheckError("external workbook links are not allowed"), v1 scoring records
``recalc_status="rejected_external_links"``. Defence in depth (owner:
core-runtime): mount only ``dbwm/`` code (not ``private/`` of other evals)
into the sidecar, and run the sidecar's oracle with a cwd/temp dir that holds
nothing else.
"""

from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from dbwm.core.types import Cell, OracleContext

from .conftest import load_eval

PKG_ROOT = Path(__file__).resolve().parents[2]
SECRET = 612345  # inside the checker's Aurora range (600k-650k), so figures mode prints it


def _workbook_with_external_link(path: Path, target: str, *, label: str = "Aurora birds") -> None:
    """Write a market_model.xlsx: A1 = ``label``, B1 = ``=[1]Sheet!A1`` where
    external book [1] is ``target`` (an xl/externalLinks part, TargetMode External)."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = label
    ws["B1"] = "=C9+1"
    wb.save(path)
    with zipfile.ZipFile(path) as zin:
        parts = {i.filename: zin.read(i.filename) for i in zin.infolist()}
    parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(
        b"</sheets>", b'</sheets><externalReferences><externalReference r:id="rIdExt"/></externalReferences>')
    parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].replace(
        b"</Relationships>",
        b'<Relationship Id="rIdExt" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        b'relationships/externalLink" Target="externalLinks/externalLink1.xml"/></Relationships>')
    sheet = parts["xl/worksheets/sheet1.xml"]
    assert b"<f>C9+1</f>" in sheet
    parts["xl/worksheets/sheet1.xml"] = sheet.replace(b"<f>C9+1</f>", b"<f>[1]Sheet!A1</f>")
    parts["xl/externalLinks/externalLink1.xml"] = (
        b'<externalLink xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        b'<externalBook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        b'r:id="rId1"><sheetNames><sheetName val="Sheet"/></sheetNames><sheetDataSet><sheetData '
        b'sheetId="0"><row r="1"><cell r="A1"><v>0</v></cell></row></sheetData></sheetDataSet>'
        b'</externalBook></externalLink>')
    parts["xl/externalLinks/_rels/externalLink1.xml.rels"] = (
        '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
        '2006/relationships/externalLinkPath" Target="%s" TargetMode="External"/></Relationships>' % target
    ).encode()
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(
        b"</Types>",
        b'<Override PartName="/xl/externalLinks/externalLink1.xml" ContentType="application/'
        b'vnd.openxmlformats-officedocument.spreadsheetml.externalLink+xml"/></Types>')
    with zipfile.ZipFile(path, "w") as z:
        for name, data in parts.items():
            z.writestr(name, data)


def _secret_workbook(path: Path, value: int = SECRET) -> None:
    import openpyxl

    sw = openpyxl.Workbook()
    sw.active["A1"] = value  # default sheet title "Sheet"
    sw.save(path)


def _has_external_links(path: Path) -> bool:
    with zipfile.ZipFile(path) as z:
        return any(n.startswith("xl/externalLinks/") for n in z.namelist())


def test_eval4_qa_check_rejects_external_link_workbook(tmp_path):
    from dbwm.eval4 import oracle as o4
    _mod, ev = load_eval("eval4")
    sb = tmp_path / "work"
    sb.mkdir()
    _workbook_with_external_link(sb / o4.WORKBOOK, "/nonexistent-dir/secret.xlsx")
    assert _has_external_links(sb / o4.WORKBOOK)
    ctx = OracleContext(cell=Cell("eval4", ev.instances()[0], "checklist-executable", 0),
                        arm=ev.arms["checklist-executable"], mode="figures", sandbox=sb, state={})
    with pytest.raises(o4.QACheckError):
        o4.qa_check({}, ctx)


# -- the decisive demonstration: the real image (Linux paths, as in the sidecar) ----

try:
    from dbwm.core import container as _container
    _docker_ok = _container.docker_available() and _container.image_exists()
    _skip_reason = "" if _docker_ok else "needs Docker + dbwm-agent:1.0.0 image (dbwm image build)"
except Exception as e:  # noqa: BLE001
    _skip_reason = f"container module unavailable: {e}"

# Runs the sidecar's own qa_check code path (handler -> O_NOFOLLOW read -> temp
# copy -> checker -> formulas) inside the image with PKG at /dbwm, the sandbox
# at /work-ro (both read-only, as the sidecar mounts them) and a stand-in
# "private" workbook at /dbwm-secret.
IMAGE_PROBE = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, "/dbwm")
from dbwm.core.types import Cell, OracleContext
from dbwm.eval4 import EVAL, oracle as o4, checker as C
ok, err = C.recalc_smoke()
if ok != "ok":
    print(json.dumps({"recalc": "unavailable", "err": str(err)})); sys.exit(0)
out = {}
for mode, arm in (("figures", "checklist-executable"), ("verdict", "checklist-verdict")):
    ctx = OracleContext(cell=Cell("eval4", EVAL.instances()[0], arm, 0), arm=EVAL.arms[arm],
                        mode=mode, sandbox=Path("/work-ro"), state={})
    try:
        text = o4.qa_check({}, ctx)
        out[mode] = {"refused": False, "leaked": "__SECRET__" in text, "excerpt":
                     [ln for ln in text.splitlines() if "aurora" in ln.lower()][:3]}
    except o4.QACheckError as e:
        out[mode] = {"refused": True, "error": str(e)[:200]}
print(json.dumps(out))
'''


@pytest.mark.skipif(bool(_skip_reason), reason=_skip_reason or "")
def test_eval4_external_link_leak_blocked_in_image(tmp_path):
    work = tmp_path / "work"
    secret_dir = tmp_path / "secret"
    work.mkdir()
    secret_dir.mkdir()
    _secret_workbook(secret_dir / "secret.xlsx")
    _workbook_with_external_link(work / "market_model.xlsx", "/dbwm-secret/secret.xlsx")
    probe = tmp_path / "probe.py"
    probe.write_text(IMAGE_PROBE.replace("__SECRET__", str(SECRET)), encoding="utf-8")
    argv = [_container.docker_bin(), "run", "--rm", "--network", "none", "--read-only",
            "--tmpfs", "/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-v", f"{work}:/work-ro:ro",
            "-v", f"{secret_dir}:/dbwm-secret:ro",
            "-v", f"{PKG_ROOT}:/dbwm:ro",
            "-v", f"{probe}:/probe/probe.py:ro",
            "-w", "/work-ro", _container.IMAGE, "python", "/probe/probe.py"]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=240)
    out = [ln for ln in r.stdout.splitlines() if ln.strip().startswith("{")]
    assert out, f"probe produced no JSON: {r.stdout[-500:]} / {r.stderr[-800:]}"
    result = json.loads(out[-1])
    if result.get("recalc") == "unavailable":
        pytest.skip(f"formulas recalc unavailable in image: {result.get('err')}")
    # desired (after the fix): refused, or at least nothing from outside the sandbox printed
    assert result["figures"].get("refused") or not result["figures"]["leaked"], result
