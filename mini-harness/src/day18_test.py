"""Unit tests for Day 18: Context Compaction, Memory Index Governance & Controlled Reboot."""

import sys
import unittest
from typing import Any, Dict, List

from day18_context_compaction import (
    AutoCompactTracker,
    CompactBoundary,
    ContextCompactor,
    MemoryIndexManager,
    SessionMemory,
)


class TestDay18ContextCompaction(unittest.TestCase):
    """Deterministic test suite validating Claude Code Ch 5 principles."""

    def test_01_memory_index_manager_truncation(self):
        """Verify MEMORY.md 200-line and 25KB physical boundary enforcement."""
        mgr = MemoryIndexManager()

        # Add 10 normal entries
        for i in range(10):
            mgr.add_memory(f"Topic {i}", f"Short summary {i}", f"Detailed body {i}")
        
        content, truncated = mgr.read_entrypoint()
        self.assertFalse(truncated)
        self.assertIn("Topic 9", content)
        self.assertNotIn("WARNING", content)

        # Now flood with 250 entries to exceed MAX_ENTRYPOINT_LINES = 200
        for i in range(10, 260):
            mgr.add_memory(f"Topic {i}", f"Pointer line {i}")

        content_overflow, truncated_overflow = mgr.read_entrypoint()
        self.assertTrue(truncated_overflow)
        lines = content_overflow.splitlines()
        # Should be truncated at 200 lines plus warning
        self.assertIn(MemoryIndexManager.TRUNCATION_WARNING.strip(), content_overflow)
        self.assertLessEqual(len(lines), 205)

    def test_02_autocompact_thresholds_and_budgets(self):
        """Verify context budget calculation: 20k summary reserve, 13k buffer."""
        compactor = ContextCompactor(context_window=128_000)

        effective_window = compactor.get_effective_context_window()
        # 128,000 - 20,000 = 108,000
        self.assertEqual(effective_window, 108_000)

        threshold = compactor.get_auto_compact_threshold()
        # 108,000 - 13,000 = 95,000
        self.assertEqual(threshold, 95_000)

        self.assertFalse(compactor.should_auto_compact(94_999))
        self.assertTrue(compactor.should_auto_compact(95_000))
        self.assertTrue(compactor.should_auto_compact(120_000))

        # Test 200k window
        compactor_200k = ContextCompactor(context_window=200_000)
        self.assertEqual(compactor_200k.get_effective_context_window(), 180_000)
        self.assertEqual(compactor_200k.get_auto_compact_threshold(), 167_000)

    def test_03_pre_compact_cleansing(self):
        """Verify pre-compact stripping of raw images and temporary attachments."""
        compactor = ContextCompactor()
        raw_messages = [
            {
                "role": "user",
                "content": "Please check this diagram: data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            },
            {
                "role": "assistant",
                "content": "I see the diagram: ![diagram](/tmp/diagram.png)"
            },
            {
                "role": "user",
                "content": (
                    "Here is the file attachment:\n"
                    "<attachment path=\"src/huge_service.py\">\n"
                    + ("# Code line\n" * 500)
                    + "</attachment>\n"
                    "<raw_test_log>\n"
                    + ("Error stacktrace line\n" * 100)
                    + "</raw_test_log>"
                )
            }
        ]

        cleansed = compactor.cleanse_messages_for_summary(raw_messages)
        
        # 1. Base64 replaced
        self.assertNotIn("base64,iVBOR", cleansed[0]["content"])
        self.assertIn("[image]", cleansed[0]["content"])

        # 2. Markdown image replaced
        self.assertNotIn("![diagram]", cleansed[1]["content"])
        self.assertIn("[image]", cleansed[1]["content"])

        # 3. File attachment replaced
        self.assertNotIn("# Code line", cleansed[2]["content"])
        self.assertIn("[attachment: src/huge_service.py]", cleansed[2]["content"])
        self.assertIn("[raw_test_log: truncated]", cleansed[2]["content"])

    def test_04_controlled_reboot_and_work_semantic_reconstruction(self):
        """Verify controlled reboot: resets stale read cache, re-attaches files, plans & skills."""
        compactor = ContextCompactor()
        messages = [
            {"role": "user", "content": "Let's fix the concurrency bug in cache."},
            {"role": "assistant", "content": "I am reading buggy_cache.py and running tests."},
            {"role": "user", "content": "Great, now write the fix."},
        ]
        active_files = {
            "src/buggy_cache.py": "class ThreadSafeCache:\n    def __init__(self):\n        self.lock = Lock()\n"
        }
        plan_state = {
            "name": "Cache Concurrency & Leak Fix",
            "current_step": "Step 2: Add RLock to acquire()",
            "remaining_steps": ["Step 3: Add WeakValueDictionary", "Step 4: Pytest regression test"],
        }
        # Huge skill prompt that should be capped (per-skill truncation beats dropping)
        huge_skill_prompt = "Discipline Rule:\n" + ("Always verify concurrency safety before writing.\n" * 100)
        invoked_skills = {
            "concurrency_expert": huge_skill_prompt
        }

        def mock_summarizer(msgs: List[Dict[str, Any]]) -> SessionMemory:
            return SessionMemory(
                current_state="Fixing lock contention in acquire()",
                task_specification="Eliminate race condition in cache put/get",
                files_and_functions="buggy_cache.py: acquire(), put()",
                errors_and_corrections="TypeError in mutex unwrapping was resolved",
            )

        reconstructed, boundary = compactor.compact_conversation(
            messages=messages,
            active_files=active_files,
            summarizer_func=mock_summarizer,
            plan_state=plan_state,
            invoked_skills=invoked_skills,
            current_tokens=98_000,
            turn_id="turn_42",
        )

        # Assertions on post-compact state
        self.assertTrue(compactor.tracker.compacted)
        self.assertEqual(compactor.tracker.consecutive_failures, 0)
        self.assertIn("src/buggy_cache.py", compactor.read_file_state)

        # Boundary check
        self.assertIsInstance(boundary, CompactBoundary)
        self.assertEqual(boundary.pre_compact_tokens, 98_000)
        self.assertEqual(boundary.compacted_turns, 2)  # 2 user turns

        # Semantic reconstruction check
        types = [m.get("type") for m in reconstructed]
        self.assertIn("compact_boundary", types)
        self.assertIn("session_memory_injection", types)
        self.assertIn("active_file_attachment", types)
        self.assertIn("plan_mode_attachment", types)
        self.assertIn("invoked_skill_attachment", types)

        # Verify skill truncation: 'per-skill truncation beats dropping'
        skill_msg = next(m for m in reconstructed if m.get("type") == "invoked_skill_attachment")
        self.assertIn("[Skill instruction truncated to preserve context budget]", skill_msg["content"])
        self.assertIn("Discipline Rule:", skill_msg["content"])

    def test_05_consecutive_failure_circuit_breaker(self):
        """Verify that 3 consecutive compaction failures trigger the circuit breaker."""
        compactor = ContextCompactor()
        messages = [{"role": "user", "content": "Hello"}]

        def broken_summarizer(msgs):
            raise ValueError("Simulated API failure or malformed output during summarization")

        # Failure 1
        with self.assertRaises(ValueError):
            compactor.compact_conversation(messages, {}, broken_summarizer, turn_id="t1")
        self.assertEqual(compactor.tracker.consecutive_failures, 1)
        self.assertFalse(compactor.tracker.circuit_broken)

        # Failure 2
        with self.assertRaises(ValueError):
            compactor.compact_conversation(messages, {}, broken_summarizer, turn_id="t2")
        self.assertEqual(compactor.tracker.consecutive_failures, 2)
        self.assertFalse(compactor.tracker.circuit_broken)

        # Failure 3 - Trips Circuit Breaker!
        with self.assertRaises(ValueError):
            compactor.compact_conversation(messages, {}, broken_summarizer, turn_id="t3")
        self.assertEqual(compactor.tracker.consecutive_failures, 3)
        self.assertTrue(compactor.tracker.circuit_broken)

        # 4th Call - Refused by circuit breaker before even invoking summarizer
        with self.assertRaises(RuntimeError) as ctx:
            compactor.compact_conversation(messages, {}, broken_summarizer, turn_id="t4")
        self.assertIn("AutoCompact Circuit Breaker Tripped!", str(ctx.exception))

    def test_06_session_memory_aggressive_condensation(self):
        """Verify aggressive condensation prioritizes Current State & Errors & Corrections."""
        mem = SessionMemory(
            current_state="Currently debugging race condition at line 45",
            errors_and_corrections="Fix deadlock by using RLock instead of Lock",
            worklog="Detailed log item " * 200,
            learnings="Interesting learning about Python GIL " * 100,
            codebase_docs="Architecture notes on caching module " * 100,
        )

        condensed = mem.aggressive_condense(max_total_chars=1_000)
        rendered = condensed.render_markdown()

        # Operational essentials must be preserved intact
        self.assertIn("Currently debugging race condition at line 45", rendered)
        self.assertIn("Fix deadlock by using RLock instead of Lock", rendered)

        # Bloated secondary sections must be condensed
        self.assertIn("[Worklog condensed]", rendered)
        self.assertIn("[Learnings condensed]", rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
