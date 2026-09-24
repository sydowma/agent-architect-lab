"""Unit tests for Sandbox Runtime Lifecycle & Pre-warmed OverlayFS Pool Manager.

Zero-dependency standard unittest suite testing:
1. Cgroups v2 resource red-line contract generation.
2. Standby pool capacity initialization and min_idle watermark maintenance.
3. Sub-50ms warm lease acquisition latency.
4. OverlayFS Copy-on-Write isolation (Gold Image immutability).
5. UpperDir Action Diff inspection (created, modified, deleted files).
6. Fast COW reset (instant return to clean READY state).
7. Human-in-the-Loop suspend and resume state transitions.
8. Lease expiration detection and autonomous reaper reclaiming.
9. Pool capacity exhaustion guarding.
"""

import os
import shutil
import tempfile
import time
import unittest

from pool import (
    ActionDiff,
    CapacityExhaustedError,
    CgroupsV2Quota,
    HermeticMockOverlayDriver,
    InvalidLeaseTokenError,
    SandboxInstance,
    SandboxPoolError,
    SandboxState,
    SandboxStateError,
    StandbySandboxPoolManager,
)


class TestSandboxRuntimePool(unittest.TestCase):
    """Test suite for production-grade sandbox pool lifecycle."""

    def setUp(self) -> None:
        self.root_dir = tempfile.mkdtemp(prefix="test_sandbox_env_")
        self.gold_dir = os.path.join(self.root_dir, "gold_image")
        self.pool_dir = os.path.join(self.root_dir, "pool")
        os.makedirs(self.gold_dir, exist_ok=True)
        os.makedirs(self.pool_dir, exist_ok=True)

        # Seed gold image with base files
        with open(os.path.join(self.gold_dir, "os_version.txt"), "w") as f:
            f.write("Ubuntu 24.04 LTS Headless Desktop")
        with open(os.path.join(self.gold_dir, "config.json"), "w") as f:
            f.write('{"theme": "dark", "version": 1}')

    def tearDown(self) -> None:
        shutil.rmtree(self.root_dir, ignore_errors=True)

    def test_cgroups_v2_quota_contract(self) -> None:
        """Validates that Cgroups v2 commands enforce production security red lines."""
        quota = CgroupsV2Quota(pids_max=512, memory_max_bytes=2147483648, oom_group=True)
        cmds = quota.generate_cgroup_commands("/sys/fs/cgroup/sb_test")
        joined = " ".join(cmds)
        self.assertIn('512" > /sys/fs/cgroup/sb_test/pids.max', joined)
        self.assertIn('2147483648" > /sys/fs/cgroup/sb_test/memory.max', joined)
        self.assertIn('1" > /sys/fs/cgroup/sb_test/memory.oom.group', joined)

    def test_pool_initialization_watermark(self) -> None:
        """Validates that initialize_pool() fills the standby pool to min_idle."""
        mgr = StandbySandboxPoolManager(
            gold_image_dir=self.gold_dir,
            pool_root_dir=self.pool_dir,
            min_idle=3,
            max_capacity=5,
        )
        mgr.initialize_pool()

        status = mgr.status()
        self.assertEqual(status["total"], 3)
        self.assertEqual(status["ready"], 3)
        self.assertEqual(status["leased"], 0)
        mgr.shutdown()

    def test_fast_lease_acquisition_latency(self) -> None:
        """Benchmarks lease acquisition latency to ensure it is < 50ms from warm standby."""
        mgr = StandbySandboxPoolManager(
            gold_image_dir=self.gold_dir,
            pool_root_dir=self.pool_dir,
            min_idle=2,
            max_capacity=5,
        )
        mgr.initialize_pool()

        t0 = time.perf_counter()
        sb, token = mgr.acquire(ttl_seconds=60)
        t1 = time.perf_counter()

        elapsed_ms = (t1 - t0) * 1000
        self.assertLess(elapsed_ms, 50.0, f"Lease latency too slow: {elapsed_ms:.2f}ms (target < 50ms)")
        self.assertEqual(sb.state, SandboxState.LEASED)
        self.assertTrue(token.startswith("tok_"))

        mgr.shutdown()

    def test_overlayfs_cow_isolation_and_gold_purity(self) -> None:
        """Validates Copy-on-Write isolation: mutations in sandbox never corrupt Gold Image."""
        mgr = StandbySandboxPoolManager(
            gold_image_dir=self.gold_dir,
            pool_root_dir=self.pool_dir,
            min_idle=1,
        )
        mgr.initialize_pool()

        sb, token = mgr.acquire()

        # 1. Verify sandbox sees gold image files
        self.assertEqual(sb.read_file("os_version.txt"), "Ubuntu 24.04 LTS Headless Desktop")

        # 2. Modify an existing file (COW mutation)
        sb.write_file("config.json", '{"theme": "dark", "version": 2, "agent_hacked": true}')

        # 3. Create a brand new file
        sb.write_file("agent_output.py", "print('hello from sandbox')")

        # 4. Delete a base file (whiteout)
        sb.delete_file("os_version.txt")

        # 5. Verify sandbox view reflects mutations
        self.assertEqual(sb.read_file("config.json"), '{"theme": "dark", "version": 2, "agent_hacked": true}')
        self.assertEqual(sb.read_file("agent_output.py"), "print('hello from sandbox')")
        with self.assertRaises(FileNotFoundError):
            sb.read_file("os_version.txt")

        # 6. CRUCIAL CHECK: Verify Gold Image remains 100% UNTOUCHED
        with open(os.path.join(self.gold_dir, "os_version.txt")) as f:
            self.assertEqual(f.read(), "Ubuntu 24.04 LTS Headless Desktop")
        with open(os.path.join(self.gold_dir, "config.json")) as f:
            self.assertEqual(f.read(), '{"theme": "dark", "version": 1}')
        self.assertFalse(os.path.exists(os.path.join(self.gold_dir, "agent_output.py")))

        mgr.shutdown()

    def test_action_diff_audit_and_fast_reset(self) -> None:
        """Validates that release() inspects mutations and fast_reset clears dirty state in ms."""
        mgr = StandbySandboxPoolManager(
            gold_image_dir=self.gold_dir,
            pool_root_dir=self.pool_dir,
            min_idle=1,
        )
        mgr.initialize_pool()

        sb, token = mgr.acquire()
        sb.write_file("new_task.txt", "task data")
        sb.write_file("config.json", '{"theme": "light"}')
        sb.delete_file("os_version.txt")

        # Release with diff inspection
        diff = mgr.release(sb.sandbox_id, token, fast_reset=True)

        self.assertIn("new_task.txt", diff.created_files)
        self.assertIn("config.json", diff.modified_files)
        self.assertIn("os_version.txt", diff.deleted_files)

        # Verify sandbox is back to READY and clean
        self.assertEqual(sb.state, SandboxState.READY)
        self.assertEqual(sb.read_file("os_version.txt"), "Ubuntu 24.04 LTS Headless Desktop")
        self.assertEqual(sb.read_file("config.json"), '{"theme": "dark", "version": 1}')
        with self.assertRaises(FileNotFoundError):
            sb.read_file("new_task.txt")

        mgr.shutdown()

    def test_human_in_the_loop_suspend_and_resume(self) -> None:
        """Validates Human-in-the-Loop takeover suspension and resumption."""
        mgr = StandbySandboxPoolManager(
            gold_image_dir=self.gold_dir,
            pool_root_dir=self.pool_dir,
            min_idle=1,
        )
        mgr.initialize_pool()

        sb, token = mgr.acquire()
        self.assertEqual(sb.state, SandboxState.LEASED)

        # Human requests takeover
        mgr.suspend(sb.sandbox_id, token)
        self.assertEqual(sb.state, SandboxState.SUSPENDED)

        # Human intervention completed, resume
        mgr.resume(sb.sandbox_id, token)
        self.assertEqual(sb.state, SandboxState.LEASED)

        mgr.shutdown()

    def test_lease_expiration_and_autonomous_reaper(self) -> None:
        """Validates that expired leases are autonomously reaped and returned to pool."""
        mgr = StandbySandboxPoolManager(
            gold_image_dir=self.gold_dir,
            pool_root_dir=self.pool_dir,
            min_idle=1,
        )
        mgr.initialize_pool()

        sb, token = mgr.acquire(ttl_seconds=10.0)

        # Simulate time fast-forward by 15 seconds
        future_time = time.time() + 15.0
        self.assertTrue(sb.is_expired(now=future_time))

        # Reaper scans and cleans
        reaped = mgr.reap_expired(now=future_time)
        self.assertIn(sb.sandbox_id, reaped)
        self.assertEqual(sb.state, SandboxState.READY)

        mgr.shutdown()

    def test_capacity_exhaustion_guard(self) -> None:
        """Validates CapacityExhaustedError when pool reaches max_capacity limit."""
        mgr = StandbySandboxPoolManager(
            gold_image_dir=self.gold_dir,
            pool_root_dir=self.pool_dir,
            min_idle=1,
            max_capacity=2,
        )
        mgr.initialize_pool()

        sb1, t1 = mgr.acquire()
        sb2, t2 = mgr.acquire()

        with self.assertRaises(CapacityExhaustedError):
            mgr.acquire()

        # Releasing one unlocks capacity
        mgr.release(sb1.sandbox_id, t1, fast_reset=True)
        sb3, t3 = mgr.acquire()
        self.assertIsNotNone(sb3)

        mgr.shutdown()


if __name__ == "__main__":
    unittest.main()
