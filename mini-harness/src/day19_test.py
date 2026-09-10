"""Unit tests for Day 19: Error Recovery, Resilience & Process Governance."""

import unittest
from typing import Any, Dict, List

from day19_error_recovery import (
    CompactionEscapeHatch,
    RecoverableErrorType,
    RecoveryAction,
    RecoveryContextState,
    ResilientQueryRecoveryEngine,
)


class TestDay19ErrorRecovery(unittest.TestCase):
    """Deterministic test suite validating Claude Code Ch 6 resilience principles."""

    def setUp(self):
        self.engine = ResilientQueryRecoveryEngine()

    def test_01_withheld_error_whitelist(self):
        """Verify only whitelisted errors are withheld; fatal errors surface immediately."""
        # Whitelisted recoverable errors
        self.assertEqual(
            self.engine.identify_error("API Error: prompt_too_long - context exceeded 128k"),
            RecoverableErrorType.PROMPT_TOO_LONG,
        )
        self.assertEqual(
            self.engine.identify_error("Execution stopped: max_output_tokens reached"),
            RecoverableErrorType.MAX_OUTPUT_TOKENS,
        )
        self.assertEqual(
            self.engine.identify_error("Payload Error: media_size too large for inline prompt"),
            RecoverableErrorType.MEDIA_SIZE_EXCEEDED,
        )

        # Fatal / Non-recoverable error
        fatal_error = "OperationalError: Database disk full"
        self.assertIsNone(self.engine.identify_error(fatal_error))

        state = RecoveryContextState()
        decision = self.engine.route_error_recovery(fatal_error, state, [])
        self.assertEqual(decision.action, RecoveryAction.SURFACE_ERROR)
        self.assertFalse(decision.skip_stop_hooks)
        self.assertIn("Non-recoverable", decision.reason)

    def test_02_ptl_recovery_ladder(self):
        """Verify stratified recovery: 1) Collapse Drain -> 2) Reactive Compact -> 3) Surface."""
        state = RecoveryContextState(staged_collapse_count=1)

        # Step 1: Drain Staged Collapse
        d1 = self.engine.route_error_recovery("Error: prompt_too_long", state, [])
        self.assertEqual(d1.action, RecoveryAction.DRAIN_COLLAPSE)
        self.assertEqual(state.staged_collapse_count, 0)
        self.assertFalse(d1.skip_stop_hooks)

        # Step 2: Reactive Compact
        d2 = self.engine.route_error_recovery("Error: prompt_too_long", state, [])
        self.assertEqual(d2.action, RecoveryAction.REACTIVE_COMPACT)
        self.assertTrue(state.has_attempted_reactive_compact)
        self.assertFalse(d2.skip_stop_hooks)

        # Step 3: Unrecoverable after compact -> Surface Error with stop hooks bypassed
        d3 = self.engine.route_error_recovery("Error: prompt_too_long", state, [])
        self.assertEqual(d3.action, RecoveryAction.SURFACE_ERROR)
        self.assertTrue(d3.skip_stop_hooks)

    def test_03_breaking_the_death_spiral_skip_stop_hooks(self):
        """Invariant: Once reactive compact has been attempted, subsequent PTL MUST skip stop hooks."""
        state = RecoveryContextState(has_attempted_reactive_compact=True)
        decision = self.engine.route_error_recovery("context_length_exceeded", state, [])
        
        self.assertEqual(decision.action, RecoveryAction.SURFACE_ERROR)
        self.assertTrue(decision.skip_stop_hooks, "Must skip stop hooks to prevent Death Spiral!")

    def test_04_compaction_escape_hatch_ptl_retry(self):
        """Verify emergency escape hatch: drop oldest conversation round when compact itself PTLs."""
        messages = [
            {"role": "system", "content": "You are a coding assistant."},
            # Round 1
            {"role": "user", "content": "Query 1"},
            {"role": "assistant", "content": "Answer 1"},
            # Round 2
            {"role": "user", "content": "Query 2"},
            {"role": "assistant", "content": "Answer 2"},
            # Round 3
            {"role": "user", "content": "Query 3"},
            {"role": "assistant", "content": "Answer 3"},
        ]

        # Drop 1 round from head
        trimmed = CompactionEscapeHatch.truncate_head_for_ptl_retry(messages, drop_round_count=1)

        # System prompt preserved at index 0
        self.assertEqual(trimmed[0]["role"], "system")
        self.assertEqual(trimmed[0]["content"], "You are a coding assistant.")

        # Round 1 should be gone
        contents = [m["content"] for m in trimmed]
        self.assertNotIn("Query 1", contents)
        self.assertNotIn("Answer 1", contents)

        # Rounds 2 and 3 preserved
        self.assertIn("Query 2", contents)
        self.assertIn("Query 3", contents)

    def test_05_mot_continuation_over_recap(self):
        """Verify MOT handling: 1st tier boosts Cap without meta; 2nd tier injects no-recap continuation."""
        state = RecoveryContextState(current_output_cap=4096, max_output_cap_limit=16384)

        # Layer 1: Cap boost
        d1 = self.engine.route_error_recovery("max_output_tokens", state, [])
        self.assertEqual(d1.action, RecoveryAction.BOOST_TOKEN_CAP)
        self.assertEqual(d1.new_token_cap, 16384)
        self.assertIsNone(d1.meta_message, "Cap boost should NOT inject unnecessary meta prompts")
        self.assertEqual(state.current_output_cap, 16384)

        # Layer 2: Already at MAX cap -> Seamless Continuation
        d2 = self.engine.route_error_recovery("max_output_tokens", state, [])
        self.assertEqual(d2.action, RecoveryAction.CONTINUE_WITHOUT_RECAP)
        self.assertIsNotNone(d2.meta_message)
        meta_text = d2.meta_message["content"]
        self.assertIn("Do NOT apologize", meta_text)
        self.assertIn("do NOT recap", meta_text)
        self.assertIn("continue seamlessly from the exact break point", meta_text)
        self.assertEqual(state.mot_recovery_count, 1)

    def test_06_user_abort_ledger_closure_and_compact_invariant(self):
        """Verify user Esc abort produces synthetic results for dangling tools and marks compact failed."""
        pending_tools = [
            {"id": "tool_call_001", "name": "grep_search"},
            {"id": "tool_call_002", "name": "run_bash"},
        ]

        # Case 1: Normal query abort
        synthetic_results, compact_ok = self.engine.handle_user_abort(pending_tools, is_compacting=False)
        self.assertEqual(len(synthetic_results), 2)
        self.assertEqual(synthetic_results[0]["tool_use_id"], "tool_call_001")
        self.assertTrue(synthetic_results[0]["is_error"])
        self.assertIn("[User aborted", synthetic_results[0]["content"])
        self.assertTrue(compact_ok)

        # Case 2: Abort during compact -> Invariant: MUST NOT allow compact success
        _, compact_ok_during_compact = self.engine.handle_user_abort(pending_tools, is_compacting=True)
        self.assertFalse(compact_ok_during_compact, "Aborted compact must NEVER be marked as successful!")


if __name__ == "__main__":
    unittest.main(verbosity=2)
