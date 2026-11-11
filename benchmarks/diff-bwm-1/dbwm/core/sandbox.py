"""Fresh per-attempt sandboxes: build, pre-check, collect, delete.

Why: in the paper's harness the agent's cwd was the trial output directory inside
the harness repo (coupled_planner.py:359), the world directory was shared and
writable across concurrent trials (ledger N03), earlier attempts' outputs and
other trials' plans were reachable (N04, N12), and grounded prompts printed
absolute paths to the world spec and the harness (coupled_planner.py:243-244;
N05). Here every attempt gets a new empty directory outside any repository,
populated only from the eval's exhaustive StagingManifest, checked for leaks
before the agent starts, harvested for declared deliverables afterwards, and
deleted.

Layout of a sandbox:
    TASK.md                 the eval's task text (manifest.task_md)
    <staged files>          exactly manifest.files, chmod 0444 (0555 if executable)
    <cli_name>              one CLI shim per oracle tool, bash arms only (0555)
    <writable_dirs>/        created empty
The MCP shim for no-bash arms is NOT in the sandbox: write_mcp_tools() puts it
and mcp.json in a tools directory under the attempt dir (harness_out).
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

from dbwm.core import canary, paths
from dbwm.core.types import Cell, OracleToolSpec, StagingManifest, ToolPolicy

TASK_FILENAME = "TASK.md"
MCP_SHIM_FILENAME = "dbwm_mcp.py"
MCP_CONFIG_FILENAME = "mcp.json"
MCP_SERVER_NAME = "dbwm"

_SHIM_DIR = Path(__file__).resolve().parent / "oracle"
_CLI_MARKER = "SPEC = None  # replaced by the sandbox builder"
_MCP_MARKER = "SPECS = None  # replaced by the sandbox builder"

MAX_DELIVERABLE_FILE_BYTES = 100 * 1024 * 1024


class SandboxError(RuntimeError):
    pass


@dataclass
class SandboxHandle:
    path: Path
    manifest_sha256: str
    listing: list[str]
    expected: list[str] = field(default_factory=list)
    cell: Cell | None = None
    shims: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"path": str(self.path), "manifest_sha256": self.manifest_sha256,
                "listing": self.listing, "expected": self.expected, "shims": self.shims,
                "cell": self.cell.cell_id if self.cell else None}


# -- shims -------------------------------------------------------------------


def render_cli_shim(spec: OracleToolSpec) -> str:
    """Source of the sandbox executable for one oracle tool, spec baked in.

    The baked spec carries only the op name, usage line, description and how
    argv maps to the payload: no address, token, mode or path (those reach the
    process through DBWM_ORACLE / DBWM_ORACLE_TOKEN at run time).
    """
    src = (_SHIM_DIR / "shim_cli.py").read_text(encoding="utf-8")
    if _CLI_MARKER not in src:
        raise SandboxError("shim_cli.py lost its SPEC marker")
    baked = {"op": spec.name, "usage": spec.cli_usage, "description": spec.description,
             "args": spec.cli_args, "payload_key": spec.cli_payload_key}
    if spec.cli_args not in ("json_file", "none"):
        raise SandboxError(f"oracle tool {spec.name}: unknown cli_args {spec.cli_args!r}")
    line = "SPEC = json.loads(%r)" % json.dumps(baked, sort_keys=True)
    return src.replace(_CLI_MARKER, line, 1)


def render_mcp_shim(specs: Sequence[OracleToolSpec]) -> str:
    src = (_SHIM_DIR / "shim_mcp.py").read_text(encoding="utf-8")
    if _MCP_MARKER not in src:
        raise SandboxError("shim_mcp.py lost its SPECS marker")
    baked = [{"name": s.name, "description": s.description,
              "input_schema": json.loads(json.dumps(dict(s.input_schema)))} for s in specs]
    line = "SPECS = json.loads(%r)" % json.dumps(baked, sort_keys=True)
    return src.replace(_MCP_MARKER, line, 1)


def write_mcp_tools(tools_dir: Path, specs: Sequence[OracleToolSpec], *, address: str, token: str,
                    python: str = "python3", agent_tools_dir: str | None = None) -> Path:
    """Write the MCP shim and mcp.json into ``tools_dir``; return the mcp.json path.

    ``agent_tools_dir`` is where the agent process sees ``tools_dir`` (e.g.
    ``/dbwm-tools`` in the container); default is ``tools_dir`` itself.
    """
    tools_dir = Path(tools_dir)
    tools_dir.mkdir(parents=True, exist_ok=True)
    shim = tools_dir / MCP_SHIM_FILENAME
    shim.write_text(render_mcp_shim(specs), encoding="utf-8")
    os.chmod(shim, 0o555)
    seen_dir = agent_tools_dir or str(tools_dir)
    cfg = {"mcpServers": {MCP_SERVER_NAME: {
        "command": python,
        "args": [f"{seen_dir.rstrip('/')}/{MCP_SHIM_FILENAME}"],
        "env": {"DBWM_ORACLE": address, "DBWM_ORACLE_TOKEN": token},
    }}}
    path = tools_dir / MCP_CONFIG_FILENAME
    path.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


# Harness-side files that carry the per-trial oracle token while the attempt
# runs (relative to the attempt dir): the MCP config the CLI reads (host), the
# tools-dir copy mounted into the agent container, and the sidecar's sealed
# config (container).
TOKEN_FILES = (MCP_CONFIG_FILENAME, f"tools/{MCP_CONFIG_FILENAME}", "sealed.json")
REDACTED = "<redacted>"


def redact_token_files(attempt_dir: Path, token: str | None) -> list[str]:
    """After an attempt: replace the oracle token in TOKEN_FILES with ``<redacted>``.

    The server is gone by then, so a leftover token is dead, but it should not
    sit in ``runs/`` (review finding m8). The files are kept, redacted, as a
    record of what the agent was given. Returns the redacted relative paths.
    """
    done: list[str] = []
    if not token:
        return done
    for rel in TOKEN_FILES:
        f = Path(attempt_dir) / rel
        try:
            if f.is_symlink() or not f.is_file():
                continue
            text = f.read_text(encoding="utf-8")
            if token in text:
                f.write_text(text.replace(token, REDACTED), encoding="utf-8")
                done.append(rel)
        except OSError:
            continue
    return done


MAX_REDACT_BYTES = 64 * 1024 * 1024


def redact_secret_values(attempt_dir: Path, values) -> list[str]:
    """After an attempt: replace credential VALUES in every regular file under the attempt dir.

    Whatever the agent printed (a Bash ``env``, a deliverable) must not leave
    the real credential in ``runs/`` (red-team finding RT-E; with the CONTAINER
    API relay the agent never holds it, but HOST-UNSAFE bash arms and other
    adapters can). Values shorter than 8 characters are ignored (too likely to
    match innocent text). Symlinks are never followed. Returns the redacted
    relative paths.
    """
    vals = sorted({str(v) for v in values or () if v and len(str(v)) >= 8}, key=len, reverse=True)
    done: list[str] = []
    if not vals:
        return done
    root = Path(attempt_dir)
    for f in sorted(root.rglob("*")):
        try:
            if f.is_symlink() or not f.is_file() or f.stat().st_size > MAX_REDACT_BYTES:
                continue
            data = f.read_bytes()
            new = data
            for v in vals:
                new = new.replace(v.encode("utf-8"), REDACTED.encode())
            if new != data:
                f.write_bytes(new)
                done.append(str(f.relative_to(root)))
        except OSError:
            continue
    return done


# -- building ----------------------------------------------------------------


def _norm(dst: str) -> str:
    p = PurePosixPath(dst)
    if p.is_absolute() or ".." in p.parts or str(p) in ("", "."):
        raise SandboxError(f"bad sandbox path {dst!r}")
    return str(p)


def _parents(rel: str) -> list[str]:
    parts = PurePosixPath(rel).parts[:-1]
    return ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]


def expected_listing(manifest: StagingManifest, tools: ToolPolicy,
                     oracle_specs: Sequence[OracleToolSpec] = ()) -> list[str]:
    """What listing() must return for a correctly built sandbox (files, then 'dir/' entries)."""
    files = {_norm(f.dst) for f in manifest.files}
    files.add(TASK_FILENAME)
    if tools.transport == "cli":
        files.update(_norm(s.cli_name) for s in oracle_specs)
    dirs: set[str] = set()
    for f in files:
        dirs.update(_parents(f))
    for d in manifest.writable_dirs:
        nd = _norm(d)
        dirs.add(nd)
        dirs.update(_parents(nd + "/x"))
    return sorted(files) + sorted(d + "/" for d in dirs)


def listing(root: Path) -> list[str]:
    """Every file (and symlink) as a relative POSIX path, then every directory as 'dir/'."""
    root = Path(root)
    files: list[str] = []
    dirs: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        for d in dirnames:
            full = Path(dirpath) / d
            rel = d if rel_dir == "." else f"{rel_dir}/{d}"
            if full.is_symlink():
                files.append(rel.replace(os.sep, "/"))
            else:
                dirs.append(rel.replace(os.sep, "/") + "/")
        for fn in filenames:
            rel = fn if rel_dir == "." else f"{rel_dir}/{fn}"
            files.append(rel.replace(os.sep, "/"))
    return sorted(files) + sorted(dirs)


def sha256_tree(root: Path) -> str:
    """Content hash of a directory: sorted (path, sha256, mode) lines plus directories."""
    root = Path(root)
    h = hashlib.sha256()
    for rel in listing(root):
        full = root / rel.rstrip("/")
        if rel.endswith("/"):
            h.update(f"D\t{rel}\n".encode())
        elif full.is_symlink():
            h.update(f"L\t{rel}\t{os.readlink(full)}\n".encode())
        else:
            digest = hashlib.sha256(full.read_bytes()).hexdigest()
            mode = stat.S_IMODE(full.stat().st_mode)
            h.update(f"F\t{rel}\t{digest}\t{mode:o}\n".encode())
    return h.hexdigest()


def new_sandbox_dir(root: Path | None = None) -> Path:
    base = Path(root) if root else paths.sandbox_root()
    base.mkdir(parents=True, exist_ok=True)
    p = Path(tempfile.mkdtemp(prefix="dbwm_", dir=str(base))).resolve()
    paths.assert_outside_repos(p)
    return p


def build_sandbox(manifest: StagingManifest, cell: Cell, tools: ToolPolicy,
                  oracle_specs: Sequence[OracleToolSpec] = (), *, root: Path | None = None) -> SandboxHandle:
    """Create and populate a fresh sandbox; raises SandboxError on a malformed manifest.

    The sandbox is created by tempfile.mkdtemp(prefix="dbwm_") under
    DBWM_SANDBOX_ROOT (or the system temp dir) and checked with
    paths.assert_outside_repos before anything is written.
    """
    seen: set[str] = set()
    for f in manifest.files:
        d = _norm(f.dst)
        if d in seen:
            raise SandboxError(f"duplicate staged path {d}")
        if d == TASK_FILENAME:
            raise SandboxError("TASK.md is written from manifest.task_md; do not stage it")
        seen.add(d)
    shim_names: list[str] = []
    if tools.transport == "cli":
        for s in oracle_specs:
            n = _norm(s.cli_name)
            if n in seen or n == TASK_FILENAME:
                raise SandboxError(f"CLI shim name {n} collides with a staged file")
            shim_names.append(n)
    for d in manifest.writable_dirs:
        nd = _norm(d)
        if nd in seen:
            raise SandboxError(f"writable dir {nd} collides with a staged file")

    sb = new_sandbox_dir(root)
    try:
        for f in manifest.files:
            dst = sb / _norm(f.dst)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if f.content is not None:
                dst.write_bytes(f.content)
            else:
                src = Path(f.src)  # type: ignore[arg-type]
                if not src.is_file():
                    raise SandboxError(f"staged source missing or not a file: {f.dst}")
                shutil.copyfile(src, dst)
            os.chmod(dst, 0o555 if f.executable else f.mode)
        task = sb / TASK_FILENAME
        task.write_text(manifest.task_md, encoding="utf-8")
        os.chmod(task, 0o444)
        for s in (oracle_specs if tools.transport == "cli" else ()):
            p = sb / _norm(s.cli_name)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(render_cli_shim(s), encoding="utf-8")
            os.chmod(p, 0o555)
        for d in manifest.writable_dirs:
            wd = sb / _norm(d)
            wd.mkdir(parents=True, exist_ok=True)
            os.chmod(wd, 0o755)
        os.chmod(sb, 0o755)
    except Exception:
        remove_tree(sb)
        raise
    return SandboxHandle(path=sb, manifest_sha256=sha256_tree(sb), listing=listing(sb),
                         expected=expected_listing(manifest, tools, oracle_specs), cell=cell,
                         shims=shim_names)


# -- pre-check ------------------------------------------------------------------

_GENERIC_ABS = re.compile(
    r"(?<![\w.:/~-])(/(?:Users|home|private|var|tmp|etc|opt|root|mnt|Volumes|usr|srv|dbwm|"
    r"dbwm-tools|harness|work-ro)(?:/[^\s'\"`)]*)?)(?=[\s'\"`),;]|$)")
_WINDOWS_ABS = re.compile(r"(?<![\w])[A-Za-z]:\\[^\s]*")
_HOME_REL = re.compile(r"(?<![\w/])~/")
_DOTDOT = re.compile(r"(?:^|[\s\"'`(=/\\])\.\.(?=[/\\])|(?:^|\s)\.\.(?=\s|$)", re.M)
_BREADCRUMBS = ("private/", "ANSWER_KEY", "reference.json")


def check_text(text: str, label: str, extra_paths: Iterable[str] = ()) -> list[str]:
    """Leak problems in agent-facing text (TASK.md, the prompt).

    Flags: any known host root (package checkout, data/private roots, HOME,
    temp dir, the sandbox path) or a generic absolute host path;
    a ``..`` path segment (``../`` or a bare ``..`` token; prose ellipses are
    fine); and the scorer breadcrumbs "private/", "ANSWER_KEY",
    "reference.json".
    """
    probs: list[str] = []
    known = {str(paths.PKG_ROOT), str(paths.private_root()), str(paths.data_root()),
             str(Path.home()), str(Path(tempfile.gettempdir()).resolve()),
             tempfile.gettempdir(), *[str(p) for p in extra_paths]}
    for k in sorted(k for k in known if k and k not in ("/", ".")):
        if k in text:
            probs.append(f"{label}: contains host path {k!r}")
    for m in _GENERIC_ABS.finditer(text):
        probs.append(f"{label}: contains absolute path {m.group(1)[:80]!r}")
    for m in _WINDOWS_ABS.finditer(text):
        probs.append(f"{label}: contains absolute path {m.group(0)[:80]!r}")
    if _HOME_REL.search(text):
        probs.append(f"{label}: contains a home-relative path (~/)")
    if _DOTDOT.search(text):
        probs.append(f"{label}: contains a '..' path segment")
    for b in _BREADCRUMBS:
        if b in text:
            probs.append(f"{label}: contains scorer breadcrumb {b!r}")
    return probs


def precheck(handle: SandboxHandle, *, answer_values: Sequence[object] = (),
             prompt: str | None = None, disclosed_values: Sequence[object] = ()) -> list[str]:
    """Every reason this sandbox must not be shown to an agent (empty list = clean).

    Called after build_sandbox and before the agent starts; any finding makes
    the attempt INFRA_INVALID_REGIME (never scored). Checks: the listing equals
    the manifest exactly (and contains no symlinks); no CANARY_GUID in any file
    or file name; no answer-key value (>= canary.MIN_VALUE_LEN chars) in any
    staged file, including zip members; TASK.md and the prompt pass check_text.

    ``disclosed_values`` (``dbwm.core.types.disclosed_values_for``: the arm's
    ``ArmSpec.disclosed_values`` plus the eval's per-cell hook) are strings the
    brief shows on purpose: an answer-key value that occurs only inside one of
    them is not a finding. Any other occurrence still is.
    """
    sb = handle.path
    probs: list[str] = []
    actual = listing(sb)
    if actual != handle.expected:
        extra = sorted(set(actual) - set(handle.expected))
        missing = sorted(set(handle.expected) - set(actual))
        probs.append(f"sandbox listing differs from manifest (extra={extra[:10]}, missing={missing[:10]})")
    for rel in actual:
        if not rel.endswith("/") and (sb / rel).is_symlink():
            probs.append(f"sandbox contains a symlink: {rel}")
    for hit in canary.scan_for_guid(sb):
        probs.append(f"canary GUID found in {_rel_label(hit, sb)}")
    for label, value in canary.scan_for_values(sb, answer_values, disclosed=disclosed_values):
        probs.append(f"answer-key value {value[:40]!r} found in {_rel_label(label, sb)}")
    task = sb / TASK_FILENAME
    if task.is_file():
        probs.extend(check_text(task.read_text(encoding="utf-8", errors="replace"), "TASK.md",
                                extra_paths=[str(sb), str(sb.parent)]))
    if prompt is not None:
        probs.extend(check_text(prompt, "prompt", extra_paths=[str(sb), str(sb.parent)]))
        if canary.scan_text_for_guid(prompt):
            probs.append("prompt contains the canary GUID")
        for v in canary.scan_text_for_values(prompt, answer_values, disclosed=disclosed_values):
            probs.append(f"prompt contains answer-key value {v[:40]!r}")
    return probs


def _rel_label(label: str, root: Path) -> str:
    s = str(label)
    r = str(root)
    return s[len(r) + 1:] if s.startswith(r + os.sep) else s


# -- collection and cleanup -------------------------------------------------------

_GLOB_CHARS = set("*?[")


def collect(handle: SandboxHandle | Path, deliverables: Sequence[str], dest: Path) -> dict[str, Any]:
    """Copy declared deliverables (files, directories or glob patterns) into ``dest``.

    Symlinks are never followed out of the sandbox; directory symlinks are
    skipped; files above MAX_DELIVERABLE_FILE_BYTES are skipped and reported.
    Returns a record for deliverables.json: present/missing per declared
    path, and sha256/bytes per copied file.
    """
    sb = Path(handle.path if isinstance(handle, SandboxHandle) else handle).resolve()
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    rec: dict[str, Any] = {"declared": list(deliverables), "present": {}, "missing": [],
                           "files": [], "rejected": []}

    def _inside(p: Path) -> bool:
        try:
            p.resolve().relative_to(sb)
            return True
        except ValueError:
            return False

    def _copy_file(src: Path, rel: str) -> None:
        # Resolve the WHOLE path: a symlinked parent directory (``out -> /etc``
        # then deliverable ``out/passwd``) must not pull a host file into the
        # attempt dir. Inside a container the link target is resolved on the
        # host, which is exactly why every copy is checked here.
        if not _inside(src):
            rec["rejected"].append({"path": rel, "reason": "resolves outside sandbox"})
            return
        real = src.resolve()
        if not real.is_file():
            rec["rejected"].append({"path": rel, "reason": "not a regular file"})
            return
        size = real.stat().st_size
        if size > MAX_DELIVERABLE_FILE_BYTES:
            rec["rejected"].append({"path": rel, "reason": f"too large ({size} bytes)"})
            return
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(real, out)
        os.chmod(out, 0o644)
        rec["files"].append({"path": rel, "bytes": size,
                             "sha256": hashlib.sha256(out.read_bytes()).hexdigest()})

    for decl in deliverables:
        d = _norm(decl)
        if _GLOB_CHARS & set(d):
            matches = sorted(p for p in sb.glob(d))
            matches = [m for m in matches if fnmatch.fnmatch(m.relative_to(sb).as_posix(), d)]
        else:
            matches = [sb / d] if os.path.lexists(sb / d) else []
        if not matches:
            rec["present"][decl] = False
            rec["missing"].append(decl)
            continue
        copied_before = len(rec["files"])
        for m in matches:
            rel = m.relative_to(sb).as_posix()
            if m.is_symlink() and m.is_dir():
                rec["rejected"].append({"path": rel, "reason": "directory symlink"})
            elif m.is_dir():
                for dirpath, dirnames, filenames in os.walk(m, followlinks=False):
                    dirnames.sort()
                    for fn in sorted(filenames):
                        fp = Path(dirpath) / fn
                        _copy_file(fp, fp.relative_to(sb).as_posix())
            else:
                _copy_file(m, rel)
        rec["present"][decl] = len(rec["files"]) > copied_before or any(
            m.is_dir() and not m.is_symlink() for m in matches)
        if not rec["present"][decl]:
            rec["missing"].append(decl)
    return rec


def remove_tree(p: Path) -> None:
    """rmtree that first restores owner write permission (staged files and dirs are read-only)."""
    p = Path(p)
    if not os.path.lexists(p):
        return

    def _onerror(func, path, exc_info):  # noqa: ANN001 - shutil API
        try:
            os.chmod(os.path.dirname(path), 0o700)
            os.chmod(path, 0o700)
            func(path)
        except OSError:
            pass

    for dirpath, dirnames, _ in os.walk(p, followlinks=False):
        for d in dirnames:
            full = os.path.join(dirpath, d)
            if not os.path.islink(full):
                try:
                    os.chmod(full, 0o700)
                except OSError:
                    pass
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(p, onexc=lambda func, path, exc: _onerror(func, path, None))
    else:
        shutil.rmtree(p, onerror=_onerror)


def cleanup(handle: SandboxHandle, keep: bool = False) -> None:
    if not keep:
        remove_tree(handle.path)
