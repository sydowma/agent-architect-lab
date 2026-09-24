"""Unit tests for Aegis-Sandbox Temporal Workflow & State Machine Subsystem.

Zero-dependency standard unittest suite testing:
1. Event Store immutability and monotonic event sequence.
2. Deterministic Replay Engine: Zero duplicate API/token calls on crash recovery.
3. Mid-flight crash recovery: Seamless transition from REPLAY to ACTIVE mode.
4. Human-in-the-Loop (HITL) Takeover and Resume signal coordination.
5. Dynamic instruction correction via InterventionSignal.
6. Saga pattern LIFO backward compensation upon activity failure.
7. Side-effect-free status and trajectory queries.
"""

from __future__ import annotations

import unittest
from typing import Any, Dict, List

from workflow import (
    ComputerUseAgentWorkflow,
    EventStore,
    EventType,
    HistoryEvent,
    SagaCoordinator,
    SignalChannel,
    WorkflowContext,
    WorkflowMode,
)


class TestTemporalWorkflowEngine(unittest.TestCase):
    """Test suite for Aegis-Sandbox Workflow State Machine."""

    def test_event_store_monotonic_immutability(self) -> None:
        """Validates that history events have monotonic IDs and are immutable."""
        store = EventStore()
        ev1 = store.append(EventType.WORKFLOW_STARTED, name="test_wf", payload={"k": "v"})
        ev2 = store.append(EventType.ACTIVITY_SCHEDULED, name="step_1")

        self.assertEqual(ev1.event_id, 1)
        self.assertEqual(ev2.event_id, 2)
        self.assertEqual(len(store), 2)

        # Immutability verification: frozen dataclass cannot be altered
        with self.assertRaises(AttributeError):
            ev1.name = "tampered"  # type: ignore

    def test_deterministic_replay_zero_token_cost(self) -> None:
        """Validates that replaying an existing execution incurs ZERO model API calls."""
        store = EventStore()
        ctx_run1 = WorkflowContext("wf_001", event_store=store, is_replaying=False)

        real_call_counts = {"screen": 0, "model": 0, "input": 0}

        def mock_capture() -> str:
            real_call_counts["screen"] += 1
            return f"hash_{real_call_counts['screen']}"

        def mock_inference(screen_hash: str, instructions: str) -> Dict[str, Any]:
            real_call_counts["model"] += 1
            # Step 2 achieves goal
            is_done = real_call_counts["model"] >= 2
            return {"action": "click", "x": 500, "y": 300, "is_completed": is_done}

        def mock_inject(action: str, x: int, y: int) -> str:
            real_call_counts["input"] += 1
            return f"injected_{action}_at_{x}_{y}"

        # 1. 首次正式执行 (ACTIVE 模式)
        wf_run1 = ComputerUseAgentWorkflow(ctx_run1, goal="Book flight to Tokyo", max_steps=5)
        res1 = wf_run1.run(mock_capture, mock_inference, mock_inject)

        self.assertEqual(res1["status"], "COMPLETED")
        self.assertEqual(res1["steps"], 2)
        self.assertEqual(real_call_counts["model"], 2)
        self.assertEqual(real_call_counts["screen"], 2)

        initial_history_len = len(store)
        self.assertGreater(initial_history_len, 6)

        # 2. 模拟 Worker 宕机重启：新 Worker 基于已有 EventStore 开启 REPLAY 模式
        real_call_counts_replay = {"screen": 0, "model": 0, "input": 0}

        def mock_capture_replay() -> str:
            real_call_counts_replay["screen"] += 1
            return "unexpected_new_hash"

        def mock_inference_replay(screen_hash: str, instructions: str) -> Dict[str, Any]:
            real_call_counts_replay["model"] += 1
            return {"action": "click", "x": 0, "y": 0, "is_completed": False}

        def mock_inject_replay(action: str, x: int, y: int) -> str:
            real_call_counts_replay["input"] += 1
            return "unexpected_injection"

        ctx_run2 = WorkflowContext("wf_001", event_store=store, is_replaying=True)
        wf_run2 = ComputerUseAgentWorkflow(ctx_run2, goal="Book flight to Tokyo", max_steps=5)
        res2 = wf_run2.run(mock_capture_replay, mock_inference_replay, mock_inject_replay)

        # 验证：完全通过 History 提取，真实函数调用次数必须严格为 0！
        self.assertEqual(res2["status"], "COMPLETED")
        self.assertEqual(res2["steps"], 2)
        self.assertEqual(real_call_counts_replay["model"], 0, "Replay MUST incur 0 model calls!")
        self.assertEqual(real_call_counts_replay["screen"], 0)
        self.assertEqual(real_call_counts_replay["input"], 0)

        # 轨迹结果与第一次完全幂等一致
        self.assertEqual(len(wf_run2.trajectory), 2)
        self.assertEqual(wf_run2.trajectory[0].screen_hash, "hash_1")
        self.assertEqual(wf_run2.trajectory[1].screen_hash, "hash_2")

    def test_worker_crash_and_seamless_transition(self) -> None:
        """Validates that a crashed worker replays finished steps then seamlessly continues new steps."""
        store = EventStore()
        ctx_run1 = WorkflowContext("wf_002", event_store=store, is_replaying=False)

        run1_calls = {"model": 0}

        def mock_capture() -> str:
            return "hash_fixed"

        def mock_inference_crash(screen_hash: str, instructions: str) -> Dict[str, Any]:
            run1_calls["model"] += 1
            if run1_calls["model"] == 2:
                # 模拟 Worker 在第 2 步处理完毕后直接物理崩溃 (抛出断电/OOM 异常)
                raise SystemExit("Simulated Worker OOM Kill")
            return {"action": "click", "x": 100, "y": 100, "is_completed": False}

        def mock_inject(action: str, x: int, y: int) -> str:
            return "ok"

        wf_run1 = ComputerUseAgentWorkflow(ctx_run1, goal="Perform data migration", max_steps=5)
        with self.assertRaises(SystemExit):
            wf_run1.run(mock_capture, mock_inference_crash, mock_inject)

        self.assertEqual(run1_calls["model"], 2)

        # 恢复阶段：新 Worker 启动，重放前 1 步并继续执行第 2 步和第 3 步
        run2_calls = {"model": 0}

        def mock_inference_recovered(screen_hash: str, instructions: str) -> Dict[str, Any]:
            run2_calls["model"] += 1
            # 第 3 步成功收尾
            return {"action": "click", "x": 100, "y": 100, "is_completed": run2_calls["model"] >= 2}

        ctx_run2 = WorkflowContext("wf_002", event_store=store, is_replaying=True)
        wf_run2 = ComputerUseAgentWorkflow(ctx_run2, goal="Perform data migration", max_steps=5)
        res2 = wf_run2.run(mock_capture, mock_inference_recovered, mock_inject)

        self.assertEqual(res2["status"], "COMPLETED")
        # Step 1 从历史直接取，真实执行了第 2 步和第 3 步，共 2 次实际调用
        self.assertEqual(run2_calls["model"], 2)

    def test_human_in_the_loop_takeover_and_resume(self) -> None:
        """Validates Human Takeover suspension and subsequent Resume with action context injection."""
        store = EventStore()
        ctx = WorkflowContext("wf_hitl", event_store=store)
        wf = ComputerUseAgentWorkflow(ctx, goal="Checkout shopping cart", max_steps=5)

        # 模拟外部 WebRTC 悬浮窗发来人工接管信号 (TakeoverSignal)
        ctx.get_signal_channel("TakeoverSignal").send({
            "user_id": "operator_admin",
            "reason": "Payment 2FA QR code requires manual scan",
        })

        # 运行工作流：应该在第 1 步前立刻捕获 TakeoverSignal 并挂起 (SUSPENDED)
        res_suspended = wf.run(
            capture_screen_fn=lambda: "screen_login",
            model_inference_fn=lambda s, i: {"action": "none"},
            inject_input_fn=lambda a, x, y: "none",
        )

        self.assertEqual(res_suspended["status"], "SUSPENDED")
        self.assertTrue(wf.is_suspended)
        self.assertEqual(wf.takeover_count, 1)

        # 模拟人工在前端完成扫码后，点击交还控制权 (ResumeSignal)
        ctx.get_signal_channel("ResumeSignal").send({
            "summary": "User completed QR scan and dismissed 2FA modal",
        })

        # 重新唤醒执行：工作流解除挂起，并将人工操作摘要注入上下文继续完成后续步骤
        res_resumed = wf.run(
            capture_screen_fn=lambda: "screen_success",
            model_inference_fn=lambda s, i: {"action": "click", "x": 200, "y": 200, "is_completed": True},
            inject_input_fn=lambda a, x, y: "clicked",
        )

        self.assertEqual(res_resumed["status"], "COMPLETED")
        self.assertFalse(wf.is_suspended)
        self.assertIn("USER_ACTION: User completed QR scan", wf.active_instructions)

    def test_intervention_signal_dynamic_override(self) -> None:
        """Validates that InterventionSignal dynamically updates active instructions."""
        store = EventStore()
        ctx = WorkflowContext("wf_intervene", event_store=store)
        wf = ComputerUseAgentWorkflow(ctx, goal="Search shoes", max_steps=3)

        # 预先向信号队列投递指导信号
        ctx.get_signal_channel("InterventionSignal").send("Focus on red running shoes only")

        step_received_instructions: List[str] = []

        def capture() -> str:
            return "hash"

        def inference(screen_hash: str, instructions: str) -> Dict[str, Any]:
            step_received_instructions.append(instructions)
            return {"action": "click", "x": 10, "y": 10, "is_completed": True}

        wf.run(capture, inference, lambda a, x, y: "ok")

        self.assertEqual(len(step_received_instructions), 1)
        self.assertIn("CORRECTION: Focus on red running shoes only", step_received_instructions[0])

    def test_saga_compensation_lifo_rollback(self) -> None:
        """Validates that workflow failures trigger registered compensations in reverse LIFO order."""
        store = EventStore()
        ctx = WorkflowContext("wf_saga", event_store=store)
        wf = ComputerUseAgentWorkflow(ctx, goal="Deploy microservice", max_steps=5)

        rollback_history: List[str] = []

        def mock_rollback(ckpt_id: str) -> None:
            rollback_history.append(ckpt_id)

        step_counter = 0

        def capture() -> str:
            return "screen"

        def inference_failing(s: str, i: str) -> Dict[str, Any]:
            nonlocal step_counter
            step_counter += 1
            if step_counter == 3:
                raise RuntimeError("Disk full / kernel lockup at step 3")
            return {"action": "click", "x": 10, "y": 10, "is_completed": False}

        with self.assertRaises(RuntimeError):
            wf.run(
                capture_screen_fn=capture,
                model_inference_fn=inference_failing,
                inject_input_fn=lambda a, x, y: "ok",
                rollback_checkpoint_fn=mock_rollback,
            )

        # 验证 Saga 补偿栈：以 LIFO 后进先出顺序逆向回滚
        self.assertEqual(len(rollback_history), 3)
        self.assertEqual(rollback_history, ["ckpt_step_3", "ckpt_step_2", "ckpt_step_1"])

        # 验证 EventStore 完整记录了补偿事件与失败事件
        event_types = [ev.event_type for ev in store.get_events()]
        self.assertIn(EventType.COMPENSATION_EXECUTED, event_types)
        self.assertIn(EventType.WORKFLOW_FAILED, event_types)

    def test_side_effect_free_queries(self) -> None:
        """Validates that status queries do not pollute immutable event history."""
        store = EventStore()
        ctx = WorkflowContext("wf_query", event_store=store)
        wf = ComputerUseAgentWorkflow(ctx, goal="Analyze spreadsheet", max_steps=2)

        # 执行一次查询
        status_before = ctx.query("get_status")
        self.assertEqual(status_before["workflow_id"], "wf_query")
        self.assertEqual(status_before["steps_completed"], 0)
        self.assertEqual(len(store), 0, "Query handler must NOT write events to history")

        # 跑完 1 步
        wf.run(
            capture_screen_fn=lambda: "s1",
            model_inference_fn=lambda s, i: {"action": "click", "x": 1, "y": 1, "is_completed": True},
            inject_input_fn=lambda a, x, y: "ok",
        )

        status_after = ctx.query("get_status")
        self.assertEqual(status_after["steps_completed"], 1)
        self.assertTrue(status_after["is_completed"])

        trajectory = ctx.query("get_trajectory")
        self.assertEqual(len(trajectory), 1)
        self.assertEqual(trajectory[0]["action_type"], "click")


if __name__ == "__main__":
    unittest.main()
