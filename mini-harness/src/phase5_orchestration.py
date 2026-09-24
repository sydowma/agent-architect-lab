"""Phase 5: Multi-Agent Orchestration Patterns (DAG, GroupChat, Magentic-One).

Implements the three foundational multi-agent orchestration topologies:
1. Static DAG Workflow (DAGWorkflow, DAGTaskNode, topological execution, cycle detection).
2. Dynamic GroupChat (GroupChat, SpeakerSelector, RoundRobinSelector, AIDrivenSelector).
3. Magentic-One Planning Engine (MagenticOrchestrator, PlanLedger, Outer-Inner Loop, Dynamic Replanning).
"""

from __future__ import annotations

import abc
import collections
import copy
import sys
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

# Import from Topic 1 micro-kernel
from phase5_pico_kernel import (
    AgentContext,
    AgentMessage,
    MultiAgentBus,
    PicoAgent,
    Role,
)


# ============================================================================
# 0. 异常体系 (Exceptions)
# ============================================================================

class OrchestrationError(Exception):
    """Base exception for multi-agent orchestration failures."""


class WorkflowCycleError(OrchestrationError):
    """Raised when a DAG workflow definition contains circular dependencies."""


class WorkflowExecutionError(OrchestrationError):
    """Raised when DAG execution is stalled or a step fails."""


class GroupChatConvergenceError(OrchestrationError):
    """Raised when a group chat exceeds max turns without converging."""


class ReplanningLimitExceededError(OrchestrationError):
    """Raised when Magentic-One fails to converge within max_replans limit."""


# ============================================================================
# 1. 静态有向无环图工作流 (Static DAG Workflow)
# ============================================================================

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class DAGTaskNode:
    """A deterministic task step in a DAG pipeline."""
    task_id: str
    assigned_agent: str
    instruction: str
    depends_on: Set[str] = field(default_factory=set)
    status: TaskStatus = TaskStatus.PENDING
    output: Optional[str] = None

    def add_dependency(self, upstream_task_id: str) -> None:
        self.depends_on.add(upstream_task_id)


class DAGWorkflow:
    """Directed Acyclic Graph orchestrator with cycle detection and topological dispatch."""

    def __init__(self, name: str = "dag_pipeline"):
        self.name = name
        self.nodes: Dict[str, DAGTaskNode] = {}

    def add_node(self, node: DAGTaskNode) -> None:
        self.nodes[node.task_id] = node

    def detect_cycles(self) -> None:
        """Kahn's algorithm / DFS cycle detection."""
        in_degree = {tid: 0 for tid in self.nodes}
        adj = collections.defaultdict(list)

        for tid, node in self.nodes.items():
            for dep in node.depends_on:
                if dep not in self.nodes:
                    raise WorkflowExecutionError(f"Task '{tid}' depends on non-existent task '{dep}'")
                adj[dep].append(tid)
                in_degree[tid] += 1

        queue = collections.deque([tid for tid, deg in in_degree.items() if deg == 0])
        visited_count = 0

        while queue:
            curr = queue.popleft()
            visited_count += 1
            for neighbor in adj[curr]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited_count < len(self.nodes):
            raise WorkflowCycleError(f"Circular dependency detected in DAG workflow '{self.name}'.")

    def execute(self, bus: MultiAgentBus) -> Dict[str, str]:
        """Executes nodes in topological order, aggregating upstream outputs for each step."""
        self.detect_cycles()
        results: Dict[str, str] = {}

        while True:
            # Find executable nodes: PENDING with all dependencies COMPLETED
            executable = []
            for tid, node in self.nodes.items():
                if node.status == TaskStatus.PENDING:
                    deps_satisfied = all(
                        self.nodes[dep].status == TaskStatus.COMPLETED for dep in node.depends_on
                    )
                    if deps_satisfied:
                        executable.append(node)

            if not executable:
                # Check if all completed
                all_done = all(n.status == TaskStatus.COMPLETED for n in self.nodes.values())
                if all_done:
                    break
                # If not all done and nothing executable, we are stalled
                stalled = [n.task_id for n in self.nodes.values() if n.status != TaskStatus.COMPLETED]
                raise WorkflowExecutionError(f"Workflow stalled! Unable to resolve remaining tasks: {stalled}")

            for node in executable:
                node.status = TaskStatus.RUNNING

                # Aggregate upstream outputs
                upstream_context = ""
                if node.depends_on:
                    ctx_parts = [f"[Output of {dep}]: {self.nodes[dep].output}" for dep in sorted(node.depends_on)]
                    upstream_context = f"Upstream Dependencies:\n" + "\n".join(ctx_parts) + "\n\n"

                composed_prompt = f"{upstream_context}Your Task: {node.instruction}"
                response = bus.send_p2p(sender="DAG_Workflow", recipient=node.assigned_agent, content=composed_prompt)

                node.output = response.content
                node.status = TaskStatus.COMPLETED
                results[node.task_id] = response.content

        return results


