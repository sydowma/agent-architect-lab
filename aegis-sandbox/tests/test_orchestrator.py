"""Unit tests for Aegis-Sandbox Distributed Orchestrator & Event-Sourced State Machine.

Tests modeled on Temporal.io mechanics:
1. Append-only event history recording and query invariants.
2. Happy-path multi-step agent workflow execution.
3. Deterministic Replay after node crash (zero re-execution of historical activities).
4. Human-in-the-Loop asynchronous approval and rejection gates.
5. Saga pattern reverse-order compensating transaction execution.
6. Human-in-the-Loop takeover and resumption signaling.
"""

import time
import unittest
from typing import List

from orchestrator import (
    ActivityDefinition,
    EventHistory,
    EventRecord,
    EventSourcedAgentWorkflowEngine,
    SignalChannel,
    SignalPayload,
    SignalType,
    WorkflowEventType,
    WorkflowStatus,
)


class TestOrchestratorAndEventSourcing(unittest.TestCase):
    """Test suite for event-sourced workflow engine and Temporal-like state machine."""

    def test_event_history_append_and_query(self) -> None:
        """Validates append-only immutability and activity lookup in history."""
        hist = EventHistory()
        evt1 = hist.append(WorkflowEventType.WORKFLOW_STARTED, payload={"goal": "test"})
        evt2 = hist.append(
            WorkflowEventType.ACTIVITY_COMPLETED,
            activity_name="click_button",
            step_index=1,
            payload={"result": "clicked_ok"},
        )

        self.assertEqual(hist.count(), 2)
        self.assertEqual(evt1.event_id, 1)
        self.assertEqual(evt2.event_id, 2)

        # Query existing completed activity
        found, res = hist.find_activity_result("click_button", 1)
        self.assertTrue(found)
        self.assertEqual(res, "clicked_ok")

        # Query non-existing activity
        found2, _ = hist.find_activity_result("click_button", 2)
        self.assertFalse(found2)

    def test_happy_path_workflow_execution(self) -> None:
        """Validates complete agent execution with event logging."""
        engine = EventSourcedAgentWorkflowEngine("wf_happy_01")
        engine.start_workflow(goal="Fill form and submit")

        calls = []

        act_perceive = ActivityDefinition(
            name="perceive_screen",
            execute_fn=lambda: calls.append("perceive") or "screen_data_v1",
        )
        act_click = ActivityDefinition(
            name="click_submit",
            execute_fn=lambda coord: calls.append(f"click_{coord}") or "clicked",
        )

        res1 = engine.execute_activity(act_perceive, step_index=1)
        res2 = engine.execute_activity(act_click, step_index=2, coord=(500, 300))

        engine.complete_workflow(final_output="Task completed successfully")

        self.assertEqual(res1, "screen_data_v1")
        self.assertEqual(res2, "clicked")
        self.assertEqual(calls, ["perceive", "click_(500, 300)"])
        self.assertEqual(engine.live_invocations, 2)
        self.assertEqual(engine.replayed_invocations, 0)
        self.assertEqual(engine.status, WorkflowStatus.COMPLETED)

    def test_deterministic_replay_after_crash(self) -> None:
        """CRUCIAL ARCHITECTURAL TEST:
        Simulates worker crash at Step 2.
        Re-running the workflow from a new worker using existing EventHistory MUST:
        1. Fast-forward Step 1 and Step 2 with 0 calls to LLM / Sandbox.
        2. Execute Step 3 in Live mode.
        """
        shared_history = EventHistory()

        # --- Worker 1: Executes Step 1 and Step 2, then CRASHES ---
        worker1_calls: List[str] = []
        act_step1 = ActivityDefinition(
            name="login_step",
            execute_fn=lambda: worker1_calls.append("login") or "logged_in",
        )
        act_step2 = ActivityDefinition(
            name="query_data_step",
            execute_fn=lambda: worker1_calls.append("query") or "report_100_rows",
        )
        act_step3 = ActivityDefinition(
            name="export_pdf_step",
            execute_fn=lambda: worker1_calls.append("export") or "pdf_ready",
        )

        engine1 = EventSourcedAgentWorkflowEngine("wf_crash_test", history=shared_history)
        engine1.start_workflow("Generate financial report")
        engine1.execute_activity(act_step1, step_index=1)
        engine1.execute_activity(act_step2, step_index=2)

        # Worker 1 executed 2 real invocations
        self.assertEqual(engine1.live_invocations, 2)
        self.assertEqual(worker1_calls, ["login", "query"])

        # >>> SIMULATE CRASH: Worker 1 dies here! <<<
        del engine1

        # --- Worker 2: Recovers from shared_history ---
        worker2_calls: List[str] = []
        act2_step1 = ActivityDefinition(
            name="login_step",
            execute_fn=lambda: worker2_calls.append("login_again") or "logged_in",
        )
        act2_step2 = ActivityDefinition(
            name="query_data_step",
            execute_fn=lambda: worker2_calls.append("query_again") or "report_100_rows",
        )
        act2_step3 = ActivityDefinition(
            name="export_pdf_step",
            execute_fn=lambda: worker2_calls.append("export") or "pdf_ready",
        )

        engine2 = EventSourcedAgentWorkflowEngine("wf_crash_test", history=shared_history)
        engine2.start_workflow("Generate financial report")

        # Re-run full workflow logic
        r1 = engine2.execute_activity(act2_step1, step_index=1)
        r2 = engine2.execute_activity(act2_step2, step_index=2)
        r3 = engine2.execute_activity(act2_step3, step_index=3)

        engine2.complete_workflow("All done")

        # VERIFICATIONS:
        # Results must be identical
        self.assertEqual(r1, "logged_in")
        self.assertEqual(r2, "report_100_rows")
        self.assertEqual(r3, "pdf_ready")

        # Worker 2 must NOT have re-executed step 1 or step 2!
        self.assertEqual(engine2.replayed_invocations, 2)
        self.assertEqual(engine2.live_invocations, 1)
        self.assertEqual(worker2_calls, ["export"])  # Only step 3 actually called!
        self.assertEqual(engine2.status, WorkflowStatus.COMPLETED)

    def test_saga_compensating_rollback_on_failure(self) -> None:
        """Validates that a failed step triggers compensating rollbacks in reverse order."""
        engine = EventSourcedAgentWorkflowEngine("wf_saga_01")
        engine.start_workflow("Transaction workflow")

        rollback_log = []

        act_create_tmp = ActivityDefinition(
            name="create_tmp_dir",
            execute_fn=lambda: "dir_101",
            compensate_fn=lambda res: rollback_log.append(f"delete_{res}"),
        )
        act_write_file = ActivityDefinition(
            name="write_file",
            execute_fn=lambda: "file_202",
            compensate_fn=lambda res: rollback_log.append(f"remove_{res}"),
        )

        def blow_up():
            raise RuntimeError("Disk quota exceeded!")

        act_faulty = ActivityDefinition(
            name="failing_action",
            execute_fn=blow_up,
        )

        engine.execute_activity(act_create_tmp, step_index=1)
        engine.execute_activity(act_write_file, step_index=2)

        with self.assertRaises(RuntimeError):
            engine.execute_activity(act_faulty, step_index=3)

        self.assertEqual(engine.status, WorkflowStatus.ROLLED_BACK)
        # Rollbacks must happen in STRICT REVERSE ORDER: file_202 -> dir_101
        self.assertEqual(rollback_log, ["remove_file_202", "delete_dir_101"])

    def test_human_in_the_loop_approval_and_rejection(self) -> None:
        """Validates asynchronous signal channel approval gate."""
        engine = EventSourcedAgentWorkflowEngine("wf_hitl_01")
        engine.start_workflow("Payment transfer")

        # Case 1: Human approves
        engine.signal_channel.send(SignalType.APPROVE, data={"user": "admin_mark"})
        approved = engine.await_approval_or_takeover(step_index=1, action_desc="Transfer $500")
        self.assertTrue(approved)

        # Case 2: Human rejects
        engine.signal_channel.send(SignalType.REJECT, data={"reason": "Suspicious recipient"})
        rejected = engine.await_approval_or_takeover(step_index=2, action_desc="Transfer $50000")
        self.assertFalse(rejected)

    def test_human_takeover_and_resume_signaling(self) -> None:
        """Validates Human-in-the-Loop takeover: Agent pauses, human intervenes, then resumes."""
        engine = EventSourcedAgentWorkflowEngine("wf_takeover_01")
        engine.start_workflow("Complex captcha bypass")

        # Human takes over control via WebRTC
        engine.signal_channel.send(SignalType.TAKEOVER, data={"operator": "mark"})
        # Human finishes, sends resume
        engine.signal_channel.send(SignalType.RESUME, data={"solved_captcha": True})

        handled = engine.await_approval_or_takeover(step_index=1, action_desc="Solve Captcha")
        self.assertTrue(handled)

        # Verify event sequence
        types = [e.event_type for e in engine.history.all_events()]
        self.assertIn(WorkflowEventType.HUMAN_TAKEOVER_STARTED, types)
        self.assertIn(WorkflowEventType.HUMAN_TAKEOVER_RESUMED, types)


if __name__ == "__main__":
    unittest.main()
