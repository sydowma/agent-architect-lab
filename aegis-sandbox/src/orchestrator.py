"""Aegis-Sandbox Distributed Orchestrator & Event-Sourced State Machine.

Faithfully implements Temporal.io Workflow Execution Mechanics for Long-Horizon Agents:
1. Append-Only Event History & Event Sourcing (EventRecord).
2. Deterministic Replay Engine (zero-token, sub-millisecond crash recovery).
3. Human-in-the-Loop (HITL) Asynchronous Signal Channels (Takeover / Approve / Reject).
4. Saga Pattern Compensating Transactions (reverse rollback on irrecoverable failures).
5. Seamless transition from Replay Mode to Live Execution Mode.
"""

from __future__ import annotations

import collections
import dataclasses
import enum
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple


# ============================================================================
# 1. 事件模型与不可变历史账本 (Event Sourcing Protocol)
# ============================================================================

class WorkflowEventType(str, enum.Enum):
    WORKFLOW_STARTED = "WORKFLOW_STARTED"
    ACTIVITY_SCHEDULED = "ACTIVITY_SCHEDULED"
    ACTIVITY_COMPLETED = "ACTIVITY_COMPLETED"
    ACTIVITY_FAILED = "ACTIVITY_FAILED"
    SIGNAL_RECEIVED = "SIGNAL_RECEIVED"
    HUMAN_TAKEOVER_STARTED = "HUMAN_TAKEOVER_STARTED"
    HUMAN_TAKEOVER_RESUMED = "HUMAN_TAKEOVER_RESUMED"
    COMPENSATION_TRIGGERED = "COMPENSATION_TRIGGERED"
    COMPENSATION_COMPLETED = "COMPENSATION_COMPLETED"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"


@dataclasses.dataclass(frozen=True)
class EventRecord:
    """An immutable fact recorded in the workflow history."""
    event_id: int
    event_type: WorkflowEventType
    activity_name: Optional[str] = None
    step_index: Optional[int] = None
    payload: Dict[str, Any] = dataclasses.field(default_factory=dict)
    timestamp: float = dataclasses.field(default_factory=time.time)