# ============================================================================
# 2. 动态圆桌协作会议 (Dynamic GroupChat)
# ============================================================================

class SpeakerSelector(abc.ABC):
    """Strategy interface for choosing the next speaker in a GroupChat."""

    @abc.abstractmethod
    def select_next_speaker(self, history: List[AgentMessage], participants: List[str]) -> str:
        raise NotImplementedError


class RoundRobinSelector(SpeakerSelector):
    """Fair sequential round-robin speaker selection."""

    def __init__(self, participants: List[str]):
        self.participants = participants
        self._cursor = 0

    def select_next_speaker(self, history: List[AgentMessage], participants: List[str]) -> str:
        speaker = self.participants[self._cursor % len(self.participants)]
        self._cursor += 1
        return speaker


class AIDrivenSelector(SpeakerSelector):
    """AI Moderator selects the next speaker based on context keywords and task demands."""

    def __init__(self, manager_agent: PicoAgent):
        self.manager = manager_agent

    def select_next_speaker(self, history: List[AgentMessage], participants: List[str]) -> str:
        if not history:
            return participants[0]

        last_speaker = history[-1].sender
        candidates = [p for p in participants if p != last_speaker] or list(participants)
        last_msg = history[-1].content.lower()

        # Deterministic keyword routing heuristics (can be backed by real LLM in production)
        if any(kw in last_msg for kw in ["schema", "database", "sql", "table"]):
            for p in candidates:
                if "db" in p or "data" in p:
                    return p
        if any(kw in last_msg for kw in ["security", "auth", "token", "leak", "hack"]):
            for p in candidates:
                if "security" in p or "reviewer" in p:
                    return p
        if any(kw in last_msg for kw in ["code", "implement", "bug", "syntax", "patch"]):
            for p in candidates:
                if "coder" in p or "engineer" in p:
                    return p

        # Default fallback: pick someone other than the last speaker
        return candidates[0]


class GroupChat:
    """Dynamic roundtable collaboration among multiple agents with consensus detection."""

    def __init__(
        self,
        agents: List[PicoAgent],
        selector: SpeakerSelector,
        max_turns: int = 10,
        termination_keywords: Optional[List[str]] = None,
    ):
        self.agents = {a.name: a for a in agents}
        self.participant_names = list(self.agents.keys())
        self.selector = selector
        self.max_turns = max_turns
        self.termination_keywords = termination_keywords or [
            "[CONSENSUS]", "APPROVED", "TASK_DONE", "CONVERGED"
        ]
        self.messages: List[AgentMessage] = []

    def run(self, initial_topic: str) -> List[AgentMessage]:
        """Runs the discussion until consensus is reached or max_turns is exceeded."""
        init_msg = AgentMessage(
            message_id="chat_init",
            role=Role.USER,
            sender="Moderator",
            recipient="*",
            content=initial_topic,
        )
        self.messages.append(init_msg)

        for turn in range(1, self.max_turns + 1):
            speaker_name = self.selector.select_next_speaker(self.messages, self.participant_names)
            speaker_agent = self.agents[speaker_name]

            # Compose chat context for speaker
            recent_discussion = "\n".join(f"{m.sender}: {m.content}" for m in self.messages[-5:])
            prompt = f"[Roundtable Discussion Context]\n{recent_discussion}\n\nYour response as {speaker_name}:"

            ctx = AgentContext(session_id="group_chat", turn_index=turn)
            incoming = AgentMessage(
                message_id=f"turn_{turn}",
                role=Role.USER,
                sender="GroupChat",
                recipient=speaker_name,
                content=prompt,
            )

            response = speaker_agent.generate(ctx, incoming)
            self.messages.append(response)

            # Check termination
            if any(kw in response.content for kw in self.termination_keywords):
                break

        return self.messages


