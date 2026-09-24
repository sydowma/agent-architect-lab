"""Day 23: Isolated Sandbox & Local Execution Governance (Refactored).

Faithfully reproduces the sandbox governance principles from Harness Books Book 2 Ch 3
and the OpenAI Self-hosted Sandbox specification:

1. BaseExecutionSandbox Contract (start / exec_command / snapshot / cleanup).
2. Immutable SandboxSecurityPolicy with hard-coded red lines (network=none, memory=256m, pids=64).
3. DualKeyPrivilegeBroker (host master key never enters the sandbox; only a scoped EXECUTOR_KEY).
4. OutboundReverseConnection (sandbox exposes zero inbound ports, dials out via WSS only).
5. LocalDockerSandbox (a concrete, self-hostable Docker/Podman driver).
6. SimulatedIsolatedSandbox (a pure Python, zero-dependency sandbox for hermetic tests & fallbacks).
7. SandboxedToolExecutor (bridges isolated sandboxes directly to Agent loop tool execution).
8. SandboxLifecycleManager (Session vs Compute decoupling, JIT lazy provisioning, idle reaping).
9. SandboxInvariantEnforcer (hard red lines, env isolation, result consistency).
10. SandboxOverheadProfiler (quantifies whether sandboxing every turn is affordable).
"""

from __future__ import annotations

import abc
import copy
import hashlib
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ============================================================================
# 0. 异常与进程后端 (Exceptions & Process Backend)
# ============================================================================

class SandboxError(Exception):
    """Base class for all sandbox lifecycle/execution failures."""


class SandboxSecurityViolation(SandboxError):
    """Raised when a hard red line or privilege isolation invariant is broken."""


class SandboxNotRunningError(SandboxError):
    """Raised when a command is dispatched against a non-RUNNING sandbox."""


class ProcessBackend(abc.ABC):
    """Low-level process launcher, injectable for deterministic testing."""

    @abc.abstractmethod
    def run(
        self,
        argv: List[str],
        env: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ) -> Tuple[int, str, str]:
        """Runs argv and returns (exit_code, stdout, stderr).
        
        Convention: exit_code 124 means timeout, 137 means SIGKILL (OOM killer).
        """
        raise NotImplementedError


class SubprocessBackend(ProcessBackend):
    """Real backend used against a local Docker daemon or host subprocess."""

    def run(
        self,
        argv: List[str],
        env: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ) -> Tuple[int, str, str]:
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env if env is not None else os.environ.copy(),
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or ""
            err = exc.stderr or ""
            if isinstance(out, bytes):
                out = out.decode("utf-8", "replace")
            if isinstance(err, bytes):
                err = err.decode("utf-8", "replace")
            return 124, out, err + "\n[backend] command exceeded timeout"
        except FileNotFoundError as exc:
            return 127, "", f"[backend] executable not found: {exc}"


# ============================================================================
# 1. 安全策略与硬性红线 (SandboxSecurityPolicy)
# ============================================================================

class NetworkMode(str, Enum):
    NONE = "none"          # 物理断网，纯数据处理与代码验证的默认红线
    INTERNAL = "internal"  # 仅专用内网白名单网桥
    BRIDGED = "bridge"     # 公网可达，架构红线禁止


@dataclass(frozen=True)
class SandboxSecurityPolicy:
    """Immutable execution constraints. Frozen so no layer can silently weaken it."""

    network_mode: NetworkMode = NetworkMode.NONE
    memory_limit_mb: int = 256
    pids_limit: int = 64
    cpu_limit: float = 1.0
    read_only_rootfs: bool = True
    tmpfs_size_mb: int = 64
    infinite_loop_timeout_s: int = 30

    # 双密钥降权：宿主主密钥黑名单，沙箱仅有低权限握手 Token
    forbidden_env_keys: Tuple[str, ...] = (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_ACCESS_KEY_ID",
        "DATABASE_URL",
        "GITHUB_TOKEN",
    )
    executor_key_name: str = "EXECUTOR_KEY"

    def red_lines(self) -> Dict[str, str]:
        """The three absolute physical defense lines."""
        return {
            "network": f"--network={self.network_mode.value}",
            "memory": f"--memory={self.memory_limit_mb}m",
            "pids": f"--pids-limit={self.pids_limit}",
        }

    def to_docker_flags(self) -> List[str]:
        flags: List[str] = [
            "--network", self.network_mode.value,
            "--memory", f"{self.memory_limit_mb}m",
            "--memory-swap", f"{self.memory_limit_mb}m",
            "--pids-limit", str(self.pids_limit),
            "--cpus", str(self.cpu_limit),
        ]
        if self.read_only_rootfs:
            flags.append("--read-only")
            flags += ["--tmpfs", f"/tmp:size={self.tmpfs_size_mb}m"]
        return flags


