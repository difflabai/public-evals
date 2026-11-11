"""CONTAINER isolation: per-trial internal network, sidecar, agent container, teardown.

Why: ``--restricted`` confines Claude Code's file tools to the sandbox but not
Bash (a ``cat`` of a path outside the cwd succeeded on 2.1.287; BUILD-SPEC
decision 6). In the paper's harness every code-enabled arm could reach the whole
host (e.g. ``find / -name reference.json``; ledger section "Leak paths" (12),
N19), so the sandboxes were path obscurity, not isolation. Any arm with bash
therefore runs here:

  network   ``docker network create --internal dbwm-net-<id>``: no route out.
  sidecar   same image; PKG mounted read-only at /dbwm, the sandbox read-only at
            /work-ro, the attempt dir read-write at /harness; runs
            ``python -m dbwm.core.sidecar`` (oracle server + egress proxy);
            started on the default bridge (egress) and then attached to the
            internal network as ``dbwm-sidecar``.
  agent     ``docker run --rm -i`` on the internal network only; read-only
            rootfs, tmpfs /tmp and HOME, the host user's uid:gid (non-root),
            all capabilities dropped, no-new-privileges, pids/memory limits;
            mounts ONLY the sandbox (rw, at /work, the cwd) and the tools dir
            (ro, at /dbwm-tools); HTTPS_PROXY=http://dbwm-sidecar:3128,
            DBWM_ORACLE=tcp://dbwm-sidecar:7700. It never sees PKG. With api_key
            or oauth_token auth it holds only a per-trial placeholder and
            ANTHROPIC_BASE_URL=http://dbwm-sidecar:8089 (the sidecar's API relay,
            which holds the real credential; dbwm.core.api_relay, RT-E).
  teardown  always: agent and sidecar removed, network removed, sidecar logs
            saved to <attempt>/sidecar.log.

The harness itself always runs on the host.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from dbwm.core import paths
from dbwm.core.env import is_secret_name

IMAGE = "dbwm-agent:1.0.0"
SIDECAR_ALIAS = "dbwm-sidecar"
ORACLE_PORT = 7700
PROXY_PORT = 3128
RELAY_PORT = 8089
RELAY_CREDENTIAL_ENV = "DBWM_RELAY_CREDENTIAL"
AGENT_WORKDIR = "/work"
TOOLS_MOUNT = "/dbwm-tools"
PKG_MOUNT = "/dbwm"
SANDBOX_RO_MOUNT = "/work-ro"
HARNESS_MOUNT = "/harness"
LABEL = "dbwm.trial=1"
DEFAULT_LIMITS = {"memory": "4g", "pids": 512, "cpus": None}
_CLIENT_ENV = ("PATH", "HOME", "USER", "DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CONTEXT",
               "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY", "XDG_RUNTIME_DIR", "TMPDIR")
MAX_EXEC_OUTPUT = 1024 * 1024


class ContainerError(RuntimeError):
    pass


def docker_bin() -> str:
    return os.environ.get("DBWM_DOCKER", "docker")


def _client_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = {k: os.environ[k] for k in _CLIENT_ENV if os.environ.get(k)}
    if extra:
        env.update(extra)
    return env


def _docker(args: Sequence[str], *, timeout: float = 120, env: Mapping[str, str] | None = None,
            input: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([docker_bin(), *args], capture_output=True, timeout=timeout,
                          env=_client_env(env), input=input)


def docker_available(timeout: float = 15) -> bool:
    try:
        r = _docker(["info", "--format", "{{.ServerVersion}}"], timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and bool(r.stdout.strip())


def image_id(image: str = IMAGE) -> str | None:
    try:
        r = _docker(["image", "inspect", "--format", "{{.Id}}", image], timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.decode().strip() or None if r.returncode == 0 else None


def image_exists(image: str = IMAGE) -> bool:
    return image_id(image) is not None


def build_image(tag: str = IMAGE, *, dockerfile: Path | None = None, context: Path | None = None,
                extra_args: Sequence[str] = ()) -> int:
    """``docker build`` the agent image from docker/Dockerfile; output streams to the terminal."""
    context = Path(context) if context else paths.PKG_ROOT / "docker"
    dockerfile = Path(dockerfile) if dockerfile else context / "Dockerfile"
    cmd = [docker_bin(), "build", "-t", tag, "-f", str(dockerfile), *extra_args, str(context)]
    return subprocess.call(cmd, env=_client_env())


def host_user() -> str:
    """uid:gid for the containers: the invoking user (so sandbox and attempt dir stay writable).

    A root harness gets nobody:nogroup instead, keeping the agent non-root.
    """
    uid, gid = os.getuid(), os.getgid()
    if uid == 0:
        return "65534:65534"
    return f"{uid}:{gid}"


def _safe(s: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "-", s.lower()).strip("-.")[:40] or "trial"


@dataclass
class ContainerTrial:
    trial_id: str
    image: str
    network: str
    sidecar: str
    agent: str
    sandbox: Path
    harness_out: Path
    tools_dir: Path
    oracle_port: int | None
    proxy_port: int
    user: str
    extra_mounts: list[tuple[str, str, str]] = field(default_factory=list)
    limits: dict = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    idle_agent: str | None = None
    torn_down: bool = False
    # tcp://127.0.0.1:<port> when the sidecar's oracle port is published to the
    # host loopback (start_trial(publish_oracle=True)): for adapters that run
    # on the host and call the oracle themselves (openai_compat). Never given
    # to the agent container.
    host_oracle_address: str | None = None
    # The sidecar's API relay port (start_trial(relay=...)), else None.
    relay_port: int | None = None

    @property
    def relay_url(self) -> str | None:
        return f"http://{SIDECAR_ALIAS}:{self.relay_port}" if self.relay_port else None

    @property
    def oracle_address(self) -> str | None:
        return f"tcp://{SIDECAR_ALIAS}:{self.oracle_port}" if self.oracle_port else None

    @property
    def proxy_url(self) -> str:
        return f"http://{SIDECAR_ALIAS}:{self.proxy_port}"

    # -- agent container ----------------------------------------------------------

    def _agent_flags(self, name: str) -> list[str]:
        flags = ["--name", name, "--label", LABEL, "--init", "--network", self.network,
                 "--read-only",
                 "--tmpfs", "/tmp:rw,exec,mode=1777,size=1g",
                 "--tmpfs", "/home/agent:rw,exec,mode=1777,size=512m",
                 "--user", self.user, "--cap-drop", "ALL",
                 "--security-opt", "no-new-privileges",
                 "-v", f"{self.sandbox}:{AGENT_WORKDIR}:rw",
                 "-v", f"{self.tools_dir}:{TOOLS_MOUNT}:ro",
                 "-w", AGENT_WORKDIR]
        if self.limits.get("pids"):
            flags += ["--pids-limit", str(self.limits["pids"])]
        if self.limits.get("memory"):
            flags += ["--memory", str(self.limits["memory"])]
        if self.limits.get("cpus"):
            flags += ["--cpus", str(self.limits["cpus"])]
        for host, target, mode in self.extra_mounts:
            flags += ["-v", f"{host}:{target}:{mode}"]
        return flags

    @staticmethod
    def _env_flags(env: Mapping[str, str]) -> tuple[list[str], dict[str, str]]:
        """``-e`` flags; credential values travel in the docker client env, never in argv."""
        flags: list[str] = []
        secret_vals: dict[str, str] = {}
        for k in sorted(env):
            if is_secret_name(k):
                flags += ["-e", k]
                secret_vals[k] = env[k]
            else:
                flags += ["-e", f"{k}={env[k]}"]
        return flags, secret_vals

    def agent_command(self, inner_argv: Sequence[str], env: Mapping[str, str]) -> tuple[list[str], dict[str, str]]:
        """(argv, client_env) to run ``inner_argv`` in a fresh agent container (stdin attached)."""
        eflags, secret_vals = self._env_flags(env)
        argv = [docker_bin(), "run", "--rm", "-i", *self._agent_flags(self.agent), *eflags,
                self.image, *inner_argv]
        return argv, _client_env(secret_vals)

    def kill_agent(self) -> None:
        for name in {self.agent, self.idle_agent} - {None}:
            try:
                _docker(["kill", name], timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def ensure_idle_agent(self, env: Mapping[str, str]) -> str:
        """Start (once) a long-lived agent container for exec-style adapters; return its name.

        Same mounts, limits and network as the interactive agent container,
        running ``sleep infinity``; commands go through exec_in_agent().
        """
        if self.idle_agent:
            return self.idle_agent
        name = self.agent + "-x"
        eflags, secret_vals = self._env_flags(env)
        r = _docker(["run", "-d", *self._agent_flags(name), *eflags, self.image, "sleep", "infinity"],
                    timeout=120, env=secret_vals)
        if r.returncode != 0:
            raise ContainerError(f"could not start agent container: {r.stderr.decode(errors='replace')}")
        self.idle_agent = name
        return name

    def run_once(self, inner_argv: Sequence[str], env: Mapping[str, str] | None = None,
                 timeout: float = 60) -> tuple[int, str, str]:
        """Run a short command in a throwaway container with no network (e.g. ``claude --version``)."""
        eflags, secret_vals = self._env_flags(env or {})
        flags = ["--rm", "--label", LABEL, "--network", "none", "--read-only",
                 "--tmpfs", "/tmp:rw,mode=1777", "--tmpfs", "/home/agent:rw,mode=1777",
                 "--user", self.user, "--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
        for host, target, mode in self.extra_mounts:
            flags += ["-v", f"{host}:{target}:{mode}"]
        try:
            r = _docker(["run", *flags, *eflags, self.image, *inner_argv], timeout=timeout,
                        env=secret_vals)
        except subprocess.TimeoutExpired:
            return 124, "", "timeout"
        return r.returncode, r.stdout.decode(errors="replace"), r.stderr.decode(errors="replace")

    # -- teardown -------------------------------------------------------------------

    def teardown(self) -> None:
        if self.torn_down:
            return
        self.torn_down = True
        try:
            self._teardown_containers()
        finally:
            self._archive_tools_dir()

    def _archive_tools_dir(self) -> None:
        """Copy the staged tools dir into ``<attempt>/tools`` (the record) and delete the stage.

        The tools dir is staged beside the sandbox, not under ``runs/`` (red-team
        RT-M: ``/proc/self/mountinfo`` in the agent container shows the host
        source path of every bind mount, and the default runs root is inside the
        operator's home).
        """
        stage = self.tools_dir
        dest = self.harness_out / "tools"
        if stage == dest or not stage.exists():
            return
        try:
            shutil.copytree(stage, dest, symlinks=True, dirs_exist_ok=True)
        except OSError:
            pass
        shutil.rmtree(stage, ignore_errors=True)

    def _teardown_containers(self) -> None:
        try:
            logs = _docker(["logs", self.sidecar], timeout=30)
            (self.harness_out / "sidecar.log").write_bytes(logs.stdout + logs.stderr)
        except (OSError, subprocess.TimeoutExpired):
            pass
        for name in (self.agent, self.idle_agent, self.sidecar):
            if name:
                try:
                    _docker(["rm", "-f", name], timeout=60)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        problems: list[str] = []
        for i in range(10):
            try:
                r = _docker(["network", "rm", self.network], timeout=60)
                if r.returncode == 0 or b"not found" in r.stderr:
                    problems = []
                    break
                problems.append(r.stderr.decode(errors="replace").strip())
            except (OSError, subprocess.TimeoutExpired) as e:
                problems.append(str(e))
            time.sleep(min(0.25 * (i + 1), 2.0))
        if problems:
            try:
                (self.harness_out / "teardown_errors.txt").write_text("\n".join(problems) + "\n")
            except OSError:
                pass


def exec_in_agent(container_name: str, argv: Sequence[str], timeout_s: float) -> tuple[int, str, str]:
    """Run ``argv`` in a running agent container (cwd /work); return (rc, stdout, stderr).

    The command is wrapped in coreutils ``timeout`` inside the container so a
    runaway is killed there (rc 124), with a host-side backstop. Output is
    truncated to 1 MiB per stream. Used by the openai_compat adapter's
    run_bash at CONTAINER isolation (see ContainerTrial.ensure_idle_agent).
    """
    t = max(1, int(timeout_s))
    cmd = ["exec", "-w", AGENT_WORKDIR, container_name, "timeout", "-k", "5", str(t), *argv]
    try:
        r = _docker(cmd, timeout=t + 30)
    except subprocess.TimeoutExpired:
        return 124, "", f"command timed out after {t}s"
    out = r.stdout[:MAX_EXEC_OUTPUT].decode("utf-8", "replace")
    err = r.stderr[:MAX_EXEC_OUTPUT].decode("utf-8", "replace")
    if len(r.stdout) > MAX_EXEC_OUTPUT:
        out += "\n[output truncated]"
    return r.returncode, out, err


def _env_dir_mounts() -> tuple[list[str], list[str]]:
    """Mount user overrides of DBWM_{DATA,PRIVATE}_DIR into the sidecar."""
    vflags: list[str] = []
    eflags: list[str] = []
    for var, sub in (("DBWM_DATA_DIR", "data"), ("DBWM_PRIVATE_DIR", "private")):
        val = os.environ.get(var)
        if val:
            target = f"/dbwm-env/{sub}"
            vflags += ["-v", f"{Path(val).expanduser().resolve()}:{target}:ro"]
            eflags += ["-e", f"{var}={target}"]
    return vflags, eflags


def _stage_tools_dir() -> Path:
    """A fresh tools dir beside the sandboxes (DBWM_SANDBOX_ROOT or the system temp dir).

    Not under the attempt dir: the agent container can read its bind-mount
    sources from /proc/self/mountinfo (red-team RT-M). Archived into
    ``<attempt>/tools`` at teardown.
    """
    base = paths.sandbox_root()
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="dbwm-tools-", dir=str(base))).resolve()


def network_ipv4(name: str, field: str = "Subnet") -> str | None:
    """The first IPv4 ``Subnet`` (or ``Gateway``) of a docker network, or None."""
    r = _docker(["network", "inspect", "-f", "{{range .IPAM.Config}}{{.%s}} {{end}}" % field, name], timeout=30)
    if r.returncode != 0:
        return None
    for tok in r.stdout.decode(errors="replace").split():
        if "." in tok:  # IPv4 (skip an IPv6 entry)
            return tok
    return None


def start_trial(*, trial_id: str, sandbox: Path, harness_out: Path, oracle_config: dict | None,
                egress_allow: Sequence[str], image: str = IMAGE,
                extra_mounts: Sequence[tuple[str, str, str]] = (), limits: Mapping | None = None,
                ready_timeout_s: float = 90, publish_oracle: bool = False,
                relay: Mapping | None = None, relay_credential: str | None = None) -> ContainerTrial:
    """Create the network and sidecar for one attempt and wait until the sidecar is serving.

    ``oracle_config`` is a server_from_config dict WITHOUT host/port/sandbox/
    log_path (filled in here with the container paths); None when the arm has
    no oracle tools (the sidecar then runs only the proxy). ``publish_oracle``
    also publishes the oracle port on the host loopback (127.0.0.1, random
    port) and sets ``trial.host_oracle_address``. ``relay`` (auth_header, pin,
    extra_models, upstream, placeholder) starts the sidecar's API relay;
    ``relay_credential`` is the real credential, passed to the sidecar in the
    docker client environment only (never argv, never sealed.json).
    """
    if relay is not None and not relay_credential:
        raise ContainerError("the API relay needs the real credential")
    harness_out = Path(harness_out).resolve()
    sandbox = Path(sandbox).resolve()
    suffix = f"{_safe(trial_id)}-{secrets.token_hex(3)}"
    trial = ContainerTrial(
        trial_id=trial_id, image=image, network=f"dbwm-net-{suffix}", sidecar=f"dbwm-sc-{suffix}",
        agent=f"dbwm-agent-{suffix}", sandbox=sandbox, harness_out=harness_out,
        tools_dir=_stage_tools_dir(), oracle_port=ORACLE_PORT if oracle_config else None,
        proxy_port=PROXY_PORT, user=host_user(), extra_mounts=list(extra_mounts),
        limits={**DEFAULT_LIMITS, **dict(limits or {})})
    trial.tools_dir.mkdir(parents=True, exist_ok=True)
    (harness_out / "container.json").write_text(json.dumps({
        "image": image, "network": trial.network, "sidecar": trial.sidecar, "agent": trial.agent,
        "user": trial.user, "limits": trial.limits, "extra_mounts": [list(m) for m in trial.extra_mounts],
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sealed: dict = {
        "proxy": {"allow": list(egress_allow), "port": PROXY_PORT,
                  "log_path": f"{HARNESS_MOUNT}/egress.jsonl"},
        "oracle": None,
        "ready_file": f"{HARNESS_MOUNT}/sidecar-ready.json",
        "error_file": f"{HARNESS_MOUNT}/sidecar-error.txt",
    }
    if oracle_config:
        sealed["oracle"] = {**oracle_config, "host": "0.0.0.0", "port": ORACLE_PORT,
                            "sandbox": SANDBOX_RO_MOUNT,
                            "log_path": f"{HARNESS_MOUNT}/oracle_calls.jsonl"}
    relay_env: dict[str, str] = {}
    rflags: list[str] = []
    if relay is not None:
        sealed["relay"] = {**{k: v for k, v in dict(relay).items() if k != "credential"},
                           "port": RELAY_PORT, "credential_env": RELAY_CREDENTIAL_ENV,
                           "log_path": f"{HARNESS_MOUNT}/api_relay.jsonl"}
        relay_env = {RELAY_CREDENTIAL_ENV: str(relay_credential)}
        rflags = ["-e", RELAY_CREDENTIAL_ENV]
    cfg_path = harness_out / "sealed.json"  # written once the network's subnet is known
    ready = harness_out / "sidecar-ready.json"
    if ready.exists():
        ready.unlink()
    try:
        r = _docker(["network", "create", "--internal", "--label", LABEL, trial.network], timeout=60)
        if r.returncode != 0:
            raise ContainerError(f"docker network create failed: {r.stderr.decode(errors='replace')}")
        # Review m1: the sidecar listens only on the trial network (it is also on the
        # default bridge, where any other container could otherwise reach it).
        subnet = network_ipv4(trial.network, "Subnet")
        if not subnet:
            raise ContainerError(f"could not read the subnet of docker network {trial.network}")
        sealed["bind_subnet"] = subnet
        pflags = ["-p", f"127.0.0.1::{ORACLE_PORT}"] if (publish_oracle and oracle_config) else []
        if pflags:
            gw = network_ipv4("bridge", "Gateway")
            if not gw:
                raise ContainerError("could not read the gateway of the default bridge network")
            sealed["publish_gateway"] = gw
        fd = os.open(cfg_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(sealed, fh, indent=2, sort_keys=True)
        vflags, eflags = _env_dir_mounts()
        r = _docker(["run", "-d", "--name", trial.sidecar, "--label", LABEL, "--init", *pflags,
                     "--read-only", "--tmpfs", "/tmp:rw,mode=1777",
                     "--user", trial.user, "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                     "--network", "bridge",
                     "-v", f"{paths.PKG_ROOT}:{PKG_MOUNT}:ro",
                     "-v", f"{sandbox}:{SANDBOX_RO_MOUNT}:ro",
                     "-v", f"{harness_out}:{HARNESS_MOUNT}:rw",
                     *vflags,
                     "-e", f"PYTHONPATH={PKG_MOUNT}", "-e", "PYTHONDONTWRITEBYTECODE=1",
                     "-e", "HOME=/tmp", *eflags, *rflags,
                     "-w", PKG_MOUNT,
                     image, "python", "-m", "dbwm.core.sidecar", "--config", f"{HARNESS_MOUNT}/sealed.json"],
                    timeout=120, env=relay_env)
        if r.returncode != 0:
            raise ContainerError(f"sidecar start failed: {r.stderr.decode(errors='replace')}")
        # Started on the default bridge (egress; and port publishing, which an
        # --internal network cannot do), then attached to the trial's internal
        # network under the alias the agent uses.
        r = _docker(["network", "connect", "--alias", SIDECAR_ALIAS, trial.network, trial.sidecar], timeout=60)
        if r.returncode != 0:
            raise ContainerError(f"sidecar internal network connect failed: {r.stderr.decode(errors='replace')}")
        deadline = time.monotonic() + ready_timeout_s
        while not ready.exists():
            if time.monotonic() > deadline:
                raise ContainerError("sidecar did not become ready in time")
            st = _docker(["inspect", "--format", "{{.State.Running}}", trial.sidecar], timeout=30)
            if st.stdout.decode().strip() == "false":
                err = harness_out / "sidecar-error.txt"
                detail = err.read_text(encoding="utf-8")[-2000:] if err.exists() else ""
                raise ContainerError(f"sidecar exited before ready. {detail}")
            time.sleep(0.25)
        info = json.loads(ready.read_text(encoding="utf-8"))
        trial.proxy_port = int(info.get("proxy_port") or PROXY_PORT)
        if oracle_config:
            trial.oracle_port = int(info["oracle_port"])
        if relay is not None:
            if not info.get("relay_port"):
                raise ContainerError("sidecar did not start the API relay")
            trial.relay_port = int(info["relay_port"])
        if pflags:
            r = _docker(["port", trial.sidecar, f"{ORACLE_PORT}/tcp"], timeout=30)
            hostport = r.stdout.decode().split()[0] if r.returncode == 0 and r.stdout.strip() else ""
            host, _, port = hostport.rpartition(":")
            if not port.isdigit():
                raise ContainerError(f"could not read the published oracle port: {r.stderr.decode(errors='replace')}")
            trial.host_oracle_address = f"tcp://127.0.0.1:{int(port)}"
    except Exception:
        trial.teardown()
        raise
    return trial


def remove_stale(label: str = LABEL) -> int:
    """Remove leftover dbwm containers and networks (after a crashed harness). Returns count."""
    n = 0
    r = _docker(["ps", "-aq", "--filter", f"label={label}"], timeout=60)
    ids = r.stdout.decode().split()
    if ids:
        _docker(["rm", "-f", *ids], timeout=120)
        n += len(ids)
    r = _docker(["network", "ls", "-q", "--filter", f"label={label}"], timeout=60)
    nets = r.stdout.decode().split()
    for net in nets:
        _docker(["network", "rm", net], timeout=60)
        n += 1
    return n
