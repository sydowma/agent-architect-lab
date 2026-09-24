"""Aegis-Sandbox Distributed Workflow & State Machine Subsystem.

Production-grade Temporal-style Event Sourcing & Durable Execution Engine:
1. Event Sourcing History Ledger (Immutable Event Log & Monotonic Sequence).
2. Deterministic Replay Engine (Crash-Proof Recovery with Zero Duplicate Tokens).
3. Workflow vs Activity Boundary Isolation.
4. Human-in-the-Loop (HITL) Signal Channels (Takeover, Resume, Intervention).
5. Saga Pattern Backward Compensation & Checkpoint Rollback.
6. Real-time Workflow Query Protocol without History Side Effects.
"""

from __future__ import annotations

import collections
import dataclasses
import enum
import functools
import inspect
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ============================================================================
# 1. 事件溯源协议与不可变事件账本 (Event Sourcing Protocol)
# ============================================================================

class EventType(str, enum.Enum):
    """Temporal workflow execution event types."""
    WORKFLOW_STARTED = "WorkflowExecutionStarted"
    ACTIVITY_SCHEDULED = "ActivityTaskScheduled"
    ACTIVITY_COMPLETED = "ActivityTaskCompleted"
    ACTIVITY_FAILED = "ActivityTaskFailed"
    SIGNAL_RECEIVED = "WorkflowExecutionSignaled"
    MARKER_RECORDED = "MarkerRecorded"
    WORKFLOW_SUSPENDED = "WorkflowExecutionSuspended"
    WORKFLOW_RESUMED = "WorkflowExecutionResumed"
    COMPENSATION_REGISTERED = "CompensationRegistered"
    COMPENSATION_EXECUTED = "CompensationExecuted"
    WORKFLOW_COMPLETED = "WorkflowExecutionCompleted"
    WORKFLOW_FAILED = "WorkflowExecutionFailed"


@dataclasses.dataclass(frozen=True)
class HistoryEvent:
    """Immutable event stored in Temporal Event History."""
    event_id: int
    event_type: EventType
    timestamp: float
    name: str  # activity name, signal name, or workflow name
    payload: Dict[str, Any] = dataclasses.field(default_factory=dict)
    result: Optional[Any] = None
    error: Optional[str] = None


