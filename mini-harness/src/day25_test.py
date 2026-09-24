"""Day 25 Unit & Synthesis Test Suite (Standard Library unittest).

Verifies:
1. PrefixCacheDisciplineValidator (monotonic hit, mid-stream mutation, timestamp rejection, compaction epoch reset).
2. SessionPortabilityEngine (JSONL serialization, DAG branching, Pi <-> Codex <-> Claude memory conversion).
3. SyntheticLedgerInvariants (dual-entry closure, Ctrl+C / abort synthetic synthesis, unbalanced error assertion).
4. ArchitectureDecisionAdvisor (workload profile evaluation, archetype recommendations, defense knobs).
"""

import os
import sys
import unittest

# Ensure day25 modules can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from day25_architecture_synthesis import (
    ArchitecturalArchetype,
    ArchitectureDecisionAdvisor,
    CacheInvalidationReason,
    ClaudeWorkingMemory,
    CodexRolloutSnapshot,
    PiEvent,
    PiSessionStream,
    PrefixCacheDisciplineValidator,
    PromptAssemblySnapshot,
    ProtocolLedgerUnbalancedError,
    SandboxIsolationTier,
    SessionPortabilityEngine,
    SessionPortabilityError,
    SessionStorageType,
    SyntheticLedger,
    WorkloadProfile,
)


