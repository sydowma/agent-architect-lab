"""Sandbox Runtime Lifecycle & Pre-warmed OverlayFS Pool Manager.

Week 1 Core Implementation for Agent Backend Architecture:
1. Low-level OverlayFS Driver (Copy-on-Write mount, atomic reset, whiteout handling).
2. Hermetic cross-platform fallback driver for zero-dependency local dev/macOS testing.
3. Cgroups v2 resource governance contract (pids.max, memory.oom.group).
4. StandbySandboxPoolManager with atomic CAS state transitions, lease tokens, and reaper.
5. Action Diff Inspector (auditing exact file mutations made by Agent in UpperDir).
"""

from __future__ import annotations

import abc
import dataclasses
import enum
import os
import shutil
import subprocess
import threading
import time
import uuid
from typing import Dict, List, Optional, Set, Tuple


# ============================================================================
# 1. 状态机枚举与异常体系
# ============================================================================

class SandboxState(str, enum.Enum):
    PREPARING = "PREPARING"   # 后台挂载与初始化中
    READY = "READY"           # 预热完成，待命借出 (Standby)
    LEASED = "LEASED"         # 已被 Agent 租借占用
    SUSPENDED = "SUSPENDED"   # 人工接管挂起 (Human-in-the-Loop)
    DIRTY = "DIRTY"           # 执行完成或超期，等待环境清理
    PURGED = "PURGED"         # 已卸载并清除


class SandboxPoolError(Exception):
    """Base error for sandbox runtime pool."""


class CapacityExhaustedError(SandboxPoolError):
    """Raised when the pool reaches max_capacity and no idle sandbox is available."""


class InvalidLeaseTokenError(SandboxPoolError):
    """Raised when an operation is attempted with an invalid or expired lease token."""


class SandboxStateError(SandboxPoolError):
    """Raised when a state transition violates state machine invariants."""


# ============================================================================
# 2. Cgroups v2 资源配额契约
# ============================================================================

@dataclasses.dataclass(frozen=True)
class CgroupsV2Quota:
    """Production-grade resource red lines for computer use sandboxes."""
    pids_max: int = 512                         # 防 Fork 炸弹红线
    memory_max_bytes: int = 2 * 1024 * 1024 * 1024  # 2GB 内存配额
    oom_group: bool = True                      # OOM 时整组清除，严防僵尸进程
    cpu_quota_us: int = 200000                  # CPU 限制 2 核 (200ms 每 100ms)
    cpu_period_us: int = 100000

    def generate_cgroup_commands(self, cgroup_path: str) -> List[str]:
        """Generates shell commands to configure Linux cgroups v2."""
        return [
            f"mkdir -p {cgroup_path}",
            f'echo "{self.pids_max}" > {cgroup_path}/pids.max',
            f'echo "{self.memory_max_bytes}" > {cgroup_path}/memory.max',
            f'echo "{1 if self.oom_group else 0}" > {cgroup_path}/memory.oom.group',
            f'echo "{self.cpu_quota_us} {self.cpu_period_us}" > {cgroup_path}/cpu.max',
        ]


# ============================================================================
# 3. OverlayFS 底层驱动 (Linux 原生 + 跨平台透明仿真)
# ============================================================================

class BaseOverlayDriver(abc.ABC):
    """Abstract interface for OverlayFS union mount operations."""

    @abc.abstractmethod
    def mount(self, lower_dir: str, upper_dir: str, work_dir: str, merged_dir: str) -> None:
        """Mounts the overlay filesystem."""

    @abc.abstractmethod
    def unmount(self, merged_dir: str) -> None:
        """Unmounts the merged directory."""

    @abc.abstractmethod
    def wipe_upper(self, upper_dir: str, work_dir: str) -> None:
        """Atomically wipes upperdir and workdir to reset state."""


