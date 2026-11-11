"""dbwm.core.hygiene: the publication gate. Every forbidden string here is assembled at run time,
so this file never trips the gate it tests."""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import pytest

from dbwm.core import hygiene as H

U = "/" + "Users" + "/"
HOME = "/" + "home" + "/"
OPERATOR = "gabe" + "hamilton"
SK_ANT = "sk-" + "ant-" + "api03-" + "A" * 30
SK = "sk-" + "B" * 32
FW = "fw" + "_" + "C" * 24
AKIA = "AKI" + "A" + "D" * 16
GHP = "gh" + "p_" + "E" * 36
UA = "Mozil" + "la/5.0 (Macintosh)"
CTX = "." + "context/"


def _tree(tmp_path: Path, files: dict[str, str | bytes]) -> Path:
    root = tmp_path / "pkg"
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
    root.mkdir(exist_ok=True)
    return root


def _checks(rep):
    return sorted({(h.path, h.check) for h in rep.hits})


def test_clean_tree_passes(tmp_path):
    root = _tree(tmp_path, {"README.md": "diff-bwm-1\n", "data/eval1/x.csv": "a,b\n192.0.2.7,1\n"})
    rep = H.run_hygiene(root)
    assert rep.ok and rep.files_scanned == 2


@pytest.mark.parametrize("text,check", [
    (f"see {U}someone/x", "users-path"),
    (f"cd {HOME}someone", "home-path"),
    (f"by {OPERATOR}", "operator-username"),
    ("conductor/" + "workspaces/foo", "conductor-workspace"),
    ("github.com/" + "difflab" + "ai/x", "private-org"),
    ("the eval-" + "framework repo", "private-harness"),
    ("business-" + "world-models", "private-sut"),
    ("http://localhost:" + "18080/", "sut-endpoint"),
    ("user bwm-eval-" + "admin", "sut-credential"),
    ("model-" + "nexus submodule", "private-submodule"),
    (f"ROOT/{CTX}build", "context-dir"),
    (f"key={SK_ANT}", "key-anthropic"),
    (f"key={SK}", "key-sk"),
    (f"key={FW}", "key-fireworks"),
    (f"aws {AKIA}", "key-aws"),
    (f"token {GHP}", "key-github"),
    (f"UA: {UA}", "browser-user-agent"),
])
def test_content_checks(tmp_path, text, check):
    root = _tree(tmp_path, {"docs/a.md": f"line one\n{text}\n"})
    rep = H.run_hygiene(root)
    assert ("docs/a.md", check) in _checks(rep)
    hit = next(h for h in rep.hits if h.check == check)
    assert hit.line == 2


def test_container_home_is_not_a_person(tmp_path):
    root = _tree(tmp_path, {"docker/Dockerfile": f"RUN useradd --home-dir {HOME}agent agent\n"})
    assert H.run_hygiene(root).ok


def test_secret_excerpts_are_masked(tmp_path):
    root = _tree(tmp_path, {"a.txt": f"ANTHROPIC_API_KEY={SK_ANT}\n"})
    rep = H.run_hygiene(root)
    text = H.render_text(rep)
    assert SK_ANT not in text and "[redacted]" in text
    assert "A" * 8 not in text  # the key body is masked too, not only its prefix
    for key, check in ((GHP, "key-github"), (FW, "key-fireworks")):
        root2 = _tree(tmp_path / check, {"b.txt": f"x {key} y\n"})
        out = H.render_text(H.run_hygiene(root2))
        assert key not in out and key[6:14] not in out and out.count("[redacted]") == 1


def test_fw_prefix_alone_is_not_a_key(tmp_path):
    root = _tree(tmp_path, {"a.py": "fw_backend = None\nraw_fw_data = 1\n"})
    assert H.run_hygiene(root).ok


def test_ipv4_only_in_authored_data_and_only_public(tmp_path):
    root = _tree(tmp_path, {
        "data/eval1/hosts.csv": "8.8.8.8\n10.0.0.1\n203.0.113.9\nLibreOffice/24.8.4.2\nv1.2.3.4\n",
        "private/eval2/x.json": '{"ip": "34.1.2.3"}\n',
        "docs/net.md": "8.8.8.8\n",
    })
    rep = H.run_hygiene(root)
    assert _checks(rep) == [("data/eval1/hosts.csv", "public-ipv4"), ("private/eval2/x.json", "public-ipv4")]


def test_file_checks(tmp_path):
    root = _tree(tmp_path, {
        "docs/render.pdf": b"%PDF-1.7\n",
        "docs/sneaky.bin": b"%PDF-1.4\n\x00",
        ".env": "X=1\n",
        "data/.DS_Store": b"\x00\x01",
        f"docs/{OPERATOR}-notes.md": "fine\n",
    })
    big = root / "data" / "big.csv"
    with open(big, "wb") as fh:
        fh.truncate(H.MAX_FILE_BYTES + 1)
    os.symlink("/etc/hosts", root / "docs" / "link")
    got = _checks(H.run_hygiene(root))
    for want in [("docs/render.pdf", "pdf"), ("docs/sneaky.bin", "pdf"), (".env", "env-file"),
                 ("data/.DS_Store", "junk-file"), ("data/big.csv", "large-file"), ("docs/link", "symlink"),
                 (f"docs/{OPERATOR}-notes.md", "operator-username")]:
        assert want in got, (want, got)