# ============================================================================
# 2. 双密钥降权中间人 (DualKeyPrivilegeBroker)
# ============================================================================

class DualKeyPrivilegeBroker:
    """Host holds the master key; the sandbox receives only a scoped executor token."""

    def __init__(self, host_secrets: Dict[str, str], policy: Optional[SandboxSecurityPolicy] = None):
        self._host_secrets = dict(host_secrets)
        self.policy = policy or SandboxSecurityPolicy()

    def issue_executor_env(self, session_id: str) -> Dict[str, str]:
        """Produces a sanitized env containing only a deterministic, scope-limited token."""
        digest = hashlib.sha256(f"executor::{session_id}".encode("utf-8")).hexdigest()[:24]
        token = f"exec-{session_id}-{digest}"
        return {self.policy.executor_key_name: token}

    def assert_no_master_key_leak(self, env: Dict[str, str]) -> None:
        leaked = [k for k in self.policy.forbidden_env_keys if k in env]
        if leaked:
            raise SandboxSecurityViolation(
                f"Dual-Key Violation: host master secrets leaked into sandbox env: {leaked}"
            )


# ============================================================================
# 3. 纯出网反向连接契约 (OutboundReverseConnection)
# ============================================================================

@dataclass
class ConnectionContract:
    session_id: str
    protocol: str
    inbound_ports: List[int]
    outbound_endpoint: str


class OutboundReverseConnection:
    """Models the outbound-only WSS topology: the sandbox never listens for inbound traffic."""

    def __init__(self, control_plane_endpoint: str = "wss://harness.internal/executor"):
        self.control_plane_endpoint = control_plane_endpoint

    def contract(self, session_id: str) -> ConnectionContract:
        return ConnectionContract(
            session_id=session_id,
            protocol="wss",
            inbound_ports=[],
            outbound_endpoint=self.control_plane_endpoint,
        )

    @staticmethod
    def assert_no_inbound_binding(contract: ConnectionContract) -> None:
        if contract.inbound_ports:
            raise SandboxSecurityViolation(
                f"Inbound Exposure Violation: sandbox binds ports {contract.inbound_ports}, "
                "which are reachable by network scanners."
            )


# ============================================================================
# 4. 执行结果与沙箱抽象契约 (ExecutionResult & BaseExecutionSandbox)
# ============================================================================

OOM_EXIT_CODE = 137
TIMEOUT_EXIT_CODE = 124


@dataclass
class ExecutionResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    command: str = ""
    timed_out: bool = False
    oom_killed: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.oom_killed

    def render_observation(self) -> str:
        """Renders raw sandbox execution into a clean observation for the Agent loop."""
        if self.oom_killed:
            note = f"[Sandbox OOM] exit code {OOM_EXIT_CODE}: memory limit exceeded, host protected."
        elif self.timed_out:
            note = f"[Sandbox Timeout] exit code {TIMEOUT_EXIT_CODE}: command exceeded the wall-clock limit."
        elif self.exit_code != 0:
            note = f"[Sandbox Exit {self.exit_code}]"
        else:
            note = "[Sandbox OK]"

        body = self.stdout.strip()
        err = self.stderr.strip()
        if err:
            body = f"{body}\nSTDERR: {err}".strip()
        return f"{note}\n{body}".strip()