class TestDay25ArchitectureSynthesis(unittest.TestCase):

    # ========================================================================
    # 1. 提示缓存分界纪律测试
    # ========================================================================

    def test_01_prefix_cache_discipline_monotonic_hit(self):
        """Prompt append-only growth preserves KV cache hit with optimal TTFT."""
        validator = PrefixCacheDisciplineValidator(base_ttft_ms=25.0, cold_penalty_factor=8.0)
        static_prefix = "You are an expert AI software architect. Obey safety rules."
        tools_declaration = '{"tools": ["read_file", "write_file", "execute_bash"]}'

        # Turn 1
        s1 = PromptAssemblySnapshot(
            turn_index=1,
            static_prefix=static_prefix,
            tools_declaration=tools_declaration,
            history_stream=["User: Check repository status."]
        )
        hit, reason, ttft = validator.register_turn(s1)
        self.assertTrue(hit)
        self.assertEqual(reason, CacheInvalidationReason.NONE)
        self.assertEqual(ttft, 25.0)

        # Turn 2: Append-only growth
        s2 = PromptAssemblySnapshot(
            turn_index=2,
            static_prefix=static_prefix,
            tools_declaration=tools_declaration,
            history_stream=[
                "User: Check repository status.",
                "Assistant: Running git status.",
                "User: Now run tests."
            ]
        )
        hit, reason, ttft = validator.register_turn(s2)
        self.assertTrue(hit)
        self.assertEqual(reason, CacheInvalidationReason.NONE)
        self.assertEqual(ttft, 25.0)
        self.assertEqual(validator.total_cache_hits, 2)
        self.assertEqual(validator.total_cache_misses, 0)

    def test_02_prefix_cache_discipline_system_mutation_detected(self):
        """System prompt alteration mid-conversation invalidates cache prefix."""
        validator = PrefixCacheDisciplineValidator(base_ttft_ms=25.0, cold_penalty_factor=8.0)
        tools_decl = '{"tools": ["bash"]}'

        s1 = PromptAssemblySnapshot(1, "Base System v1", tools_decl, ["User: Hello"])
        hit1, _, _ = validator.register_turn(s1)
        self.assertTrue(hit1)

        # Turn 2: Mutated system prefix
        s2 = PromptAssemblySnapshot(2, "Base System v2 with extra rules", tools_decl, ["User: Hello", "Assistant: Hi"])
        hit2, reason2, ttft2 = validator.register_turn(s2)
        self.assertFalse(hit2)
        self.assertEqual(reason2, CacheInvalidationReason.SYSTEM_PREFIX_MUTATED)
        self.assertEqual(ttft2, 200.0)  # 25.0 * 8.0 penalty
        self.assertEqual(validator.total_cache_misses, 1)

    def test_03_prefix_cache_discipline_retroactive_history_edit_detected(self):
        """Rewriting historical messages in-place breaks monotonic prefix requirement."""
        validator = PrefixCacheDisciplineValidator()
        tools_decl = '{"tools": []}'

        s1 = PromptAssemblySnapshot(1, "Static System", tools_decl, ["User: Step 1 original"])
        validator.register_turn(s1)

        # Turn 2: History was retroactively rewritten in place
        s2 = PromptAssemblySnapshot(2, "Static System", tools_decl, ["User: Step 1 REWRITTEN", "Assistant: OK"])
        hit2, reason2, _ = validator.register_turn(s2)
        self.assertFalse(hit2)
        self.assertEqual(reason2, CacheInvalidationReason.RETROACTIVE_HISTORY_EDIT)

    def test_04_prefix_cache_discipline_timestamp_in_prefix_rejected(self):
        """Volatile runtime clock injected into static prefix triggers defensive invalidation."""
        validator = PrefixCacheDisciplineValidator()
        tools_decl = '{"tools": []}'

        s1 = PromptAssemblySnapshot(1, "System Prompt with clock: 2026-09-16T23:00:00", tools_decl, ["User: Hi"])
        hit, reason, ttft = validator.register_turn(s1)
        self.assertFalse(hit)
        self.assertEqual(reason, CacheInvalidationReason.TIMESTAMP_INJECTED_IN_PREFIX)
        self.assertEqual(ttft, 200.0)

    def test_05_prefix_cache_compaction_epoch_reset(self):
        """Compaction establishes a new clean cache baseline anchor."""
        validator = PrefixCacheDisciplineValidator()
        tools_decl = '{"tools": ["bash"]}'

        s1 = PromptAssemblySnapshot(1, "Old System", tools_decl, ["User: Long work 1"])
        validator.register_turn(s1)

        # Perform compaction
        validator.trigger_compaction_reset("Completed tasks 1-5; ready for task 6.", tools_decl)
        self.assertEqual(validator.current_epoch, 1)

        # Next turn matches new epoch baseline
        s_new = PromptAssemblySnapshot(
            turn_index=2,
            static_prefix="[Compaction Epoch 1]\nSummary: Completed tasks 1-5; ready for task 6.",
            tools_declaration=tools_decl,
            history_stream=["User: Proceed with task 6."]
        )
        hit, reason, ttft = validator.register_turn(s_new)
        self.assertTrue(hit)
        self.assertEqual(reason, CacheInvalidationReason.NONE)

    # ========================================================================
    # 2. 会话可移植性测试 (Session Portability & Branching)
    # ========================================================================

    def test_06_pi_session_stream_jsonl_and_branching(self):
        """Pi-style append-only JSONL supports serialization and zero-side-effect DAG branching."""
        stream = PiSessionStream(session_id="sess_001")
        e1 = stream.append_event("system_prompt", {"text": "System Init"}, turn_index=0)
        e2 = stream.append_event("user_prompt", {"text": "Explore src/"}, turn_index=1)
        e3 = stream.append_event("tool_call", {"tool_name": "list_dir", "path": "src/"}, turn_index=1)
        e4 = stream.append_event("tool_result", {"tool_name": "list_dir", "output": "core.py\ncli.py"}, turn_index=1)

        # JSONL Export & Import
        jsonl_text = stream.serialize_to_jsonl()
        self.assertEqual(len(jsonl_text.strip().split("\n")), 4)
        restored = PiSessionStream.deserialize_from_jsonl("sess_001_restored", jsonl_text)
        self.assertEqual(len(restored.events), 4)
        self.assertEqual(restored.events[0].kind, "system_prompt")
        self.assertEqual(restored.events[3].payload["output"], "core.py\ncli.py")

        # DAG Branching from Turn 1 (e2)
        branched = stream.branch_from(e2.event_id, new_session_id="sess_001_alternative")
        self.assertEqual(len(branched.events), 2)
        self.assertEqual(branched.events[-1].event_id, e2.event_id)

        # Append to branched stream without polluting original
        branched.append_event("user_prompt", {"text": "Alternative path: explore tests/"}, turn_index=2)
        self.assertEqual(len(branched.events), 3)
        self.assertEqual(len(stream.events), 4)  # original unchanged

    def test_07_session_portability_cross_format_conversion(self):
        """Converts cleanly between Pi (stream), Codex (typed snapshot), and Claude (working memory)."""
        stream = PiSessionStream(session_id="sess_alpha")
        stream.append_event("system_prompt", {"text": "Constitution: Rule 1"}, turn_index=0)
        stream.append_event("user_prompt", {"text": "Run build"}, turn_index=1)
        stream.append_event("tool_call", {"tool_name": "bash", "cmd": "make"}, turn_index=1)
        stream.append_event("tool_result", {"tool_name": "bash", "output": "success: build completed"}, turn_index=1)

        # Pi -> Codex Rollout Snapshot
        codex_snap = SessionPortabilityEngine.pi_to_codex(stream, policy="safe_exec")
        self.assertEqual(codex_snap.thread_id, "sess_alpha")
        self.assertEqual(codex_snap.execution_policy, "safe_exec")
        self.assertEqual(len(codex_snap.context_fragments), 1)
        self.assertEqual(len(codex_snap.turns), 1)
        self.assertEqual(len(codex_snap.turns[0]["actions"]), 2)

        # Codex Snapshot -> Pi Stream (Roundtrip)
        pi_roundtrip = SessionPortabilityEngine.codex_to_pi(codex_snap)
        self.assertEqual(len(pi_roundtrip.events), 4)
        self.assertEqual(pi_roundtrip.events[0].kind, "system_prompt")
        self.assertEqual(pi_roundtrip.events[-1].payload["output"], "success: build completed")

        # Pi -> Claude Working Memory
        claude_mem = SessionPortabilityEngine.pi_to_claude_memory(stream, objective="Build and deploy project")
        self.assertEqual(claude_mem.objective, "Build and deploy project")
        self.assertTrue(any("success" in m for m in claude_mem.completed_milestones))
        self.assertEqual(claude_mem.key_facts["session_id"], "sess_alpha")

    # ========================================================================
    # 3. 循环账本闭合与合成补账测试
    # ========================================================================

    def test_08_synthetic_ledger_happy_path(self):
        """Normal execution matches every call with real result."""
        ledger = SyntheticLedger()
        ledger.debit_call("call_01", "read_file", {"path": "README.md"})
        self.assertFalse(ledger.is_balanced)

        ledger.credit_result("call_01", "# Repo README")
        self.assertTrue(ledger.is_balanced)
        ledger.assert_ledger_balanced()

    def test_09_synthetic_ledger_unbalanced_error(self):
        """Unclosed dangling call raises ProtocolLedgerUnbalancedError."""
        ledger = SyntheticLedger()
        ledger.debit_call("call_01", "bash", {"cmd": "pytest"})
        self.assertFalse(ledger.is_balanced)

        with self.assertRaises(ProtocolLedgerUnbalancedError) as ctx:
            ledger.assert_ledger_balanced()
        self.assertIn("left dangling", str(ctx.exception))

    def test_10_synthetic_ledger_abnormal_interruption_synthesized(self):
        """Abnormal interruption synthesizes tool results to preserve model deserialization."""
        ledger = SyntheticLedger()
        ledger.debit_call("call_01", "query_db", {"q": "SELECT 1"})
        ledger.debit_call("call_02", "long_running_job", {"timeout": 300})

        # Call 1 finishes normally
        ledger.credit_result("call_01", "Result: 1")

        # Call 2 was interrupted by user hitting Ctrl+C
        synthetic_results = ledger.abort_and_synthesize("User pressed Ctrl+C")
        self.assertEqual(len(synthetic_results), 1)
        self.assertEqual(synthetic_results[0].call_id, "call_02")
        self.assertTrue(synthetic_results[0].is_synthetic)
        self.assertIn("[SYNTHETIC_RESULT: Interrupted - User pressed Ctrl+C]", synthetic_results[0].result_payload)

        # Invariants hold
        self.assertTrue(ledger.is_balanced)
        ledger.assert_ledger_balanced()

    # ========================================================================
    # 4. 架构决策分析与选型求解器测试
    # ========================================================================

    def test_11_architecture_decision_advisor_enterprise_codex(self):
        """Multi-tenant enterprise workloads map to Codex typed governance."""
        profile = WorkloadProfile(
            untrusted_code_execution=True,
            multi_tenant_saas=True,
            strict_audit_compliance=True,
            custom_ide_embed=False,
            latency_critical_ttft=False,
            team_size=50
        )
        prescription = ArchitectureDecisionAdvisor.evaluate(profile)
        self.assertEqual(prescription.archetype, ArchitecturalArchetype.CODEX_TYPED_GOVERNANCE)
        self.assertEqual(prescription.sandbox_tier, SandboxIsolationTier.MICRO_VM_FIRECRACKER)
        self.assertEqual(prescription.session_storage, SessionStorageType.TYPED_ROLLOUT_DB)
        self.assertEqual(prescription.delegation_style, "TOOLIZED_DELEGATION_PROTOCOL")

    def test_12_architecture_decision_advisor_pi_microkernel(self):
        """Embedded IDE latency-sensitive environments map to Pi micro-kernel."""
        profile = WorkloadProfile(
            untrusted_code_execution=False,
            multi_tenant_saas=False,
            strict_audit_compliance=False,
            custom_ide_embed=True,
            latency_critical_ttft=True,
            team_size=1
        )
        prescription = ArchitectureDecisionAdvisor.evaluate(profile)
        self.assertEqual(prescription.archetype, ArchitecturalArchetype.PI_MICRO_KERNEL)
        self.assertEqual(prescription.sandbox_tier, SandboxIsolationTier.NONE)
        self.assertEqual(prescription.session_storage, SessionStorageType.APPEND_ONLY_JSONL)
        self.assertEqual(prescription.delegation_style, "DIRECT_COROUTINE_DISPATCH")

    def test_13_architecture_decision_advisor_claude_code_team(self):
        """Team software engineering maps to Claude Code runtime resilient archetype."""
        profile = WorkloadProfile(
            untrusted_code_execution=True,
            multi_tenant_saas=False,
            strict_audit_compliance=False,
            custom_ide_embed=False,
            latency_critical_ttft=False,
            team_size=10
        )
        prescription = ArchitectureDecisionAdvisor.evaluate(profile)
        self.assertEqual(prescription.archetype, ArchitecturalArchetype.CLAUDE_CODE_RUNTIME_RESILIENT)
        self.assertEqual(prescription.sandbox_tier, SandboxIsolationTier.LOCAL_DOCKER_NO_NET)
        self.assertEqual(prescription.session_storage, SessionStorageType.LIVING_WORKING_MEMORY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