def test_zip_members_are_scanned(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/workbook.xml", f'<x15ac:absPath url="{U}{OPERATOR}/Desktop/"/>')
        zf.writestr("docProps/core.xml", "<dc:creator>openpyxl</dc:creator>")
    root = _tree(tmp_path, {"data/eval4/model.xlsx": buf.getvalue(), "data/eval4/bad.zip": b"PK\x03\x04junk"})
    got = _checks(H.run_hygiene(root))
    assert ("data/eval4/model.xlsx!xl/workbook.xml", "users-path") in got
    assert ("data/eval4/model.xlsx!xl/workbook.xml", "operator-username") in got
    assert ("data/eval4/bad.zip", "unreadable-member") in got


def test_skipped_directories(tmp_path):
    root = _tree(tmp_path, {
        "runs/r1/transcript.jsonl": f"{U}x\n",  # root-level run outputs: never shipped
        "dbwm/__pycache__/m.pyc": f"{U}x\n",
        "pkg.egg-info/PKG-INFO": f"{U}x\n",
        "tests/eval2/fixtures/runs/v.json": f"{U}x\n",  # a nested runs/ directory: scanned
    })
    assert _checks(H.run_hygiene(root)) == [("tests/eval2/fixtures/runs/v.json", "users-path")]


def test_allowlist(tmp_path):
    root = _tree(tmp_path, {"docs/errata.md": f"originally in the eval-{'framework'} repo\n",
                            "docs/other.md": f"eval-{'framework'}\n"})
    allow = root / H.ALLOWLIST_NAME
    allow.write_text("# comment\n"
                     "docs/errata.md:private-harness  # errata must name the original repository\n"
                     "docs/never.md:*  # stale entry\n", encoding="utf-8")
    rep = H.run_hygiene(root)
    assert _checks(rep) == [("docs/other.md", "private-harness")]
    assert len(rep.suppressed) == 1 and [e.glob for e in rep.stale_allowlist] == ["docs/never.md"]
    allow.write_text("docs/errata.md:private-harness\n" "docs/x.md:not-a-check  # r\n" "no-colon  # r\n")
    rep = H.run_hygiene(root)
    assert len(rep.allowlist_errors) == 3 and not rep.ok


def test_cli_exit_status(tmp_path, capsys):
    root = _tree(tmp_path, {"a.md": "clean\n"})
    assert H.main(["--root", str(root)]) == 0
    (root / "b.md").write_text(f"{U}x\n")
    assert H.main(["--root", str(root), "--json"]) == 1
    assert '"ok": false' in capsys.readouterr().out


def test_package_allowlist_is_well_formed():
    rep = H.run_hygiene()
    assert rep.allowlist_errors == []


def test_package_tree_is_clean():
    """The release gate itself: fails while anything in the package would leak."""
    rep = H.run_hygiene()
    assert rep.ok, H.render_text(rep)[-6000:]


def test_gitignored_paths_do_not_ship(tmp_path):
    root = _tree(tmp_path, {
        ".gitignore": "# fetched\ndata/eval4/aa-briefcase-lite/\n*.log\n!keep.log\n/top-only.txt\n",
        "data/eval4/aa-briefcase-lite/source_files/x.pdf": b"%PDF-1.4\n",
        "data/eval4/own.md": f"{U}x\n",
        "a/debug.log": f"{U}x\n",
        "a/keep.log": f"{U}x\n",
        "top-only.txt": f"{U}x\n",
        "a/top-only.txt": f"{U}x\n",
    })
    rep = H.run_hygiene(root)
    assert _checks(rep) == [("a/keep.log", "users-path"), ("a/top-only.txt", "users-path"),
                            ("data/eval4/own.md", "users-path")]
    assert "data/eval4/aa-briefcase-lite/" in rep.gitignored and "a/debug.log" in rep.gitignored
    assert "gitignored" in H.render_text(rep)


def test_package_gitignore_is_anchored_for_run_and_build_dirs():
    """/runs/, /build/, /dist/ are root-only: a runs/, build/ or dist/ directory elsewhere in the tree must ship."""
    from pathlib import Path

    pkg = Path(__file__).resolve().parents[2]
    gi = H.GitIgnore.load(pkg)
    for d in ("runs", "build", "dist", "data/eval4/aa-briefcase-lite", "private/eval4/aa-briefcase-lite"):
        assert gi.ignored(d, True), d
    for d in ("tests/eval1/fixtures/runs", "tests/eval3/fixtures/runs", "dbwm/eval2/build", "tests/core/dist"):
        assert not gi.ignored(d, True), d
