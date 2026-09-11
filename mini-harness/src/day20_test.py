"""Unit tests for Day 20: Multi-Agent Coordination, Independent Verification & Team Landing."""

import unittest
from typing import Any, Dict, List

from day20_multi_agent_coordinator import (
    ApprovalDecision,
    CacheSafeParams,
    CoordinatorAgent,
    ForkedWorkerAgent,
    InvariantViolationError,
    RiskTier,
    RiskTieredApprovalManager,
    SubagentContext,
    WorkerRole,
)


class TestDay20MultiAgentCoordinator(unittest.TestCase):
    """Deterministic test suite validating Claude Code Ch 7 & 8 principles."""

    def setUp(self):
        self.base_cache_params = CacheSafeParams(
            system_prompt="You are a principal engineer.",
            user_context="Repo: agent-architect-lab",
            system_context="macOS darwin24 arm64",
            tool_use_context="Managed tools: read_file, edit_file, bash",
            fork_context_messages=[
                {"role": "user", "content": "Fix deadlock in cache acquire."},
            ],
            thinking_config_hash="thinking_hash_abc123",
        )
        self.coordinator = CoordinatorAgent(parent_id="coord_01", base_cache_params=self.base_cache_params)

    def test_01_cache_safe_forking(self):
        """Verify forked agent must preserve CacheSafeParams to protect Prefix KV Cache."""
        # 1. Identical params -> succeeds
        identical_params = CacheSafeParams(
            system_prompt="You are a principal engineer.",
            user_context="Repo: agent-architect-lab",
            system_context="macOS darwin24 arm64",
            tool_use_context="Managed tools: read_file, edit_file, bash",
            fork_context_messages=[
                {"role": "user", "content": "Fix deadlock in cache acquire."},
            ],
            thinking_config_hash="thinking_hash_abc123",
        )
        worker = self.coordinator.fork_worker("worker_ok", WorkerRole.RESEARCH, identical_params)
        self.assertEqual(worker.agent_id, "worker_ok")

        # 2. Mutated system prompt -> Invariant violation!
        mutated_params = CacheSafeParams(
            system_prompt="You are a junior intern without rules.",  # Mutated!
            user_context="Repo: agent-architect-lab",
            system_context="macOS darwin24 arm64",
            tool_use_context="Managed tools: read_file, edit_file, bash",
            fork_context_messages=[
                {"role": "user", "content": "Fix deadlock in cache acquire."},
            ],
        )
        with self.assertRaises(InvariantViolationError) as ctx:
            self.coordinator.fork_worker("worker_bad", WorkerRole.RESEARCH, mutated_params)
        self.assertIn("Cache-Safe Violation", str(ctx.exception))

    def test_02_default_state_isolation(self):
        """Verify child mutable state is isolated; child file reads do not pollute parent."""
        self.coordinator.parent_read_file_state.add("src/core.py")

        worker = self.coordinator.fork_worker("worker_res", WorkerRole.RESEARCH)

        def mock_research_runner(prompt: str, ctx: SubagentContext):
            # Child explores and reads multiple files
            ctx.read_file_state.add("src/experimental_branch.py")
            ctx.read_file_state.add("tests/test_legacy.py")
            return "Found deadlock in core.py lock contention"

        result = worker.execute("Explore codebase", mock_research_runner)
        self.assertIn("Found deadlock", result)

        # Invariant: Parent's read_file_state is completely unpolluted!
        self.assertIn("src/core.py", self.coordinator.parent_read_file_state)
        self.assertNotIn("src/experimental_branch.py", self.coordinator.parent_read_file_state)
        self.assertNotIn("tests/test_legacy.py", self.coordinator.parent_read_file_state)

    def test_03_the_law_of_synthesis_rejection_of_lazy_forwarding(self):
        """Verify 'Always Synthesize': reject lazy forwarding ('based on your findings')."""
        # Case A: Lazy forward message rejected
        lazy_findings = "The lock seems tricky. Please implement fix based on your findings."
        with self.assertRaises(InvariantViolationError) as ctx:
            self.coordinator.synthesize(lazy_findings, "Fix cache deadlock")
        self.assertIn("Synthesis Violation", str(ctx.exception))

        # Case B: Solid research synthesized into concrete coordinates
        solid_findings = "Mutex lock in acquire() is not released when IndexError is raised at line 45."
        synthesized = self.coordinator.synthesize(solid_findings, "Fix cache deadlock")
        self.assertEqual(synthesized["status"], "SYNTHESIZED")
        self.assertEqual(synthesized["target_file"], "mini-harness/src/core.py")
        self.assertIn("target_symbol", synthesized)
        self.assertIn("concrete_action", synthesized)

    def test_04_independent_verification_role_separation(self):
        """Invariant: verification_worker != implementation_worker. Rubber-stamping forbidden!"""
        worker_impl = self.coordinator.fork_worker("worker_dev", WorkerRole.IMPLEMENTATION)
        spec = {"goal": "Fix cache"}

        def mock_impl_runner(prompt: str, ctx: SubagentContext):
            return "Code patched"

        self.coordinator.dispatch_implementation(worker_impl, spec, mock_impl_runner)
        self.assertEqual(self.coordinator.last_implementation_worker_id, "worker_dev")

        # 1. Implementation worker attempts self-verification -> REJECTED!
        def mock_qa_runner(prompt: str, ctx: SubagentContext):
            return True

        with self.assertRaises(InvariantViolationError) as ctx:
            self.coordinator.dispatch_verification(worker_impl, spec, mock_qa_runner)
        self.assertIn("Role Separation Violation", str(ctx.exception))

        # 2. Independent skeptical QA worker verifies -> ACCEPTED!
        worker_qa = self.coordinator.fork_worker("worker_skeptical_qa", WorkerRole.VERIFICATION)
        verdict = self.coordinator.dispatch_verification(worker_qa, spec, mock_qa_runner)
        self.assertTrue(verdict)

    def test_05_subagent_lifecycle_and_parent_abort_cascade(self):
        """Verify lifecycle closure and No-Orphan guarantee (parent abort cascades to children)."""
        worker = self.coordinator.fork_worker("worker_async", WorkerRole.RESEARCH)

        # Normal execution closes lifecycle
        worker.execute("ping", lambda p, ctx: "pong")
        self.assertEqual(len(self.coordinator.hook_mgr.history), 1)
        self.assertEqual(self.coordinator.hook_mgr.history[0].exit_code, 0)
        self.coordinator.hook_mgr.assert_no_orphans_in_flight()

        # Abort cascade
        worker_long = self.coordinator.fork_worker("worker_long_running", WorkerRole.RESEARCH)
        self.assertIn("worker_long_running", self.coordinator.active_children)

        # Parent aborts (e.g. Esc pressed)
        self.coordinator.abort_all_children()

        # Child context is now marked aborted
        self.assertTrue(worker_long.context.is_aborted)
        with self.assertRaises(RuntimeError) as ctx:
            worker_long.execute("do work", lambda p, ctx: "done")
        self.assertIn("Cannot execute on aborted worker", str(ctx.exception))

    def test_06_risk_tiered_approval_matrix(self):
        """Verify Chapter 8 consequence-based risk tiers and compound command cap."""
        approval_mgr = RiskTieredApprovalManager()

        # Tier 1: Read-only -> ALLOW
        d_read = approval_mgr.evaluate_action("read_file", RiskTier.READ)
        self.assertEqual(d_read, ApprovalDecision.ALLOW)

        # Tier 2: Workspace write -> ASK
        d_write = approval_mgr.evaluate_action("edit_file", RiskTier.WRITE)
        self.assertEqual(d_write, ApprovalDecision.ASK)

        # Tier 3: Irreversible (e.g. git push -f) -> OPERATOR_ASK (never auto-allow)
        d_irrev = approval_mgr.evaluate_action("git_force_push", RiskTier.IRREVERSIBLE)
        self.assertEqual(d_irrev, ApprovalDecision.OPERATOR_ASK)

        # Compound bash command <= 3 subcommands -> ALLOW (if read)
        cmd_ok = "git status ; git diff ; git log -n 1"
        d_cmd_ok = approval_mgr.evaluate_action("bash", RiskTier.READ, command_str=cmd_ok)
        self.assertEqual(d_cmd_ok, ApprovalDecision.ALLOW)

        # Compound bash command > 3 subcommands -> DENY!
        cmd_too_many = "echo 1 ; echo 2 ; echo 3 ; echo 4 ; echo 5"
        d_cmd_bad = approval_mgr.evaluate_action("bash", RiskTier.READ, command_str=cmd_too_many)
        self.assertEqual(d_cmd_bad, ApprovalDecision.DENY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