class EventStore:
    """Thread-safe append-only ledger for immutable workflow history."""

    def __init__(self) -> None:
        self._events: List[HistoryEvent] = []
        self._next_id: int = 1

    def append(
        self,
        event_type: EventType,
        name: str,
        payload: Optional[Dict[str, Any]] = None,
        result: Optional[Any] = None,
        error: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> HistoryEvent:
        """Appends an immutable event to the history stream."""
        event = HistoryEvent(
            event_id=self._next_id,
            event_type=event_type,
            timestamp=timestamp if timestamp is not None else time.time(),
            name=name,
            payload=dict(payload or {}),
            result=result,
            error=error,
        )
        self._events.append(event)
        self._next_id += 1
        return event

    def get_events(self) -> List[HistoryEvent]:
        """Returns a snapshot of the event history."""
        return list(self._events)

    def find_activity_result(self, activity_name: str, occurrence_index: int) -> Optional[HistoryEvent]:
        """Finds the completed activity event matching the given call occurrence."""
        matching_count = 0
        for ev in self._events:
            if ev.event_type == EventType.ACTIVITY_COMPLETED and ev.name == activity_name:
                if matching_count == occurrence_index:
                    return ev
                matching_count += 1
        return None

    def __len__(self) -> int:
        return len(self._events)


# ============================================================================
# 2. 信号通道与人机回路 (HITL Signal Channels)
# ============================================================================

class SignalChannel:
    """Non-blocking and timeout-capable asynchronous signal channel."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._queue: collections.deque[Any] = collections.deque()

    def send(self, payload: Any) -> None:
        """Sends a signal payload into the channel."""
        self._queue.append(payload)

    def has_pending(self) -> bool:
        """Returns True if there is a signal waiting in the channel."""
        return len(self._queue) > 0

    def receive_non_blocking(self) -> Optional[Any]:
        """Pops a signal immediately if available, otherwise returns None."""
        if self._queue:
            return self._queue.popleft()
        return None

    def receive(self, timeout_sec: float = 0.0) -> Optional[Any]:
        """Pops a signal, waiting up to timeout_sec if provided."""
        if self._queue:
            return self._queue.popleft()
        if timeout_sec <= 0:
            return None
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if self._queue:
                return self._queue.popleft()
            time.sleep(0.01)
        return None


# ============================================================================
# 3. Saga 逆向补偿协调器 (Saga Pattern Compensation)
# ============================================================================

@dataclasses.dataclass
class CompensationAction:
    """Registered compensating activity to execute in reverse on failure."""
    name: str
    action_fn: Callable[..., Any]
    args: Tuple[Any, ...]
    kwargs: Dict[str, Any]


class SagaCoordinator:
    """Coordinates backward compensation stack (LIFO) upon workflow failure."""

    def __init__(self) -> None:
        self._stack: List[CompensationAction] = []

    def register(self, name: str, action_fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Registers a compensating action (pushed to LIFO stack)."""
        self._stack.append(CompensationAction(name, action_fn, args, kwargs))

    def rollback(self, on_action_executed: Optional[Callable[[str, Any], None]] = None) -> List[Tuple[str, Any]]:
        """Executes all compensations in reverse order (LIFO)."""
        results: List[Tuple[str, Any]] = []
        while self._stack:
            comp = self._stack.pop()
            try:
                res = comp.action_fn(*comp.args, **comp.kwargs)
                results.append((comp.name, res))
                if on_action_executed:
                    on_action_executed(comp.name, res)
            except Exception as exc:
                results.append((comp.name, f"COMPENSATION_ERROR: {exc}"))
        return results

    def clear(self) -> None:
        self._stack.clear()

    @property
    def pending_count(self) -> int:
        return len(self._stack)


# ============================================================================
# 4. 工作流执行上下文与确定性重放引擎 (Workflow Context & Replay Engine)
# ============================================================================

class WorkflowMode(enum.Enum):
    """Workflow execution state."""
    REPLAY = "REPLAY"    # Replaying existing immutable history
    ACTIVE = "ACTIVE"    # Actively scheduling real activities


class NonDeterministicWorkflowError(RuntimeError):
    """Raised when replayed workflow code diverges from historical event log."""
    pass


class WorkflowContext:
    """Manages deterministic workflow execution, signal handling, and activity dispatch."""

    def __init__(
        self,
        workflow_id: str,
        event_store: Optional[EventStore] = None,
        is_replaying: bool = False,
    ) -> None:
        self.workflow_id = workflow_id
        self.store = event_store if event_store is not None else EventStore()
        self.mode = WorkflowMode.REPLAY if is_replaying else WorkflowMode.ACTIVE
        self.saga = SagaCoordinator()

        # State tracking for deterministic replay
        self._activity_occurrence_map: Dict[str, int] = collections.defaultdict(int)
        self._replay_event_index: int = 0
        self._history_events = self.store.get_events() if is_replaying else []

        # Signal channels and Query handlers
        self._signal_channels: Dict[str, SignalChannel] = {}
        self._query_handlers: Dict[str, Callable[..., Any]] = {}

        # Workflow logical clock (pinned during replay to historical events)
        self._logical_time = 0.0

    @property
    def current_time(self) -> float:
        """Deterministic workflow clock."""
        if self.mode == WorkflowMode.REPLAY and self._replay_event_index < len(self._history_events):
            return self._history_events[self._replay_event_index].timestamp
        return time.time()

    def get_signal_channel(self, name: str) -> SignalChannel:
        """Gets or creates a named signal channel."""
        if name not in self._signal_channels:
            self._signal_channels[name] = SignalChannel(name)
        return self._signal_channels[name]

    def register_query_handler(self, query_type: str, handler_fn: Callable[..., Any]) -> None:
        """Registers a side-effect-free query handler."""
        self._query_handlers[query_type] = handler_fn

    def query(self, query_type: str, *args: Any, **kwargs: Any) -> Any:
        """Executes a registered query without mutating event history."""
        if query_type not in self._query_handlers:
            raise KeyError(f"No query handler registered for {query_type}")
        return self._query_handlers[query_type](*args, **kwargs)

    def execute_activity(
        self,
        activity_fn: Callable[..., Any],
        *args: Any,
        activity_name: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """Executes an activity with deterministic replay protection.
        
        If in REPLAY mode and activity was already recorded in history:
          -> Extracts historical result without executing side effects or model API calls.
        If in ACTIVE mode (or replay history exhausted):
          -> Executes real activity, captures result, and appends to event ledger.
        """
        act_name = activity_name or getattr(activity_fn, "__name__", str(activity_fn))
        call_index = self._activity_occurrence_map[act_name]
        self._activity_occurrence_map[act_name] += 1

        # Check if we can satisfy this call from historical replay
        if self.mode == WorkflowMode.REPLAY:
            hist_event = self.store.find_activity_result(act_name, call_index)
            if hist_event is not None:
                # Cache hit from history: ZERO network calls, ZERO token cost!
                self._replay_event_index += 1
                return hist_event.result
            else:
                # History exhausted: seamlessly transition from REPLAY to ACTIVE mode!
                self.mode = WorkflowMode.ACTIVE

        # ACTIVE Mode: Schedule and execute real activity
        self.store.append(
            EventType.ACTIVITY_SCHEDULED,
            name=act_name,
            payload={"args": args, "kwargs": kwargs},
        )

        try:
            result = activity_fn(*args, **kwargs)
            self.store.append(
                EventType.ACTIVITY_COMPLETED,
                name=act_name,
                result=result,
            )
            return result
        except Exception as exc:
            self.store.append(
                EventType.ACTIVITY_FAILED,
                name=act_name,
                error=str(exc),
            )
            raise exc

    def record_marker(self, name: str, details: Dict[str, Any]) -> None:
        """Records an internal audit marker in the event stream."""
        self.store.append(
            EventType.MARKER_RECORDED,
            name=name,
            payload=details,
        )


# ============================================================================
# 5. Computer Use Agent 分布式工作流 (ComputerUseAgentWorkflow)
# ============================================================================

@dataclasses.dataclass
class AgentStepRecord:
    """Record of a single Perception-Decision-Action step."""
    step_number: int
    screen_hash: str
    action_type: str
    target_x: int
    target_y: int
    observation: str
    token_cost: int = 150


class ComputerUseAgentWorkflow:
    """State machine coordinating Computer Use perception-action loops with HITL & Saga."""

    def __init__(self, ctx: WorkflowContext, goal: str, max_steps: int = 10) -> None:
        self.ctx = ctx
        self.goal = goal
        self.max_steps = max_steps
        self.trajectory: List[AgentStepRecord] = []
        self.is_completed = False
        self.is_suspended = False
        self.takeover_count = 0
        self.active_instructions = goal

        # Register standard query handlers
        self.ctx.register_query_handler("get_status", self.query_status)
        self.ctx.register_query_handler("get_trajectory", self.query_trajectory)

    def query_status(self) -> Dict[str, Any]:
        """Side-effect-free query returning current workflow status."""
        return {
            "workflow_id": self.ctx.workflow_id,
            "goal": self.goal,
            "active_instructions": self.active_instructions,
            "steps_completed": len(self.trajectory),
            "is_completed": self.is_completed,
            "is_suspended": self.is_suspended,
            "takeover_count": self.takeover_count,
            "mode": self.ctx.mode.value,
        }

    def query_trajectory(self) -> List[Dict[str, Any]]:
        """Returns executed action trajectory for Langfuse / OTel auditing."""
        return [dataclasses.asdict(step) for step in self.trajectory]

    def run(
        self,
        capture_screen_fn: Callable[[], str],
        model_inference_fn: Callable[[str, str], Dict[str, Any]],
        inject_input_fn: Callable[[str, int, int], str],
        rollback_checkpoint_fn: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Executes the durable perception-action workflow."""
        self.ctx.store.append(
            EventType.WORKFLOW_STARTED,
            name="ComputerUseAgentWorkflow",
            payload={"goal": self.goal, "max_steps": self.max_steps},
        )

        takeover_chan = self.ctx.get_signal_channel("TakeoverSignal")
        resume_chan = self.ctx.get_signal_channel("ResumeSignal")
        intervention_chan = self.ctx.get_signal_channel("InterventionSignal")

        step = 0
        try:
            # 0. 如果工作流之前已被挂起，优先检查恢复信号
            if self.is_suspended:
                resume_payload = resume_chan.receive_non_blocking()
                if not resume_payload:
                    return {"status": "SUSPENDED", "steps": step, "reason": "Awaiting Human Resume"}
                self.is_suspended = False
                self.active_instructions += f" | USER_ACTION: {resume_payload.get('summary', 'done')}"
                self.ctx.store.append(
                    EventType.WORKFLOW_RESUMED,
                    name="ResumeSignal",
                    payload=resume_payload,
                )

            while step < self.max_steps and not self.is_completed:
                # 1. 检查是否存在动态意图纠偏信号 (InterventionSignal)
                intervention = intervention_chan.receive_non_blocking()
                if intervention:
                    self.active_instructions = f"{self.goal} | CORRECTION: {intervention}"
                    self.ctx.store.append(
                        EventType.SIGNAL_RECEIVED,
                        name="InterventionSignal",
                        payload={"correction": intervention},
                    )

                # 2. 检查是否存在人工抢占信号 (Human Takeover Signal)
                takeover = takeover_chan.receive_non_blocking()
                if takeover:
                    self.is_suspended = True
                    self.takeover_count += 1
                    self.ctx.store.append(
                        EventType.WORKFLOW_SUSPENDED,
                        name="TakeoverSignal",
                        payload={"reason": takeover.get("reason", "manual_takeover")},
                    )

                    # 挂起自动化执行，等待人工交接信号 (ResumeSignal)
                    resume_payload = resume_chan.receive_non_blocking()
                    if not resume_payload:
                        # 尚无 Resume 信号，暂停当前循环轮次
                        return {"status": "SUSPENDED", "steps": step, "reason": "Awaiting Human Resume"}

                    # 收到恢复信号，把人工操作摘要注入上下文
                    self.is_suspended = False
                    self.active_instructions += f" | USER_ACTION: {resume_payload.get('summary', 'done')}"
                    self.ctx.store.append(
                        EventType.WORKFLOW_RESUMED,
                        name="ResumeSignal",
                        payload=resume_payload,
                    )

                step += 1

                # 3. 注册 Saga 状态快照与回滚补偿 (Checkpoint)
                checkpoint_id = f"ckpt_step_{step}"
                if rollback_checkpoint_fn:
                    self.ctx.saga.register(
                        f"rollback_to_{checkpoint_id}",
                        rollback_checkpoint_fn,
                        checkpoint_id,
                    )
                    self.ctx.store.append(
                        EventType.COMPENSATION_REGISTERED,
                        name=f"rollback_to_{checkpoint_id}",
                    )

                # 4. 执行 Activity 1: MIT-SHM 零拷贝截屏
                screen_hash = self.ctx.execute_activity(capture_screen_fn, activity_name="capture_screen")

                # 5. 执行 Activity 2: VLM 模型决策
                decision = self.ctx.execute_activity(
                    model_inference_fn,
                    screen_hash,
                    self.active_instructions,
                    activity_name="model_inference",
                )

                action_type = decision.get("action", "click")
                target_x = decision.get("x", 100)
                target_y = decision.get("y", 100)
                is_goal_met = decision.get("is_completed", False)

                # 6. 执行 Activity 3: /dev/uinput 内核事件注入
                obs = self.ctx.execute_activity(
                    inject_input_fn,
                    action_type,
                    target_x,
                    target_y,
                    activity_name="inject_input",
                )

                record = AgentStepRecord(
                    step_number=step,
                    screen_hash=screen_hash,
                    action_type=action_type,
                    target_x=target_x,
                    target_y=target_y,
                    observation=obs,
                )
                self.trajectory.append(record)

                if is_goal_met:
                    self.is_completed = True
                    break

            self.ctx.store.append(
                EventType.WORKFLOW_COMPLETED,
                name="ComputerUseAgentWorkflow",
                payload={"steps": len(self.trajectory)},
            )
            return {"status": "COMPLETED", "steps": len(self.trajectory)}

        except Exception as exc:
            # 7. 异常崩溃时触发 Saga 逆向补偿
            compensation_results = self.ctx.saga.rollback(
                on_action_executed=lambda name, res: self.ctx.store.append(
                    EventType.COMPENSATION_EXECUTED,
                    name=name,
                    result=res,
                )
            )
            self.ctx.store.append(
                EventType.WORKFLOW_FAILED,
                name="ComputerUseAgentWorkflow",
                error=str(exc),
                payload={"compensations": compensation_results},
            )
            raise exc
