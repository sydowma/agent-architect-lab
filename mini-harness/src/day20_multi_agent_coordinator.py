"""Day 20: Multi-Agent Coordination, Independent Verification & Team Landing Engine.

Faithfully reproduces Claude Code's architectural principles from Harness Books Ch 7 & 8:
1. Multi-Agent Partitioning of Uncertainty (Research, Synthesis, Implementation, Verification).
2. Cache-Safe Forking (CacheSafeParams preserving Prefix KV Cache hit).
3. Default-Isolate Mutable State (createSubagentContext: read_file_state cloned, setAppState noop).
4. The Law of Synthesis: Always Synthesize (Coordinator digests findings into concrete coordinates; no forwarding).
5. Skeptical Independent Verification (Implementation worker != Verification worker; physical test proof).
6. Subagent Lifecycle Closure & No-Orphan Guarantee (parent.abort => child.abort).
7. Risk-Tiered Approval & Team Boundary Governance (Read/Write/Irreversible & subcommand cap).
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ============================================================================
# 1. Cache-Safe 参数与状态隔离 (CacheSafeParams & SubagentContext)
# ============================================================================

@dataclass
class CacheSafeParams:
    """Parameters that must be identical between parent and forked subagent.
    
    Preserves prefix prompt cache hit (src/utils/forkedAgent.ts).
    """
    system_prompt: str
    user_context: str
    system_context: str
    tool_use_context: str
    fork_context_messages: List[Dict[str, Any]]
    thinking_config_hash: str = "default_thinking_v1"

    def compute_cache_key(self) -> str:
        """Compute stable hash representing the prefix cache anchor."""
        msg_repr = json.dumps(self.fork_context_messages, sort_keys=True)
        raw = f"{self.system_prompt}|{self.user_context}|{self.system_context}|{self.tool_use_context}|{self.thinking_config_hash}|{msg_repr}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def is_cache_compatible(self, other: CacheSafeParams) -> bool:
        return self.compute_cache_key() == other.compute_cache_key()


@dataclass
class SubagentContext:
    """Isolated runtime context for a forked subagent (src/utils/createSubagentContext.ts)."""
    agent_id: str
    agent_type: str
    parent_agent_id: str
    read_file_state: Set[str] = field(default_factory=set)
    is_aborted: bool = False
    tokens_used: int = 0
    shared_set_app_state: bool = False  # Default False: mutable state is isolated!


# ============================================================================
# 2. 生命周期钩子与审计追踪 (LifecycleHookManager)
# ============================================================================

@dataclass
class SubagentLifecycleRecord:
    agent_id: str
    agent_type: str
    start_time: float
    stop_time: Optional[float] = None
    exit_code: Optional[int] = None
    transcript_path: Optional[str] = None
    stderr_feedback: Optional[str] = None


class LifecycleHookManager:
    """Governs SubagentStart and SubagentStop events (src/utils/hooks/hooksConfigManager.ts)."""

    def __init__(self):
        self.active_agents: Dict[str, SubagentLifecycleRecord] = {}
        self.history: List[SubagentLifecycleRecord] = []

    def fire_subagent_start(self, agent_id: str, agent_type: str):
        record = SubagentLifecycleRecord(
            agent_id=agent_id,
            agent_type=agent_type,
            start_time=time.time(),
        )
        self.active_agents[agent_id] = record

    def fire_subagent_stop(
        self,
        agent_id: str,
        exit_code: int,
        transcript_path: str,
        stderr: str = "",
    ) -> Optional[str]:
        """Fire SubagentStop. If exit_code == 2, returns stderr feedback to reinject."""
        if agent_id not in self.active_agents:
            raise KeyError(f"Cannot stop untracked subagent: {agent_id}")

        record = self.active_agents.pop(agent_id)
        record.stop_time = time.time()
        record.exit_code = exit_code
        record.transcript_path = transcript_path
        record.stderr_feedback = stderr if exit_code == 2 else None

        self.history.append(record)

        # exit code 2 in Claude Code allows stderr feedback reinjection
        if exit_code == 2:
            return stderr
        return None

    def assert_no_orphans_in_flight(self):
        """Invariant: All started subagents must have fired stop hook."""
        if self.active_agents:
            orphan_ids = list(self.active_agents.keys())
            raise AssertionError(f"Lifecycle Violation: Active subagents still in-flight: {orphan_ids}")


# ============================================================================
# 3. 角色分工与工作者 (WorkerRole & ForkedWorkerAgent)
# ============================================================================

class WorkerRole(Enum):
    RESEARCH = "research"
    IMPLEMENTATION = "implementation"
    VERIFICATION = "verification"


class ForkedWorkerAgent:
    """Isolated worker agent executing specialized subtasks."""

    def __init__(
        self,
        agent_id: str,
        role: WorkerRole,
        context: SubagentContext,
        cache_params: CacheSafeParams,
        hook_mgr: LifecycleHookManager,
    ):
        self.agent_id = agent_id
        self.role = role
        self.context = context
        self.cache_params = cache_params
        self.hook_mgr = hook_mgr

    def execute(self, task_prompt: str, runner_func: Callable[[str, SubagentContext], Any]) -> Any:
        if self.context.is_aborted:
            raise RuntimeError(f"Cannot execute on aborted worker: {self.agent_id}")

        self.hook_mgr.fire_subagent_start(self.agent_id, self.role.value)
        try:
            result = runner_func(task_prompt, self.context)
            self.hook_mgr.fire_subagent_stop(
                self.agent_id, exit_code=0, transcript_path=f"/tmp/transcripts/{self.agent_id}.jsonl"
            )
            return result
        except Exception as e:
            self.hook_mgr.fire_subagent_stop(
                self.agent_id, exit_code=1, transcript_path=f"/tmp/transcripts/{self.agent_id}.jsonl", stderr=str(e)
            )
            raise e


# ============================================================================
# 4. 协调者代理与综合律 (CoordinatorAgent & The Law of Synthesis)
# ============================================================================

class InvariantViolationError(Exception):
    """Raised when an architectural invariant is breached."""
    pass


class CoordinatorAgent:
    """Oversees Research, Synthesis, Implementation & Independent Verification (src/coordinator/coordinatorMode.ts)."""

    def __init__(self, parent_id: str, base_cache_params: CacheSafeParams):
        self.parent_id = parent_id
        self.base_cache_params = base_cache_params
        self.hook_mgr = LifecycleHookManager()
        self.parent_read_file_state: Set[str] = set()
        self.active_children: Dict[str, SubagentContext] = {}
        self.last_implementation_worker_id: Optional[str] = None

    def fork_worker(
        self,
        agent_id: str,
        role: WorkerRole,
        worker_cache_params: Optional[CacheSafeParams] = None,
        share_app_state: bool = False,
    ) -> ForkedWorkerAgent:
        """Fork an isolated worker verifying cache safety."""
        params = worker_cache_params or self.base_cache_params

        # Invariant: child.CacheSafeParams == parent.CacheSafeParams
        if not self.base_cache_params.is_cache_compatible(params):
            raise InvariantViolationError("Cache-Safe Violation: Forked worker altered cache-critical prefix params!")

        # Default-Isolate mutable state
        child_context = SubagentContext(
            agent_id=agent_id,
            agent_type=role.value,
            parent_agent_id=self.parent_id,
            read_file_state=copy.deepcopy(self.parent_read_file_state),
            shared_set_app_state=share_app_state,
        )
        self.active_children[agent_id] = child_context

        return ForkedWorkerAgent(
            agent_id=agent_id,
            role=role,
            context=child_context,
            cache_params=params,
            hook_mgr=self.hook_mgr,
        )

    def synthesize(self, raw_findings: str, target_goal: str) -> Dict[str, Any]:
        """Coordinator synthesizes findings into concrete, actionable coordinates (The Law of Synthesis).
        
        Enforces 'Always Synthesize':
        - Rejects raw forward messages ('based on findings...', 'do as researcher suggested').
        - Must output explicit coordinates: target_file, target_location, specific_change.
        """
        # Detection of lazy forwarding
        lazy_forward_patterns = [
            r"based on your findings",
            r"based on the research findings",
            r"do whatever the researcher found",
            r"as investigated above",
        ]
        for pat in lazy_forward_patterns:
            if re.search(pat, raw_findings, re.IGNORECASE):
                raise InvariantViolationError(
                    "Synthesis Violation: Coordinator attempted lazy forwarding! Must synthesize concrete coordinates."
                )

        # Synthesize into structured execution plan
        synthesized_spec = {
            "goal": target_goal,
            "target_file": "mini-harness/src/core.py",
            "target_symbol": "ThreadSafeCache.acquire",
            "concrete_action": "Wrap internal lock release in finally block to prevent deadlock on exception.",
            "status": "SYNTHESIZED",
        }
        return synthesized_spec

    def dispatch_implementation(self, worker: ForkedWorkerAgent, spec: Dict[str, Any], runner: Callable) -> Any:
        """Dispatch implementation task and register worker ID."""
        self.last_implementation_worker_id = worker.agent_id
        return worker.execute(json.dumps(spec), runner)

    def dispatch_verification(
        self,
        worker: ForkedWorkerAgent,
        verification_spec: Dict[str, Any],
        test_runner: Callable[[str, SubagentContext], bool],
    ) -> bool:
        """Dispatch verification ensuring strict role separation.
        
        Invariant: verification_worker != implementation_worker.
        """
        if worker.agent_id == self.last_implementation_worker_id:
            raise InvariantViolationError(
                f"Role Separation Violation: Worker {worker.agent_id} implemented the code and cannot self-verify! "
                "Verification must be performed by an independent skeptical worker."
            )

        if worker.role != WorkerRole.VERIFICATION:
            raise InvariantViolationError(f"Worker role must be VERIFICATION, got {worker.role}")

        # Execute physical test proof
        return worker.execute(json.dumps(verification_spec), test_runner)

    def abort_all_children(self):
        """Propagate parent abort signal to all active children (No-Orphan Invariant)."""
        for child_id, child_ctx in self.active_children.items():
            child_ctx.is_aborted = True
        self.active_children.clear()


# ============================================================================
# 5. 后果分级审批与团队边界治理 (RiskTieredApprovalManager)
# ============================================================================

class RiskTier(Enum):
    READ = "read"
    WRITE = "write"
    IRREVERSIBLE = "irreversible"


class ApprovalDecision(Enum):
    ALLOW = "allow"
    ASK = "ask"
    OPERATOR_ASK = "operator_ask"
    DENY = "deny"


class RiskTieredApprovalManager:
    """Governs tool approvals by consequence and risk tier rather than tool name (Chapter 8)."""

    SUBCOMMAND_CAP = 3  # Max allowed subcommands in compound Bash

    def evaluate_action(
        self,
        action_name: str,
        risk_tier: RiskTier,
        command_str: Optional[str] = None,
    ) -> ApprovalDecision:
        # Check compound bash command cap
        if command_str:
            subcommands = [cmd.strip() for cmd in re.split(r"[;&|]+", command_str) if cmd.strip()]
            if len(subcommands) > self.SUBCOMMAND_CAP:
                return ApprovalDecision.DENY

        # Decision according to Risk Tier
        if risk_tier == RiskTier.READ:
            return ApprovalDecision.ALLOW
        elif risk_tier == RiskTier.WRITE:
            return ApprovalDecision.ASK
        elif risk_tier == RiskTier.IRREVERSIBLE:
            # Irreversible actions (force push, rm -rf, dropping tables) can NEVER be auto-allowed
            return ApprovalDecision.OPERATOR_ASK

        return ApprovalDecision.DENY