class SandboxState(str, Enum):
    UNINITIALIZED = "uninitialized"
    RUNNING = "running"
    SUSPENDED = "suspended"
    DESTROYED = "destroyed"


class BaseExecutionSandbox(abc.ABC):
    """Industrial isolation contract: Harness depends only on this, never on bare subprocess."""

    @abc.abstractmethod
    def start(self) -> None:
        """Provision or wake the isolated environment."""

    @abc.abstractmethod
    def exec_command(self, cmd: str, timeout: int = 30) -> ExecutionResult:
        """Run a command inside the controlled environment."""

    @abc.abstractmethod
    def snapshot(self) -> str:
        """Capture a filesystem snapshot supporting time travel/rollback."""

    @abc.abstractmethod
    def cleanup(self) -> None:
        """Securely destroy the environment and erase ephemeral state."""


# ============================================================================
# 5. 本地受控 Docker 沙箱驱动 (LocalDockerSandbox)
# ============================================================================

class LocalDockerSandbox(BaseExecutionSandbox):
    """Self-hostable Docker/Podman driver with hard-coded red lines."""

    def __init__(
        self,
        image: str = "python:3.12-slim",
        workspace_dir: str = "/tmp/mini-harness-workspace",
        policy: Optional[SandboxSecurityPolicy] = None,
        broker: Optional[DualKeyPrivilegeBroker] = None,
        backend: Optional[ProcessBackend] = None,
        session_id: Optional[str] = None,
        clock: Callable[[], float] = time.monotonic,
        container_name: Optional[str] = None,
    ):
        self.image = image
        self.workspace_dir = workspace_dir
        self.policy = policy or SandboxSecurityPolicy()
        self.broker = broker or DualKeyPrivilegeBroker(host_secrets={}, policy=self.policy)
        self.backend = backend or SubprocessBackend()
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.clock = clock
        self.container_name = container_name or f"mini-harness-sbx-{self.session_id}"
        self.state = SandboxState.UNINITIALIZED
        self.snapshots: List[str] = []
        self._exec_env: Dict[str, str] = {}

    def build_run_args(self) -> List[str]:
        env = self.broker.issue_executor_env(self.session_id)
        self.broker.assert_no_master_key_leak(env)
        args = ["docker", "run", "-d", "--name", self.container_name]
        args += self.policy.to_docker_flags()
        args += [
            "-v", f"{self.workspace_dir}:/workspace",
            "-w", "/workspace",
            "-e", f"{self.policy.executor_key_name}={env[self.policy.executor_key_name]}",
            self.image,
            "sleep", "infinity",
        ]
        return args

    def build_exec_args(self, cmd: str) -> List[str]:
        return [
            "docker", "exec",
            "-w", "/workspace",
            self.container_name,
            "/bin/sh", "-lc", cmd,
        ]

    def start(self) -> None:
        if self.state == SandboxState.RUNNING:
            return
        self._exec_env = self.broker.issue_executor_env(self.session_id)
        self.broker.assert_no_master_key_leak(self._exec_env)
        code, out, err = self.backend.run(self.build_run_args(), timeout=60)
        if code != 0:
            raise SandboxError(f"Sandbox provisioning failed ({code}): {err.strip() or out.strip()}")
        self.state = SandboxState.RUNNING

    def exec_command(self, cmd: str, timeout: int = 30) -> ExecutionResult:
        if self.state != SandboxState.RUNNING:
            raise SandboxNotRunningError(
                f"Cannot exec while sandbox is '{self.state.value}'. Call start() first."
            )
        t0 = self.clock()
        code, out, err = self.backend.run(self.build_exec_args(cmd), env=None, timeout=timeout)
        duration_ms = (self.clock() - t0) * 1000.0
        return ExecutionResult(
            exit_code=code,
            stdout=out,
            stderr=err,
            duration_ms=duration_ms,
            command=cmd,
            timed_out=(code == TIMEOUT_EXIT_CODE),
            oom_killed=(code == OOM_EXIT_CODE),
        )

    def snapshot(self) -> str:
        snap = f"{self.container_name}-snap{len(self.snapshots) + 1}:latest"
        self.backend.run(["docker", "commit", self.container_name, snap], timeout=60)
        self.snapshots.append(snap)
        return snap

    def cleanup(self) -> None:
        if self.state in (SandboxState.DESTROYED, SandboxState.UNINITIALIZED):
            self.state = SandboxState.DESTROYED
            return
        self.backend.run(["docker", "rm", "-f", self.container_name], timeout=30)
        self.state = SandboxState.DESTROYED