class EventHistory:
    """Thread-safe append-only event store for deterministic workflow execution."""

    def __init__(self) -> None:
        self._events: List[EventRecord] = []
        self._lock = threading.Lock()
        self._next_id = 1

    def append(
        self,
        event_type: WorkflowEventType,
        activity_name: Optional[str] = None,
        step_index: Optional[int] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> EventRecord:
        with self._lock:
            evt = EventRecord(
                event_id=self._next_id,
                event_type=event_type,
                activity_name=activity_name,
                step_index=step_index,
                payload=payload or {},
                timestamp=time.time(),
            )
            self._next_id += 1
            self._events.append(evt)
            return evt

    def find_activity_result(self, activity_name: str, step_index: int) -> Tuple[bool, Any]:
        """Queries whether an activity was already completed in past history."""
        with self._lock:
            for evt in self._events:
                if (
                    evt.event_type == WorkflowEventType.ACTIVITY_COMPLETED
                    and evt.activity_name == activity_name
                    and evt.step_index == step_index
                ):
                    return True, evt.payload.get("result")
            return False, None

    def all_events(self) -> List[EventRecord]:
        with self._lock:
            return list(self._events)

    def count(self) -> int:
        with self._lock:
            return len(self._events)


# ============================================================================
# 2. 信号与人机回路通道 (Human-in-the-Loop Signals)
# ============================================================================

class SignalType(str, enum.Enum):
    TAKEOVER = "TAKEOVER"       # 人工请求夺取控制权
    RESUME = "RESUME"           # 人工操作完毕，交还控制权
    APPROVE = "APPROVE"         # 批准敏感高危操作
    REJECT = "REJECT"           # 拒绝敏感操作并回滚


@dataclasses.dataclass(frozen=True)
class SignalPayload:
    signal_type: SignalType
    data: Dict[str, Any] = dataclasses.field(default_factory=dict)
    timestamp: float = dataclasses.field(default_factory=time.time)


class SignalChannel:
    """Non-blocking asynchronous channel for external human intervention."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._queue: queue.Queue[SignalPayload] = queue.Queue()

    def send(self, signal_type: SignalType, data: Optional[Dict[str, Any]] = None) -> None:
        self._queue.put(SignalPayload(signal_type=signal_type, data=data or {}))

    def receive(self, timeout: Optional[float] = None) -> Optional[SignalPayload]:
        try:
            return self._queue.get(block=True, timeout=timeout)
        except queue.Empty:
            return None

    def has_data(self) -> bool:
        return not self._queue.empty()


# ============================================================================
# 3. Activity 契约与 Saga 逆向补偿 (Compensating Transactions)
# ============================================================================

@dataclasses.dataclass
class ActivityDefinition:
    """Represents a discrete side-effecting task."""
    name: str
    execute_fn: Callable[..., Any]
    compensate_fn: Optional[Callable[..., None]] = None


# ============================================================================
# 4. 确定性事件溯源编排引擎 (EventSourcedAgentWorkflowEngine)
# ============================================================================

class WorkflowStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUSPENDED = "SUSPENDED"     # 等待人工信号或挂起接管
    REPLAYING = "REPLAYING"     # 故障恢复确定性重放中
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class EventSourcedAgentWorkflowEngine:
    """Industrial workflow orchestrator modeled after Temporal.io mechanics."""

    def __init__(
        self,
        workflow_id: str,
        history: Optional[EventHistory] = None,
    ) -> None:
        self.workflow_id = workflow_id
        self.history = history or EventHistory()
        self.status = WorkflowStatus.PENDING

        self.signal_channel = SignalChannel(f"signals_{workflow_id}")
        self._compensation_stack: List[Tuple[str, Callable[..., None], Any]] = []

        # Profiling telemetry
        self.live_invocations = 0
        self.replayed_invocations = 0

    def start_workflow(self, goal: str) -> None:
        """Starts workflow and registers entry event if not replaying."""
        if self.history.count() == 0:
            self.history.append(
                WorkflowEventType.WORKFLOW_STARTED,
                payload={"workflow_id": self.workflow_id, "goal": goal},
            )
            self.status = WorkflowStatus.RUNNING
        else:
            self.status = WorkflowStatus.REPLAYING

    def execute_activity(
        self,
        activity: ActivityDefinition,
        step_index: int,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Executes an activity with automatic Deterministic Replay detection."""
        # 1. Check if this activity step was already completed in history (Replay phase)
        found, cached_result = self.history.find_activity_result(activity.name, step_index)
        if found:
            self.replayed_invocations += 1
            # If compensation is registered, track it in the stack during replay as well
            if activity.compensate_fn:
                self._compensation_stack.append((activity.name, activity.compensate_fn, cached_result))
            return cached_result

        # 2. If not found in history, we have exited Replay mode and are in Live Execution!
        self.status = WorkflowStatus.RUNNING
        self.live_invocations += 1

        self.history.append(
            WorkflowEventType.ACTIVITY_SCHEDULED,
            activity_name=activity.name,
            step_index=step_index,
        )

        try:
            result = activity.execute_fn(*args, **kwargs)
            self.history.append(
                WorkflowEventType.ACTIVITY_COMPLETED,
                activity_name=activity.name,
                step_index=step_index,
                payload={"result": result},
            )
            # Register for Saga rollback if failure occurs later
            if activity.compensate_fn:
                self._compensation_stack.append((activity.name, activity.compensate_fn, result))
            return result

        except Exception as e:
            self.history.append(
                WorkflowEventType.ACTIVITY_FAILED,
                activity_name=activity.name,
                step_index=step_index,
                payload={"error": str(e)},
            )
            self.rollback_saga()
            self.status = WorkflowStatus.FAILED
            raise e

    def await_approval_or_takeover(
        self,
        step_index: int,
        action_desc: str,
        timeout_seconds: Optional[float] = None,
    ) -> bool:
        """Human-in-the-Loop gate: pauses workflow until user approves, rejects, or takes over."""
        # 1. Check if history already has the human decision for this step
        for evt in self.history.all_events():
            if (
                evt.event_type == WorkflowEventType.SIGNAL_RECEIVED
                and evt.step_index == step_index
            ):
                decision = evt.payload.get("decision")
                return decision == SignalType.APPROVE.value

        # 2. Live execution: wait for signal
        self.status = WorkflowStatus.SUSPENDED
        sig = self.signal_channel.receive(timeout=timeout_seconds)

        if not sig:
            # Timeout: reject by default for security
            self.history.append(
                WorkflowEventType.SIGNAL_RECEIVED,
                step_index=step_index,
                payload={"decision": "TIMEOUT_REJECTED"},
            )
            self.status = WorkflowStatus.RUNNING
            return False

        # Record signal into history for future determinism
        self.history.append(
            WorkflowEventType.SIGNAL_RECEIVED,
            step_index=step_index,
            payload={"decision": sig.signal_type.value, "data": sig.data},
        )
        self.status = WorkflowStatus.RUNNING

        if sig.signal_type == SignalType.TAKEOVER:
            self.history.append(WorkflowEventType.HUMAN_TAKEOVER_STARTED, step_index=step_index)
            # Await resume signal
            resume_sig = self.signal_channel.receive(timeout=timeout_seconds)
            self.history.append(
                WorkflowEventType.HUMAN_TAKEOVER_RESUMED,
                step_index=step_index,
                payload={"data": resume_sig.data if resume_sig else {}},
            )
            return True

        return sig.signal_type == SignalType.APPROVE

    def rollback_saga(self) -> None:
        """Executes compensating transactions in strict reverse chronological order."""
        self.history.append(WorkflowEventType.COMPENSATION_TRIGGERED)
        while self._compensation_stack:
            name, compensate_fn, intermediate_data = self._compensation_stack.pop()
            try:
                compensate_fn(intermediate_data)
                self.history.append(
                    WorkflowEventType.COMPENSATION_COMPLETED,
                    activity_name=name,
                    payload={"status": "reverted"},
                )
            except Exception as rollback_err:
                self.history.append(
                    WorkflowEventType.COMPENSATION_COMPLETED,
                    activity_name=name,
                    payload={"status": "rollback_failed", "error": str(rollback_err)},
                )
        self.status = WorkflowStatus.ROLLED_BACK

    def complete_workflow(self, final_output: Any) -> None:
        """Marks workflow as successfully completed."""
        self.history.append(
            WorkflowEventType.WORKFLOW_COMPLETED,
            payload={"output": final_output},
        )
        self.status = WorkflowStatus.COMPLETED