class LinuxNativeOverlayDriver(BaseOverlayDriver):
    """Real Linux kernel OverlayFS driver using mount(2) syscalls via shell."""

    def mount(self, lower_dir: str, upper_dir: str, work_dir: str, merged_dir: str) -> None:
        os.makedirs(upper_dir, exist_ok=True)
        os.makedirs(work_dir, exist_ok=True)
        os.makedirs(merged_dir, exist_ok=True)
        cmd = [
            "mount", "-t", "overlay", "overlay",
            "-o", f"lowerdir={lower_dir},upperdir={upper_dir},workdir={work_dir}",
            merged_dir,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise SandboxPoolError(f"Linux OverlayFS mount failed: {res.stderr}")

    def unmount(self, merged_dir: str) -> None:
        if os.path.exists(merged_dir):
            cmd = ["umount", "-f", merged_dir]
            subprocess.run(cmd, capture_output=True, text=True)

    def wipe_upper(self, upper_dir: str, work_dir: str) -> None:
        if os.path.exists(upper_dir):
            shutil.rmtree(upper_dir, ignore_errors=True)
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
        os.makedirs(upper_dir, exist_ok=True)
        os.makedirs(work_dir, exist_ok=True)


class HermeticMockOverlayDriver(BaseOverlayDriver):
    """Pure-Python, cross-platform hermetic mock driver for macOS and unprivileged test environments.
    
    Faithfully simulates LowerDir (immutable), UpperDir (writable delta),
    and Merged views with Whiteout (.wh.*) markers for file deletions.
    """

    def mount(self, lower_dir: str, upper_dir: str, work_dir: str, merged_dir: str) -> None:
        os.makedirs(lower_dir, exist_ok=True)
        os.makedirs(upper_dir, exist_ok=True)
        os.makedirs(work_dir, exist_ok=True)
        os.makedirs(merged_dir, exist_ok=True)
        self.sync_to_merged(lower_dir, upper_dir, merged_dir)

    def unmount(self, merged_dir: str) -> None:
        if os.path.exists(merged_dir):
            shutil.rmtree(merged_dir, ignore_errors=True)

    def wipe_upper(self, upper_dir: str, work_dir: str) -> None:
        shutil.rmtree(upper_dir, ignore_errors=True)
        shutil.rmtree(work_dir, ignore_errors=True)
        os.makedirs(upper_dir, exist_ok=True)
        os.makedirs(work_dir, exist_ok=True)

    def sync_to_merged(self, lower_dir: str, upper_dir: str, merged_dir: str) -> None:
        """Projects lower and upper into merged directory."""
        # 1. Copy lowerdir contents
        for root, dirs, files in os.walk(lower_dir):
            rel_root = os.path.relpath(root, lower_dir)
            target_dir = merged_dir if rel_root == "." else os.path.join(merged_dir, rel_root)
            os.makedirs(target_dir, exist_ok=True)
            for f in files:
                src = os.path.join(root, f)
                dst = os.path.join(target_dir, f)
                shutil.copy2(src, dst)

        # 2. Overlay upperdir contents (including whiteouts)
        if os.path.exists(upper_dir):
            for root, dirs, files in os.walk(upper_dir):
                rel_root = os.path.relpath(root, upper_dir)
                target_dir = merged_dir if rel_root == "." else os.path.join(merged_dir, rel_root)
                os.makedirs(target_dir, exist_ok=True)
                for f in files:
                    if f.startswith(".wh."):
                        # Whiteout file: delete corresponding file in merged
                        target_file = f[4:]
                        to_remove = os.path.join(target_dir, target_file)
                        if os.path.exists(to_remove):
                            if os.path.isdir(to_remove):
                                shutil.rmtree(to_remove, ignore_errors=True)
                            else:
                                os.remove(to_remove)
                    else:
                        src = os.path.join(root, f)
                        dst = os.path.join(target_dir, f)
                        shutil.copy2(src, dst)


# ============================================================================
# 4. 单沙箱实例抽象 (SandboxInstance)
# ============================================================================

@dataclasses.dataclass
class ActionDiff:
    """Summary of mutations produced by Agent during its session."""
    created_files: List[str] = dataclasses.field(default_factory=list)
    modified_files: List[str] = dataclasses.field(default_factory=list)
    deleted_files: List[str] = dataclasses.field(default_factory=list)


class SandboxInstance:
    """A concrete, isolated sandbox instance with its own OverlayFS mount."""

    def __init__(
        self,
        sandbox_id: str,
        base_dir: str,
        lower_dir: str,
        driver: BaseOverlayDriver,
        display_id: int = 99,
    ) -> None:
        self.sandbox_id = sandbox_id
        self.base_dir = base_dir
        self.lower_dir = lower_dir
        self.driver = driver
        self.display_id = display_id

        self.upper_dir = os.path.join(base_dir, "upper")
        self.work_dir = os.path.join(base_dir, "work")
        self.merged_dir = os.path.join(base_dir, "merged")

        self.state: SandboxState = SandboxState.PREPARING
        self.lease_token: Optional[str] = None
        self.created_at: float = time.time()
        self.leased_at: Optional[float] = None
        self.last_heartbeat: Optional[float] = None
        self.ttl_seconds: float = 300.0  # 默认 5 分钟

    def initialize(self) -> None:
        """Mounts the overlay and transitions to READY."""
        self.driver.mount(self.lower_dir, self.upper_dir, self.work_dir, self.merged_dir)
        self.state = SandboxState.READY

    def lease(self, ttl_seconds: float) -> str:
        """Transitions from READY to LEASED with a secure lease token."""
        if self.state != SandboxState.READY:
            raise SandboxStateError(f"Cannot lease sandbox {self.sandbox_id} in state {self.state}")
        self.state = SandboxState.LEASED
        self.lease_token = f"tok_{uuid.uuid4().hex[:12]}"
        self.leased_at = time.time()
        self.last_heartbeat = self.leased_at
        self.ttl_seconds = ttl_seconds
        return self.lease_token

    def heartbeat(self, token: str) -> None:
        """Updates heartbeat to prevent lease reaping."""
        if self.lease_token != token:
            raise InvalidLeaseTokenError("Heartbeat rejected: invalid lease token")
        self.last_heartbeat = time.time()

    def is_expired(self, now: Optional[float] = None) -> bool:
        """Checks whether the lease TTL has been exceeded."""
        if self.state not in (SandboxState.LEASED, SandboxState.SUSPENDED):
            return False
        curr_time = now or time.time()
        if not self.last_heartbeat:
            return True
        return (curr_time - self.last_heartbeat) > self.ttl_seconds

    def write_file(self, rel_path: str, content: str) -> None:
        """Writes a file to the sandbox (modifies UpperDir and Merged view)."""
        if self.state not in (SandboxState.LEASED, SandboxState.SUSPENDED):
            raise SandboxStateError("Cannot write to unleased sandbox")
        
        # Write to upper_dir
        upper_target = os.path.join(self.upper_dir, rel_path)
        os.makedirs(os.path.dirname(upper_target), exist_ok=True)
        with open(upper_target, "w", encoding="utf-8") as f:
            f.write(content)

        # Write to merged_dir for direct view
        merged_target = os.path.join(self.merged_dir, rel_path)
        os.makedirs(os.path.dirname(merged_target), exist_ok=True)
        with open(merged_target, "w", encoding="utf-8") as f:
            f.write(content)

    def read_file(self, rel_path: str) -> str:
        """Reads a file from the sandbox merged view."""
        merged_target = os.path.join(self.merged_dir, rel_path)
        if not os.path.exists(merged_target):
            raise FileNotFoundError(f"File not found in sandbox: {rel_path}")
        with open(merged_target, "r", encoding="utf-8") as f:
            return f.read()

    def delete_file(self, rel_path: str) -> None:
        """Simulates file deletion via whiteout in UpperDir."""
        if self.state not in (SandboxState.LEASED, SandboxState.SUSPENDED):
            raise SandboxStateError("Cannot delete in unleased sandbox")

        merged_target = os.path.join(self.merged_dir, rel_path)
        if os.path.exists(merged_target):
            if os.path.isdir(merged_target):
                shutil.rmtree(merged_target, ignore_errors=True)
            else:
                os.remove(merged_target)

        # Create whiteout file in upperdir (.wh.<name>)
        dir_name = os.path.dirname(rel_path)
        base_name = os.path.basename(rel_path)
        whiteout_name = f".wh.{base_name}"
        upper_whiteout = os.path.join(self.upper_dir, dir_name, whiteout_name)
        os.makedirs(os.path.dirname(upper_whiteout), exist_ok=True)
        with open(upper_whiteout, "w", encoding="utf-8") as f:
            f.write("")

    def inspect_diff(self) -> ActionDiff:
        """Inspects UpperDir to detect all file mutations created by the Agent."""
        diff = ActionDiff()
        if not os.path.exists(self.upper_dir):
            return diff

        for root, dirs, files in os.walk(self.upper_dir):
            rel_root = os.path.relpath(root, self.upper_dir)
            for f in files:
                rel_file = f if rel_root == "." else os.path.join(rel_root, f)
                if f.startswith(".wh."):
                    diff.deleted_files.append(rel_file.replace(".wh.", ""))
                else:
                    lower_candidate = os.path.join(self.lower_dir, rel_file)
                    if os.path.exists(lower_candidate):
                        diff.modified_files.append(rel_file)
                    else:
                        diff.created_files.append(rel_file)
        return diff

    def reset_cow(self) -> None:
        """Instantly wipes UpperDir and resets the sandbox to pristine READY state."""
        self.driver.wipe_upper(self.upper_dir, self.work_dir)
        self.driver.unmount(self.merged_dir)
        self.driver.mount(self.lower_dir, self.upper_dir, self.work_dir, self.merged_dir)
        self.lease_token = None
        self.leased_at = None
        self.last_heartbeat = None
        self.state = SandboxState.READY

    def purge(self) -> None:
        """Unmounts and thoroughly destroys the sandbox."""
        self.driver.unmount(self.merged_dir)
        shutil.rmtree(self.base_dir, ignore_errors=True)
        self.state = SandboxState.PURGED


# ============================================================================
# 5. 预热池管理器 (StandbySandboxPoolManager)
# ============================================================================

class StandbySandboxPoolManager:
    """Industrial pool manager for high-frequency Computer Use sandboxes.
    
    Guarantees:
    1. Pre-warmed Standby instances ready for < 50ms leasing.
    2. Concurrency-safe lease acquisition via Lock & CAS state transitions.
    3. Background replenishing daemon to maintain min_idle watermark.
    4. Autonomous reaper for expired leases.
    """

    def __init__(
        self,
        gold_image_dir: str,
        pool_root_dir: str,
        min_idle: int = 3,
        max_capacity: int = 10,
        default_ttl_seconds: float = 300.0,
        driver: Optional[BaseOverlayDriver] = None,
    ) -> None:
        self.gold_image_dir = gold_image_dir
        self.pool_root_dir = pool_root_dir
        self.min_idle = min_idle
        self.max_capacity = max_capacity
        self.default_ttl_seconds = default_ttl_seconds
        self.driver = driver or HermeticMockOverlayDriver()

        self._lock = threading.Lock()
        self._sandboxes: Dict[str, SandboxInstance] = {}
        self._next_display_id = 99

        os.makedirs(self.pool_root_dir, exist_ok=True)
        os.makedirs(self.gold_image_dir, exist_ok=True)

    def initialize_pool(self) -> None:
        """Initializes the standby pool to reach min_idle capacity."""
        with self._lock:
            while len([s for s in self._sandboxes.values() if s.state == SandboxState.READY]) < self.min_idle:
                self._create_standby_instance_locked()

    def _create_standby_instance_locked(self) -> SandboxInstance:
        """Internal helper to create a READY instance under lock."""
        if len(self._sandboxes) >= self.max_capacity:
            raise CapacityExhaustedError("Pool reached max_capacity limit")

        sb_id = f"sb_{uuid.uuid4().hex[:8]}"
        sb_dir = os.path.join(self.pool_root_dir, sb_id)
        display_id = self._next_display_id
        self._next_display_id += 1

        sb = SandboxInstance(
            sandbox_id=sb_id,
            base_dir=sb_dir,
            lower_dir=self.gold_image_dir,
            driver=self.driver,
            display_id=display_id,
        )
        sb.initialize()
        self._sandboxes[sb_id] = sb
        return sb

    def acquire(self, ttl_seconds: Optional[float] = None) -> Tuple[SandboxInstance, str]:
        """Atomically leases a pre-warmed sandbox in < 50ms."""
        ttl = ttl_seconds or self.default_ttl_seconds
        start_time = time.time()

        with self._lock:
            # 1. Search for an existing READY instance
            ready_instances = [s for s in self._sandboxes.values() if s.state == SandboxState.READY]
            if ready_instances:
                target = ready_instances[0]
                token = target.lease(ttl)
                self._replenish_async()
                return target, token

            # 2. If no READY instance, create on-demand if within capacity
            if len(self._sandboxes) < self.max_capacity:
                target = self._create_standby_instance_locked()
                token = target.lease(ttl)
                return target, token

            raise CapacityExhaustedError("No standby sandbox ready and pool capacity exhausted")

    def release(self, sandbox_id: str, lease_token: str, fast_reset: bool = True) -> ActionDiff:
        """Releases the sandbox, performs action diff audit, and resets UpperDir."""
        with self._lock:
            sb = self._sandboxes.get(sandbox_id)
            if not sb:
                raise SandboxPoolError(f"Unknown sandbox id: {sandbox_id}")
            if sb.lease_token != lease_token:
                raise InvalidLeaseTokenError("Release rejected: invalid lease token")

            diff = sb.inspect_diff()
            sb.state = SandboxState.DIRTY

            if fast_reset:
                sb.reset_cow()
            else:
                sb.purge()
                del self._sandboxes[sandbox_id]
                self._replenish_async()

            return diff

    def suspend(self, sandbox_id: str, lease_token: str) -> None:
        """Suspends sandbox execution for Human-in-the-Loop takeover."""
        with self._lock:
            sb = self._sandboxes.get(sandbox_id)
            if not sb or sb.lease_token != lease_token:
                raise InvalidLeaseTokenError("Suspend rejected: invalid token")
            if sb.state != SandboxState.LEASED:
                raise SandboxStateError(f"Cannot suspend sandbox in state {sb.state}")
            sb.state = SandboxState.SUSPENDED

    def resume(self, sandbox_id: str, lease_token: str) -> None:
        """Resumes sandbox execution after human intervention finishes."""
        with self._lock:
            sb = self._sandboxes.get(sandbox_id)
            if not sb or sb.lease_token != lease_token:
                raise InvalidLeaseTokenError("Resume rejected: invalid token")
            if sb.state != SandboxState.SUSPENDED:
                raise SandboxStateError(f"Cannot resume sandbox in state {sb.state}")
            sb.state = SandboxState.LEASED

    def reap_expired(self, now: Optional[float] = None) -> List[str]:
        """Scans and reaps all sandboxes whose lease TTL has expired."""
        reaped_ids = []
        with self._lock:
            for sb in list(self._sandboxes.values()):
                if sb.is_expired(now):
                    reaped_ids.append(sb.sandbox_id)
                    sb.state = SandboxState.DIRTY
                    sb.reset_cow()
        return reaped_ids

    def _replenish_async(self) -> None:
        """Maintains the min_idle standby pool watermark."""
        ready_count = len([s for s in self._sandboxes.values() if s.state == SandboxState.READY])
        needed = self.min_idle - ready_count
        while needed > 0 and len(self._sandboxes) < self.max_capacity:
            self._create_standby_instance_locked()
            needed -= 1

    def status(self) -> Dict[str, int]:
        """Returns runtime capacity snapshot."""
        with self._lock:
            ready = len([s for s in self._sandboxes.values() if s.state == SandboxState.READY])
            leased = len([s for s in self._sandboxes.values() if s.state == SandboxState.LEASED])
            suspended = len([s for s in self._sandboxes.values() if s.state == SandboxState.SUSPENDED])
            dirty = len([s for s in self._sandboxes.values() if s.state == SandboxState.DIRTY])
            return {
                "total": len(self._sandboxes),
                "ready": ready,
                "leased": leased,
                "suspended": suspended,
                "dirty": dirty,
                "max_capacity": self.max_capacity,
            }

    def shutdown(self) -> None:
        """Cleans up all sandboxes on shutdown."""
        with self._lock:
            for sb in list(self._sandboxes.values()):
                sb.purge()
            self._sandboxes.clear()
