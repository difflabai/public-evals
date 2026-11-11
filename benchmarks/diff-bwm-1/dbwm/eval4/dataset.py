"""AA-Briefcase-Lite at a pinned revision, split into agent-visible and grader halves.

The dataset (Artificial Analysis, Apache-2.0) is FETCHED, never redistributed:
its source pool contains real third-party documents. This module downloads the
exact subset eval 4 needs from Hugging Face at a pinned commit, verifies every
file against the shipped sha256 manifest (``aa_manifest.json``, built from the
Hugging Face tree at the revision: LFS sha256 or git blob sha1 cross-checked),
and writes it into two physically separate, gitignored trees:

- ``data/eval4/aa-briefcase-lite/``   agent-visible: the 147 source files,
  the two summary docs and the w1_t2 brief (inlined into TASK.md);
- ``private/eval4/aa-briefcase-lite/`` scorer/judge-only: checks.jsonl (the
  rubric), grader/, prompts/ (judge prompts), tasks.jsonl, README.md and the six
  published w1_t2 submissions (checker validation).

Fixes vs the paper harness (``fetch_dataset.py``): it resolved ``main`` (no
pin), recorded no hashes, and wrote the grader files into the same tree the
sandbox builder copied sources from (E4-01 / ledger N28).

CLI::

    python -m dbwm.eval4.dataset fetch [--from-dir DIR] [--force]
    python -m dbwm.eval4.dataset verify
    python -m dbwm.eval4.dataset status

``--from-dir`` installs from an existing local copy of the dataset (any layout
that mirrors the repo paths); every file is still verified against the manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from . import _paths

MANIFEST_PATH = Path(__file__).resolve().parent / "aa_manifest.json"
USER_AGENT = "diff-bwm-1/1.0.0 (+eval4 dataset fetch)"


def manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text())


def revision() -> str:
    return manifest()["revision"]


def resolve_url(path: str, rev: str | None = None) -> str:
    m = manifest()
    return (f"https://huggingface.co/datasets/{m['repo']}/resolve/"
            f"{rev or m['revision']}/{path}")


def dest_for(entry: dict[str, Any]) -> Path:
    if entry["split"] == "data":
        return _paths.dataset_public(entry["path"])
    if entry["split"] == "private":
        return _paths.dataset_private(entry["path"])
    raise ValueError(f"unknown split {entry['split']!r} for {entry['path']}")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_verified(data: bytes, entry: dict[str, Any], dest: Path) -> None:
    if len(data) != entry["size"]:
        raise ValueError(f"{entry['path']}: size {len(data)} != manifest {entry['size']}")
    got = hashlib.sha256(data).hexdigest()
    if got != entry["sha256"]:
        raise ValueError(f"{entry['path']}: sha256 {got} != manifest {entry['sha256']}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".part-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def verify(splits: tuple[str, ...] = ("data", "private")) -> dict[str, Any]:
    """Check every manifest file of ``splits`` exists with the right sha256."""
    missing, mismatched, ok = [], [], 0
    for e in manifest()["files"]:
        if e["split"] not in splits:
            continue
        p = dest_for(e)
        if not p.is_file():
            missing.append(e["path"])
        elif p.stat().st_size != e["size"] or _sha256(p) != e["sha256"]:
            mismatched.append(e["path"])
        else:
            ok += 1
    return {"ok": ok, "missing": missing, "mismatched": mismatched,
            "complete": not missing and not mismatched}


def is_fetched(split: str = "data") -> bool:
    """Cheap presence test (no hashing): every manifest file of ``split`` exists."""
    return all(dest_for(e).is_file() for e in manifest()["files"] if e["split"] == split)


def fetch(from_dir: str | Path | None = None, force: bool = False,
          splits: tuple[str, ...] = ("data", "private"), quiet: bool = False) -> dict[str, Any]:
    """Populate the data/ and private/ trees for eval 4. Returns counts.

    Network: one HTTPS GET per file to huggingface.co at the pinned revision
    (unless ``from_dir`` is given). Nothing is written outside the two
    gitignored dataset directories.
    """
    counts = {"fetched": 0, "copied": 0, "cached": 0, "failed": 0}
    errors = []
    src_root = Path(from_dir).expanduser().resolve() if from_dir else None
    for e in manifest()["files"]:
        if e["split"] not in splits:
            continue
        dest = dest_for(e)
        if (not force and dest.is_file() and dest.stat().st_size == e["size"]
                and _sha256(dest) == e["sha256"]):
            counts["cached"] += 1
            continue
        try:
            if src_root is not None:
                data = (src_root / e["path"]).read_bytes()
                _write_verified(data, e, dest)
                counts["copied"] += 1
            else:
                req = urllib.request.Request(resolve_url(e["path"]),
                                             headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=300) as r:
                    data = r.read()
                _write_verified(data, e, dest)
                counts["fetched"] += 1
        except Exception as ex:  # noqa: BLE001 - report every failure, keep going
            counts["failed"] += 1
            errors.append(f"{e['path']}: {ex}")
            if not quiet:
                print(f"FAILED {e['path']}: {ex}", file=sys.stderr)
    stamp = {"repo": manifest()["repo"], "revision": revision(),
             "counts": counts, "errors": errors}
    for split, root in (("data", _paths.dataset_public()), ("private", _paths.dataset_private())):
        if split in splits and root.exists():
            (root / "FETCHED.json").write_text(json.dumps(
                {"repo": stamp["repo"], "revision": stamp["revision"], "split": split},
                indent=2) + "\n")
    return stamp


def sources() -> list[tuple[str, Path]]:
    """(sandbox-relative dst, host src) for the 147 staged source files.

    The paper staged ``source_files/shared`` and ``source_files/week`` as
    ``shared/`` and ``week/`` at the sandbox root; TASK.md refers to them so.
    """
    out = []
    for e in manifest()["files"]:
        p = e["path"]
        if p.startswith("source_files/shared/") or p.startswith("source_files/week/"):
            out.append((p[len("source_files/"):], _paths.dataset_public(p)))
    return sorted(out)


def read_public(path: str) -> str:
    p = _paths.dataset_public(path)
    if not p.is_file():
        raise FileNotFoundError(
            f"eval4 dataset file {path} not found under {_paths.dataset_public()}; "
            "run `dbwm fetch eval4` (or `python -m dbwm.eval4.dataset fetch`)")
    return p.read_text(encoding="utf-8")


def read_private(path: str) -> str:
    p = _paths.dataset_private(path)
    if not p.is_file():
        raise FileNotFoundError(
            f"eval4 grader file {path} not found under {_paths.dataset_private()}; "
            "run `dbwm fetch eval4` (or `python -m dbwm.eval4.dataset fetch`)")
    return p.read_text(encoding="utf-8")


def w1t2_checks() -> list[dict[str, Any]]:
    """The 12 graded rubric rows: binary checks of task w1_t2 in checks.jsonl.

    checks.jsonl at the pinned revision has 63 rows over the four week-1 tasks
    (55 binary + 8 pairwise); w1_t2 has 12 binary and 2 pairwise rows. Only the
    12 binary rows are graded (the paper never used the pairwise rows).
    """
    rows = [json.loads(line) for line in read_private("checks.jsonl").splitlines() if line.strip()]
    return [c for c in rows if c.get("task_id") == "w1_t2" and c.get("scoring_type") == "binary"]


def submission_path(model: str) -> Path:
    return _paths.dataset_private("submissions", model, "w1_t2", "submission", "market_model.xlsx")


PUBLISHED_SUBMISSIONS = ("claude-4-opus-thinking", "claude-fable-5", "gemini-3-1-pro",
                         "glm-5-2", "gpt-5-5", "o3")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m dbwm.eval4.dataset", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download (or copy) and verify the pinned subset")
    f.add_argument("--from-dir", help="install from a local copy instead of the network")
    f.add_argument("--force", action="store_true", help="re-fetch even verified files")
    sub.add_parser("verify", help="verify installed files against the manifest")
    sub.add_parser("status", help="print where the trees live and whether they are complete")
    args = ap.parse_args(argv)
    if args.cmd == "fetch":
        st = fetch(from_dir=args.from_dir, force=args.force)
        print(f"AA-Briefcase-Lite @ {st['revision'][:12]}: {st['counts']}")
        return 1 if st["counts"]["failed"] else 0
    v = verify()
    if args.cmd == "status":
        print(f"revision: {revision()}")
        print(f"agent-visible: {_paths.dataset_public()}")
        print(f"grader-only:   {_paths.dataset_private()}")
    print(f"verified {v['ok']} files; missing {len(v['missing'])}; mismatched {len(v['mismatched'])}")
    for p in v["mismatched"][:20]:
        print(f"  MISMATCH {p}")
    return 0 if v["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