# ============================================================================
# 3. Magentic-One 规划驱动与动态重规划 (Magentic-One Paradigm)
# ============================================================================

@dataclass
class PlanStep:
    """Individual tactical step in the Magentic-One task ledger."""
    step_id: int
    title: str
    assignee: str
    instruction: str
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[str] = None
    failure_reason: Optional[str] = None


@dataclass
class PlanLedger:
    """Global strategic task board tracked across Outer Loop iterations."""
    objective: str
    steps: List[PlanStep]
    version: int = 1
    replan_count: int = 0


class MagenticOrchestrator:
    """Microsoft Magentic-One inspired two-tier Outer-Inner Loop orchestrator."""

    def __init__(
        self,
        bus: MultiAgentBus,
        max_replans: int = 2,
    ):
        self.bus = bus
        self.max_replans = max_replans

    def run_plan(
        self,
        objective: str,
        initial_steps: List[PlanStep],
        step_validator: Optional[Callable[[PlanStep, str], bool]] = None,
    ) -> PlanLedger:
        """Executes Outer-Inner loop with dynamic replanning upon subtask failure."""
        ledger = PlanLedger(objective=objective, steps=copy.deepcopy(initial_steps))

        step_idx = 0
        while step_idx < len(ledger.steps):
            step = ledger.steps[step_idx]
            step.status = TaskStatus.RUNNING

            # Inner Loop: dispatch tactical task to specialist agent
            prompt = f"[Objective]: {objective}\n[Step {step.step_id}: {step.title}]\nInstruction: {step.instruction}"
            resp = self.bus.send_p2p(sender="Magentic_Orchestrator", recipient=step.assignee, content=prompt)

            # Verification of step result
            step_succeeded = True
            if "[ERROR]" in resp.content or "failed" in resp.content.lower():
                step_succeeded = False
            if step_validator and not step_validator(step, resp.content):
                step_succeeded = False

            if step_succeeded:
                step.status = TaskStatus.COMPLETED
                step.result = resp.content
                step_idx += 1
            else:
                step.status = TaskStatus.FAILED
                step.failure_reason = resp.content

                # Outer Loop: Trigger Dynamic Replanning
                if ledger.replan_count >= self.max_replans:
                    raise ReplanningLimitExceededError(
                        f"Step {step.step_id} failed and max replans ({self.max_replans}) exceeded."
                    )

                ledger.replan_count += 1
                ledger.version += 1
                self._replan(ledger, step_idx)

        return ledger

    def _replan(self, ledger: PlanLedger, failed_step_idx: int) -> None:
        """Heuristic dynamic replanner: amends failed step with fallback approach."""
        failed_step = ledger.steps[failed_step_idx]
        remedy_step = PlanStep(
            step_id=failed_step.step_id,
            title=f"{failed_step.title} (Fallback Route)",
            assignee=failed_step.assignee,
            instruction=f"[REPLANNED]: Previous attempt failed with '{failed_step.failure_reason}'. Use fallback strategy.",
            status=TaskStatus.PENDING,
        )
        # Replace failed step with remedy step in the ledger
        ledger.steps[failed_step_idx] = remedy_step
