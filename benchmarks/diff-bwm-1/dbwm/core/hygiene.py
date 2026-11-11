"""Publication hygiene gate (``dbwm hygiene``; also run as a test over the whole package tree).

The benchmark was built inside a private harness on one person's laptop. This
scan fails the release if anything that only makes sense there -- or anything
secret -- is about to ship: absolute home paths, the operator's username, the
private repositories and services the harness was wired to, browser
User-Agent spoofing, API-key shapes, real public IP addresses in authored data,
PDFs (the internal renders were confidential), and large unlisted files.

Patterns are written with a character class in the middle (``gabehamilto[n]``)
so this file does not match itself. The list follows the BUILD-SPEC; key
checks match key SHAPES (prefix plus a run of key characters), not bare
prefixes such as ``fw_``, which would fire on ordinary identifiers.

What is scanned: every file that would ship (caches, the root-level
``runs/``/``build/``/``dist/`` and anything the package's ``.gitignore``
excludes are left out and the gitignored paths are listed), its relative PATH as well as its
content, and the members of zip containers (.xlsx, .docx, .zip; Office files
record the author's folder and name inside). Symlinks are reported, not
followed. Secret-shaped matches are masked in the output.

Allowlist: ``hygiene-allowlist.txt`` at the package root, one entry per line::

    <path glob relative to the package root>:<check name or *>  # <reason>

A zip member hit is reported as ``<file>!<member>``; the glob is matched
against that label.

A reason is mandatory (an entry without one is a configuration error). Entries
that suppress nothing are reported as stale. Exit status is non-zero when any
hit remains or the allowlist is malformed.
"""

from __future__ import annotations

import argparse
import fnmatch
import io
import ipaddress
import json
import os
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

PKG_ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST_NAME = "hygiene-allowlist.txt"
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_LINE_CHARS = 240  # how much of a hit line is echoed

# Directory names never shipped, wherever they occur (caches, VCS metadata).
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", ".tox",
             ".eggs"}
# Skipped only at the package root: the default run-output dir (gitignored) and
# build products.
ROOT_SKIP_DIRS = {"runs", "build", "dist", ".venv", "venv"}
SKIP_DIR_SUFFIXES = (".egg-info",)