# ============================================================================
# 6. 零依赖模拟沙箱驱动 (SimulatedIsolatedSandbox)
# ============================================================================

class SimulatedIsolatedSandbox(BaseExecutionSandbox):
    """Pure Python, zero-dependency sandbox that enforces red lines in-process.

    Simulates network blocking, OOM killing, fork bomb limits, and snapshot rollback
    for environments where Docker is not available.
    """

    def __init__(
        self,
        session_id: str = "sim-sess",
        policy: Optional[SandboxSecurityPolicy] = None,
        broker: Optional[DualKeyPrivilegeBroker] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.session_id = session_id
        self.policy = policy or SandboxSecurityPolicy()
        self.broker = broker or DualKeyPrivilegeBroker(host_secrets={}, policy=self.policy)
        self.clock = clock
        self.state = SandboxState.UNINITIALIZED
        self.snapshots: List[str] = []
        self.virtual_fs: Dict[str, str] = {}
        self._env: Dict[str, str] = {}

    def start(self) -> None:
        if self.state == SandboxState.RUNNING:
            return
        self._env = self.broker.issue_executor_env(self.session_id)
        self.broker.assert_no_master_key_leak(self._env)
        self.state = SandboxState.RUNNING

    def exec_command(self, cmd: str, timeout: int = 30) -> ExecutionResult:
        if self.state != SandboxState.RUNNING:
            raise SandboxNotRunningError(
                f"Cannot exec while sandbox is '{self.state.value}'. Call start() first."
            )

        t0 = self.clock()

        # 1. 物理断网仿真 (Network Red Line)
        if self.policy.network_mode == NetworkMode.NONE:
            net_keywords = ["curl", "wget", "urllib", "requests", "ping", "socket", "http://", "https://"]
            if any(kw in cmd for kw in net_keywords):
                return ExecutionResult(
                    exit_code=1,
                    stderr="curl: (6) Could not resolve host: Network is unreachable (network=none enforced)",
                    duration_ms=(self.clock() - t0) * 1000.0,
                    command=cmd,
                )

        # 2. 内存超额仿真 (Memory OOM Red Line)
        if "bytearray(" in cmd or "memory_hog" in cmd or "malloc" in cmd:
            # Check if allocated memory exceeds limit
            match = re.search(r"bytearray\((\d+)", cmd)
            if match:
                size_bytes = int(match.group(1))
                if size_bytes > self.policy.memory_limit_mb * 1024 * 1024:
                    return ExecutionResult(
                        exit_code=OOM_EXIT_CODE,
                        stderr="Killed (Linux OOM Killer: memory limit exceeded)",
                        duration_ms=(self.clock() - t0) * 1000.0,
                        command=cmd,
                        oom_killed=True,
                    )

        # 3. 超时/死循环仿真 (Timeout Red Line)
        if "while true" in cmd or "sleep 100" in cmd or timeout <= 0:
            return ExecutionResult(
                exit_code=TIMEOUT_EXIT_CODE,
                stderr="Command timed out after execution limit",
                duration_ms=(self.clock() - t0) * 1000.0,
                command=cmd,
                timed_out=True,
            )

        # 4. 进程分叉炸弹仿真 (PIDs Limit Red Line)
        if ":(){ :|:& };:" in cmd or "fork_bomb" in cmd:
            return ExecutionResult(
                exit_code=1,
                stderr="-sh: fork: Resource temporarily unavailable (pids-limit=64 hit)",
                duration_ms=(self.clock() - t0) * 1000.0,
                command=cmd,
            )

        # 5. 常规安全执行仿真
        if cmd.startswith("echo "):
            out = cmd[5:].strip().strip('"').strip("'")
            return ExecutionResult(
                exit_code=0,
                stdout=out,
                duration_ms=(self.clock() - t0) * 1000.0,
                command=cmd,
            )

        if cmd.startswith("touch "):
            filename = cmd[6:].strip()
            self.virtual_fs[filename] = ""
            return ExecutionResult(exit_code=0, stdout="", duration_ms=(self.clock() - t0) * 1000.0, command=cmd)

        if cmd.startswith("ls"):
            out = "\n".join(sorted(self.virtual_fs.keys()))
            return ExecutionResult(exit_code=0, stdout=out, duration_ms=(self.clock() - t0) * 1000.0, command=cmd)

        return ExecutionResult(
            exit_code=0,
            stdout=f"simulated execution of: {cmd}",
            duration_ms=(self.clock() - t0) * 1000.0,
            command=cmd,
        )

    def snapshot(self) -> str:
        snap_id = f"snap_{self.session_id}_{len(self.snapshots) + 1}"
        self.snapshots.append(snap_id)
        return snap_id

    def cleanup(self) -> None:
        self.virtual_fs.clear()
        self.state = SandboxState.DESTROYED


# ============================================================================
# 7. 沙箱化工具执行器适配器 (SandboxedToolExecutor)
# ============================================================================

class SandboxedToolExecutor:
    """Bridges isolated execution sandboxes directly into Agent loop tool calls."""

    def __init__(self, sandbox: BaseExecutionSandbox, timeout: int = 30):
        self.sandbox = sandbox
        self.timeout = timeout
        self.execution_count = 0

    def run(self, cmd: str) -> str:
        """Executes a bash command in the sandbox and formats it into an Agent Observation."""
        self.sandbox.start()
        res = self.sandbox.exec_command(cmd, timeout=self.timeout)
        self.execution_count += 1
        return res.render_observation()


# ============================================================================
# 8. 会话与算力解耦的生命周期管理器 (SandboxLifecycleManager)
# ============================================================================

@dataclass
class SessionComputeSlot:
    session_id: str
    sandbox: Optional[BaseExecutionSandbox] = None
    last_active: float = 0.0
    provisions: int = 0
    snapshots: List[str] = field(default_factory=list)


class SandboxLifecycleManager:
    """JIT lazy provisioning: sessions are long-lived, compute is short-lived and reaped."""

    def __init__(
        self,
        sandbox_factory: Callable[[str], BaseExecutionSandbox],
        idle_timeout_s: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.sandbox_factory = sandbox_factory
        self.idle_timeout_s = idle_timeout_s
        self.clock = clock
        self._slots: Dict[str, SessionComputeSlot] = {}

    def _slot(self, session_id: str) -> SessionComputeSlot:
        if session_id not in self._slots:
            self._slots[session_id] = SessionComputeSlot(session_id=session_id)
        return self._slots[session_id]

    def _wake(self, slot: SessionComputeSlot) -> BaseExecutionSandbox:
        if slot.sandbox is None:
            sandbox = self.sandbox_factory(slot.session_id)
            sandbox.start()
            slot.sandbox = sandbox
            slot.provisions += 1
        slot.last_active = self.clock()
        return slot.sandbox

    def execute(self, session_id: str, cmd: str, timeout: int = 30) -> ExecutionResult:
        slot = self._slot(session_id)
        sandbox = self._wake(slot)
        result = sandbox.exec_command(cmd, timeout=timeout)
        slot.last_active = self.clock()
        return result

    def release_idle(self) -> List[str]:
        """Snapshot then destroy compute for every idle session. Returns reaped session ids."""
        now = self.clock()
        reaped: List[str] = []
        for session_id, slot in self._slots.items():
            if slot.sandbox is None:
                continue
            if now - slot.last_active >= self.idle_timeout_s:
                slot.snapshots.append(slot.sandbox.snapshot())
                slot.sandbox.cleanup()
                slot.sandbox = None
                reaped.append(session_id)
        return reaped

    def active_compute_count(self) -> int:
        return sum(1 for s in self._slots.values() if s.sandbox is not None)

    def provision_count(self, session_id: str) -> int:
        return self._slot(session_id).provisions


# ============================================================================
# 9. 不变式强制检查器 (SandboxInvariantEnforcer)
# ============================================================================

class SandboxInvariantEnforcer:
    """Formal verification checks for sandbox security guarantees."""

    @staticmethod
    def verify_hard_red_lines(policy: SandboxSecurityPolicy) -> None:
        """Asserts that the 3 absolute red lines cannot be silently downgraded."""
        if policy.network_mode != NetworkMode.NONE:
            raise SandboxSecurityViolation(
                f"Red Line Breached: network_mode={policy.network_mode} is not NONE. Exfiltration possible."
            )
        if policy.memory_limit_mb > 256:
            raise SandboxSecurityViolation(
                f"Red Line Breached: memory_limit_mb={policy.memory_limit_mb} > 256. Risk of host OOM."
            )
        if policy.pids_limit > 64:
            raise SandboxSecurityViolation(
                f"Red Line Breached: pids_limit={policy.pids_limit} > 64. Risk of fork bomb."
            )

    @staticmethod
    def verify_env_isolation(env: Dict[str, str], policy: SandboxSecurityPolicy) -> None:
        """Asserts that sandbox env contains zero host credentials and exactly one executor token."""
        for key in policy.forbidden_env_keys:
            if key in env:
                raise SandboxSecurityViolation(f"Credential Leak: forbidden env key '{key}' found in sandbox.")
        if policy.executor_key_name not in env:
            raise SandboxSecurityViolation("Privilege Broker Error: missing scoped EXECUTOR_KEY in sandbox.")

    @staticmethod
    def verify_result_consistency(result: ExecutionResult) -> None:
        """Asserts exit codes match semantic flags."""
        if result.oom_killed and result.exit_code != OOM_EXIT_CODE:
            raise SandboxSecurityViolation(f"Result Invariant: oom_killed=True but exit_code={result.exit_code} != 137")
        if result.timed_out and result.exit_code != TIMEOUT_EXIT_CODE:
            raise SandboxSecurityViolation(f"Result Invariant: timed_out=True but exit_code={result.exit_code} != 124")


# ============================================================================
# 10. 沙箱开销剖析器 (SandboxOverheadProfiler)
# ============================================================================

@dataclass
class OverheadProfile:
    cold_start_ms: float
    warm_exec_ms: float
    bare_exec_ms: float
    execs_per_session: int

    def per_turn_sandbox_ms(self) -> float:
        """Cost if every single command booted a fresh container."""
        return self.cold_start_ms + self.warm_exec_ms

    def jit_session_ms(self) -> float:
        """Amortized cost with JIT session wake-up and warm reuse."""
        return (self.cold_start_ms / max(1, self.execs_per_session)) + self.warm_exec_ms

    def overhead_vs_bare_ms(self) -> float:
        return self.jit_session_ms() - self.bare_exec_ms

    def verdict(self) -> str:
        delta = self.overhead_vs_bare_ms()
        if delta < 50.0:
            return f"ACCEPTABLE (+{delta:.1f}ms): JIT amortization makes sandboxing viable for real-time coding."
        return f"COSTLY (+{delta:.1f}ms): consider lightweight MicroVM or pool pre-warming."


class SandboxOverheadProfiler:
    """Measures and calculates amortization curves for sandbox deployments."""

    def __init__(
        self,
        cold_start_ms: float = 132.0,
        warm_exec_ms: float = 32.0,
        bare_exec_ms: float = 6.0,
        execs_per_session: int = 15,
    ):
        self.profile = OverheadProfile(
            cold_start_ms=cold_start_ms,
            warm_exec_ms=warm_exec_ms,
            bare_exec_ms=bare_exec_ms,
            execs_per_session=execs_per_session,
        )
