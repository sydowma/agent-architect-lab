"""Integration test suite for mini-harness v0.2 Industrial Defense Release."""

import unittest
from typing import Any, Dict, List

from v02_industrial_harness import (
    CacheSafeParams,
    ContextCompactor,
    ManagedToolOrchestrator,
    MultiAgentCoordinator,
    PromptControlPlane,
    PromptTierConfig,
    ResilientRecoveryEngine,
    SafeBashGuard,
    SessionMemory,
    UnifiedIndustrialHarness,
)


class TestDay21UnifiedHarnessV02(unittest.TestCase):
    """Deterministic end-to-end integration tests validating all 10 Principles."""

    def test_01_prompt_control_plane_and_kv_cache_stability(self):
        """[P2] Verify 5-tier precedence and prefix cache stability with dynamic reminder."""
        config = PromptTierConfig(
            default_prompt="DEFAULT PROMPT",
            agent_rules="AGENT SPECIFIC CONSTITUTION",
            append_rules="RULE: VERIFY BEFORE COMPLETION",
        )
        plane = PromptControlPlane(config)
        static_sys = plane.resolve_static_system_prompt()
        self.assertIn("AGENT SPECIFIC CONSTITUTION", static_sys)
        self.assertIn("RULE: VERIFY BEFORE COMPLETION", static_sys)

        # Dynamic state is injected as tip reminder, static prompt remains 100% immutable
        reminder = plane.format_dynamic_reminder(git_status="clean", active_file="src/cache.py")
        self.assertEqual(reminder["type"], "system_reminder")
        self.assertIn("src/cache.py", reminder["content"])
        self.assertEqual(static_sys, plane.resolve_static_system_prompt(), "System prompt MUST NOT mutate!")

    def test_02_managed_tool_partitioning_and_safebashguard(self):
        """[P4] Verify concurrency-safe partitioning and SafeBashGuard destructive block."""
        orchestrator = ManagedToolOrchestrator()
        tool_calls = [
            {"id": "c1", "name": "read_file", "args": {"path": "a.py"}},
            {"id": "c2", "name": "grep_search", "args": {"query": "foo"}},
            {"id": "c3", "name": "write_file", "args": {"path": "b.py"}},
            {"id": "c4", "name": "read_file", "args": {"path": "c.py"}},
        ]
        batches = orchestrator.partition_tool_calls(tool_calls)
        # Should be: [c1, c2] parallel, [c3] serial, [c4] parallel
        self.assertEqual(len(batches), 3)
        self.assertEqual(len(batches[0]), 2)  # c1, c2
        self.assertEqual(batches[1][0]["name"], "write_file")
        self.assertEqual(batches[2][0]["name"], "read_file")

        # SafeBashGuard checks
        ok, reason = SafeBashGuard.evaluate_command("rm -rf /")
        self.assertFalse(ok)
        self.assertIn("Destructive recursive", reason)

        ok_compound, reason_compound = SafeBashGuard.evaluate_command("echo 1; echo 2; echo 3; echo 4")
        self.assertFalse(ok_compound)
        self.assertIn("exceeds subcommand limit", reason_compound)

    def test_03_query_loop_synthetic_ledger_balancing(self):
        """[P3] Verify synthetic tool results are materialized under abort, keeping ledger balanced."""
        harness = UnifiedIndustrialHarness()
        harness.pending_tool_calls = [
            {"id": "call_99", "name": "run_bash"},
            {"id": "call_100", "name": "fetch_url"},
        ]
        synthetics = harness.balance_tool_ledger_under_abort()
        self.assertEqual(len(synthetics), 2)
        self.assertEqual(synthetics[0]["tool_use_id"], "call_99")
        self.assertTrue(synthetics[0]["is_error"])
        self.assertEqual(len(harness.pending_tool_calls), 0)
        self.assertEqual(len(harness.verify_invariants()), 0)

    def test_04_autocompact_budget_and_controlled_reboot(self):
        """[P5] Verify 20k/13k budget thresholds and controlled reboot state reconstruction."""
        compactor = ContextCompactor(context_window=128_000)
        # Threshold is (128k - 20k) - 13k = 95k
        self.assertFalse(compactor.should_compact(94_000))
        self.assertTrue(compactor.should_compact(96_000))

        session_mem = SessionMemory(
            current_state="Patched race condition in queue",
            task_specification="Fix thread safety in message queue",
        )
        messages = [{"role": "user", "content": "hello"}]
        active_files = {"src/queue.py": "class SafeQueue: pass"}

        reconstructed, boundary = compactor.execute_controlled_reboot(
            messages=messages,
            session_summary=session_mem,
            active_files=active_files,
            pre_tokens=98_000,
        )
        self.assertEqual(boundary.pre_compact_tokens, 98_000)
        types = [m.get("type") for m in reconstructed]
        self.assertIn("compact_boundary", types)
        self.assertIn("session_memory", types)
        self.assertIn("active_file", types)

    def test_05_error_recovery_and_death_spiral_bypass(self):
        """[P6 & P7] Verify PTL skips stop hooks upon unrecoverable error to break death spiral."""
        engine = ResilientRecoveryEngine()
        # First PTL -> Reactive compact
        act1, skip_hooks1 = engine.handle_error("Error: prompt_too_long", has_attempted_compact=False)
        self.assertEqual(act1, "REACTIVE_COMPACT")
        self.assertFalse(skip_hooks1)

        # Second PTL after compact already attempted -> Surface and MUST skip stop hooks!
        act2, skip_hooks2 = engine.handle_error("Error: prompt_too_long", has_attempted_compact=True)
        self.assertEqual(act2, "SURFACE_ERROR")
        self.assertTrue(skip_hooks2, "Unrecoverable PTL MUST skip stop hooks to break death spiral!")

    def test_06_multi_agent_law_of_synthesis_and_qa_role_separation(self):
        """[P8 & P9] Verify coordinator synthesis requirement and strict QA role separation."""
        cache_params = CacheSafeParams(
            system_prompt="SYS",
            user_context="USER",
            system_context="SYS_CTX",
            tool_use_context="TOOLS",
            fork_context_messages=[{"role": "user", "content": "task"}],
        )
        coord = MultiAgentCoordinator(cache_params)

        # 1. Synthesis violation check
        with self.assertRaises(ValueError) as ctx:
            coord.synthesize("Please fix based on your findings.")
        self.assertIn("Synthesis Violation", str(ctx.exception))

        # 2. Valid synthesis
        plan = coord.synthesize("Deadlock occurs when lock acquired twice.")
        self.assertEqual(plan["status"], "SYNTHESIZED")

        # 3. Role separation check
        coord.last_implementation_worker = "worker_alice"
        with self.assertRaises(AssertionError) as ctx_role:
            coord.verify_role_separation("worker_alice")  # Alice cannot self-verify!
        self.assertIn("Role Separation Violation", str(ctx_role.exception))

        # Bob can verify!
        coord.verify_role_separation("worker_bob")


if __name__ == "__main__":
    unittest.main(verbosity=2)