# (name, regex) -- content checks applied to every text file.
CONTENT_CHECKS: tuple[tuple[str, str], ...] = (
    ("users-path", r"/User[s]/"),
    # /home/agent is the benchmark image's own non-root user (docker/Dockerfile), not a person.
    ("home-path", r"/hom[e]/(?!agent\b)"),
    ("operator-username", r"gabehamilto[n]"),
    ("conductor-workspace", r"conductor/workspace[s]"),
    ("private-org", r"difflaba[i]"),
    ("private-harness", r"eval-framewor[k]"),
    ("private-sut", r"business-world-model[s]"),
    ("sut-endpoint", r"localhost:1808[0]"),
    ("sut-credential", r"bwm-eval-admi[n]"),
    ("private-submodule", r"model-nexu[s]"),
    ("context-dir", r"\.contex[t]/"),
    ("launchd", r"launchct[l]"),
    ("browser-user-agent", r"Mozill[a]/5\.0"),
    ("key-anthropic", r"sk-an[t]-"),
    ("key-sk", r"\bs[k]-[A-Za-z0-9_\-]{20,}"),
    ("key-fireworks", r"\bf[w]_[A-Za-z0-9]{16,}"),
    ("key-aws", r"\bAKI[A][0-9A-Z]{16}\b"),
    ("key-github", r"\bgh[pousr]_[A-Za-z0-9]{20,}|github_pa[t]_[A-Za-z0-9_]{20,}"),
    ("key-slack", r"\bxo[x][baprs]-[A-Za-z0-9-]{10,}"),
    ("key-google", r"\bAIz[a][0-9A-Za-z_\-]{35}"),
    ("private-key-block", r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVAT[E] KEY-----"),
)
# Public IPv4 addresses are checked only under these top-level dirs (authored data).
IP_CHECK_DIRS = ("data", "private")
IP_CHECK = "public-ipv4"
FILE_CHECKS = ("pdf", "large-file", "env-file", "junk-file", "symlink", "unreadable-member")
ALL_CHECKS = tuple(n for n, _ in CONTENT_CHECKS) + (IP_CHECK,) + FILE_CHECKS

# Dotted quads not glued to a word, a slash or a version marker: "LibreOffice/24.8.4.2"
# and "v1.2.3.4" are version strings, not addresses.
_IPV4_RE = re.compile(r"(?<![\w./\-$])(?:\d{1,3}\.){3}\d{1,3}(?!\.?\d)")
_COMPILED = tuple((n, re.compile(p)) for n, p in CONTENT_CHECKS)
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


@dataclass(frozen=True)
class Hit:
    path: str  # relative POSIX path
    check: str
    line: int | None
    excerpt: str


@dataclass
class AllowEntry:
    glob: str
    check: str
    reason: str
    lineno: int
    used: int = 0


@dataclass
class HygieneReport:
    root: str
    files_scanned: int = 0
    hits: list[Hit] = field(default_factory=list)
    suppressed: list[Hit] = field(default_factory=list)
    stale_allowlist: list[AllowEntry] = field(default_factory=list)
    allowlist_errors: list[str] = field(default_factory=list)
    gitignored: list[str] = field(default_factory=list)  # paths not scanned because they do not ship

    @property
    def ok(self) -> bool:
        return not self.hits and not self.allowlist_errors

    def to_json(self) -> dict:
        return {
            "root": self.root,
            "ok": self.ok,
            "files_scanned": self.files_scanned,
            "hits": [h.__dict__ for h in self.hits],
            "suppressed": len(self.suppressed),
            "stale_allowlist": [f"{e.glob}:{e.check} (line {e.lineno})" for e in self.stale_allowlist],
            "allowlist_errors": list(self.allowlist_errors),
            "gitignored": list(self.gitignored),
        }


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

def parse_allowlist(text: str) -> tuple[list[AllowEntry], list[str]]:
    entries: list[AllowEntry] = []
    errors: list[str] = []
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        body, sep, reason = line.partition("#")
        reason = reason.strip()
        body = body.strip()
        if not sep or not reason:
            errors.append(f"line {i}: missing '# reason': {raw!r}")
            continue
        glob, sep2, check = body.rpartition(":")
        glob, check = glob.strip(), check.strip()
        if not sep2 or not glob or not check:
            errors.append(f"line {i}: expected '<path glob>:<check>  # reason': {raw!r}")
            continue
        if check != "*" and check not in ALL_CHECKS:
            errors.append(f"line {i}: unknown check {check!r} (known: {', '.join(ALL_CHECKS)})")
            continue
        entries.append(AllowEntry(glob=glob, check=check, reason=reason, lineno=i))
    return entries, errors


def _allowed(hit: Hit, entries: Sequence[AllowEntry]) -> bool:
    for e in entries:
        if (e.check == "*" or e.check == hit.check) and fnmatch.fnmatchcase(hit.path, e.glob):
            e.used += 1
            return True
    return False


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

class GitIgnore:
    """The subset of ``.gitignore`` semantics needed to know what would ship.

    Root ``.gitignore`` only. Supported: comments, blank lines, ``!`` negation
    (last match wins), trailing ``/`` (directories only), leading ``/`` or an
    inner ``/`` (anchored to the root), otherwise a pattern matches any path
    component's name; ``*``/``?``/``[...]`` via fnmatch (``**`` behaves like
    ``*``). Fetched third-party data (eval 4's AA-Briefcase-Lite) lives in
    gitignored directories and must not be reported as shipped.
    """

    def __init__(self, text: str = ""):
        self.rules: list[tuple[bool, bool, bool, str]] = []  # (negate, dir_only, anchored, pattern)
        for raw in text.splitlines():
            line = raw.rstrip()
            if not line or line.startswith("#"):
                continue
            negate = line.startswith("!")
            if negate:
                line = line[1:]
            dir_only = line.endswith("/")
            line = line.rstrip("/")
            anchored = "/" in line
            line = line.lstrip("/")
            if line:
                self.rules.append((negate, dir_only, anchored, line))

    @classmethod
    def load(cls, root: Path) -> "GitIgnore":
        p = Path(root) / ".gitignore"
        return cls(p.read_text(encoding="utf-8", errors="replace") if p.is_file() else "")

    def ignored(self, rel: str, is_dir: bool) -> bool:
        hit = False
        for negate, dir_only, anchored, pat in self.rules:
            if dir_only and not is_dir:
                continue
            if anchored:
                m = fnmatch.fnmatchcase(rel, pat) or fnmatch.fnmatchcase(rel, pat + "/**")
            else:
                m = fnmatch.fnmatchcase(rel.rsplit("/", 1)[-1], pat)
            if m:
                hit = not negate
        return hit


def iter_release_files(root: Path, gitignore: GitIgnore | None = None,
                       skipped: list[str] | None = None) -> Iterable[Path]:
    """Every file that would ship: the tree minus caches, build products, run outputs and gitignored paths.

    Paths left out because of ``.gitignore`` are appended to ``skipped`` (relative, POSIX).
    """
    root = Path(root)
    gi = gitignore if gitignore is not None else GitIgnore()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        at_root = here == root
        rel_dir = "" if at_root else here.relative_to(root).as_posix() + "/"
        keep = []
        for d in sorted(dirnames):
            if d in SKIP_DIRS or d.endswith(SKIP_DIR_SUFFIXES) or (at_root and d in ROOT_SKIP_DIRS):
                continue
            if gi.ignored(rel_dir + d, True):
                if skipped is not None:
                    skipped.append(rel_dir + d + "/")
                continue
            keep.append(d)
        dirnames[:] = keep
        for fn in sorted(filenames):
            if gi.ignored(rel_dir + fn, False):
                if skipped is not None:
                    skipped.append(rel_dir + fn)
                continue
            yield here / fn


def _is_binary(head: bytes) -> bool:
    return b"\0" in head


def _public_ipv4(token: str) -> bool:
    try:
        ip = ipaddress.IPv4Address(token)
    except ValueError:
        return False  # out-of-range octets or leading zeros: not an address
    # is_global excludes private, loopback, link-local, reserved, multicast,
    # shared (100.64/10) and the RFC 5737 documentation blocks.
    return ip.is_global and not ip.is_multicast


SECRET_CHECKS = frozenset(n for n, _ in CONTENT_CHECKS if n.startswith("key-") or n == "private-key-block")
MAX_ZIP_MEMBER_BYTES = 32 * 1024 * 1024
ZIP_MAGIC = b"PK\x03\x04"


def _excerpt(check: str, line: str, rx: re.Pattern[str] | None = None) -> str:
    """The hit line, shortened; a secret-shaped match is masked so the report itself never leaks it."""
    text = line.strip()
    if check in SECRET_CHECKS and rx is not None:
        # Mask the whole token, not just the matched key prefix: the key body follows it.
        out, pos = [], 0
        for m in rx.finditer(text):
            if m.start() < pos:
                continue
            end = m.end()
            while end < len(text) and (text[end].isalnum() or text[end] in "_-"):
                end += 1
            out.append(text[pos:m.start()] + text[m.start():m.start() + 6] + "...[redacted]")
            pos = end
        text = "".join(out) + text[pos:]
    return text[:MAX_LINE_CHARS]


def _scan_text(text: str, label: str, check_ips: bool) -> list[Hit]:
    hits: list[Hit] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for check, rx in _COMPILED:
            if rx.search(line):
                hits.append(Hit(label, check, lineno, _excerpt(check, line, rx)))
        if check_ips:
            for m in _IPV4_RE.finditer(line):
                if _public_ipv4(m.group(0)):
                    hits.append(Hit(label, IP_CHECK, lineno, _excerpt(IP_CHECK, line)))
                    break
    return hits


def _scan_zip(data: bytes, rel: str, check_ips: bool) -> list[Hit]:
    """Office files (.xlsx/.docx/.pptx) and zips are containers: scan every member.

    Excel records the author's absolute folder in ``xl/workbook.xml``
    (``x15ac:absPath``) and the creator in ``docProps/core.xml``; a binary
    scan of the compressed container would see neither.
    """
    hits: list[Hit] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                label = f"{rel}!{info.filename}"
                for check, rx in _COMPILED:
                    if rx.search(info.filename):
                        hits.append(Hit(label, check, None, "member name"))
                if info.is_dir() or info.file_size > MAX_ZIP_MEMBER_BYTES:
                    continue
                try:
                    blob = zf.read(info)
                except Exception:  # noqa: BLE001 - corrupt member: report, keep scanning
                    hits.append(Hit(label, "unreadable-member", None, "zip member could not be read"))
                    continue
                if blob[:4] == ZIP_MAGIC:
                    hits.extend(_scan_zip(blob, label, check_ips))
                elif not _is_binary(blob[:8192]):
                    hits.extend(_scan_text(blob.decode("utf-8", errors="replace"), label, check_ips))
    except zipfile.BadZipFile:
        hits.append(Hit(rel, "unreadable-member", None, "zip magic but not a readable zip"))
    return hits


def scan_file(path: Path, rel: str) -> list[Hit]:
    hits: list[Hit] = []
    name = path.name
    for check, rx in _COMPILED:  # the path itself ships too
        if rx.search(rel):
            hits.append(Hit(rel, check, None, "file path"))
    if name.lower().endswith(".pdf"):
        hits.append(Hit(rel, "pdf", None, "PDF file"))
    if name == ".env" or name.startswith(".env."):
        hits.append(Hit(rel, "env-file", None, "environment file"))
    if name in JUNK_NAMES:
        hits.append(Hit(rel, "junk-file", None, "OS junk file"))
    try:
        size = path.stat().st_size
    except OSError:
        return hits
    if size > MAX_FILE_BYTES:
        hits.append(Hit(rel, "large-file", None, f"{size} bytes > {MAX_FILE_BYTES}"))
    if path.is_symlink():
        hits.append(Hit(rel, "symlink", None, f"symlink -> {os.readlink(path)}"[:MAX_LINE_CHARS]))
        return hits
    try:
        with open(path, "rb") as fh:
            data = fh.read(min(size, 64 * 1024 * 1024))
    except OSError:
        return hits
    if data[:5] == b"%PDF-" and not name.lower().endswith(".pdf"):
        hits.append(Hit(rel, "pdf", None, "PDF content under another extension"))
    check_ips = rel.split("/", 1)[0] in IP_CHECK_DIRS
    if data[:4] == ZIP_MAGIC:
        hits.extend(_scan_zip(data, rel, check_ips))
        return hits
    if _is_binary(data[:8192]):
        return hits
    hits.extend(_scan_text(data.decode("utf-8", errors="replace"), rel, check_ips))
    return hits


def run_hygiene(root: str | os.PathLike | None = None, allowlist: str | os.PathLike | None = None) -> HygieneReport:
    """Scan ``root`` (default: the package root) against ``allowlist`` (default: root/hygiene-allowlist.txt)."""
    root_p = Path(root).resolve() if root is not None else PKG_ROOT
    rep = HygieneReport(root=root_p.name)
    allow_p = Path(allowlist) if allowlist is not None else root_p / ALLOWLIST_NAME
    entries: list[AllowEntry] = []
    if allow_p.is_file():
        entries, errors = parse_allowlist(allow_p.read_text(encoding="utf-8"))
        rep.allowlist_errors.extend(errors)
    for path in iter_release_files(root_p, GitIgnore.load(root_p), rep.gitignored):
        rel = path.relative_to(root_p).as_posix()
        rep.files_scanned += 1
        for hit in scan_file(path, rel):
            (rep.suppressed if _allowed(hit, entries) else rep.hits).append(hit)
    rep.stale_allowlist = [e for e in entries if e.used == 0]
    return rep


def render_text(rep: HygieneReport) -> str:
    lines = [f"hygiene: scanned {rep.files_scanned} files under {rep.root}/"]
    if rep.gitignored:
        lines.append(f"not scanned (gitignored, not shipped): {', '.join(rep.gitignored[:20])}"
                     + (" ..." if len(rep.gitignored) > 20 else ""))
    for e in rep.allowlist_errors:
        lines.append(f"ALLOWLIST ERROR {e}")
    by_check: dict[str, int] = {}
    for h in rep.hits:
        by_check[h.check] = by_check.get(h.check, 0) + 1
        where = f"{h.path}:{h.line}" if h.line else h.path
        lines.append(f"HIT [{h.check}] {where}: {h.excerpt}")
    for e in rep.stale_allowlist:
        lines.append(f"stale allowlist entry (line {e.lineno}): {e.glob}:{e.check}")
    if rep.ok:
        lines.append(f"hygiene OK ({len(rep.suppressed)} allowlisted mention(s))")
    else:
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(by_check.items()))
        lines.append(f"hygiene FAILED: {len(rep.hits)} hit(s) ({summary}); "
                     f"{len(rep.allowlist_errors)} allowlist error(s)")
    return "\n".join(lines)


def configure_parser(ap: argparse.ArgumentParser) -> argparse.ArgumentParser:
    ap.add_argument("--root", default=None, help="tree to scan (default: the package root)")
    ap.add_argument("--allowlist", default=None, help=f"allowlist file (default: <root>/{ALLOWLIST_NAME})")
    ap.add_argument("--json", action="store_true", help="print a JSON report instead of text")
    return ap


def run_from_args(args: argparse.Namespace) -> int:
    rep = run_hygiene(args.root, args.allowlist)
    if args.json:
        print(json.dumps(rep.to_json(), indent=2, sort_keys=True))
    else:
        print(render_text(rep))
    return 0 if rep.ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    ap = configure_parser(argparse.ArgumentParser(prog="dbwm hygiene", description=__doc__.split("\n\n")[0]))
    return run_from_args(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
