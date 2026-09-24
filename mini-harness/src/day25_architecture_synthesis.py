"""Day 25: Architecture Synthesis, Convergences & Divergences.

Faithfully realizes the convergence invariants and divergence models from Harness Books
Book 2 Epilogue and the Pi Bluebook (Earendil / Mario Zechner):

1. PrefixCacheDisciplineValidator:
   - Physical check of static prefix vs dynamic message stream.
   - Strictly enforces monotonic append-only history and static prefix hashing.
   - Intercepts mid-stream mutations, retroactive edits, and timestamp-in-prefix drift.
   - Tracks cache hit ratio and quantifies TTFT latency penalties.
2. SessionPortabilityEngine:
   - Pi-style Append-Only JSONL Event Stream (DAG branching, zero-magic file portability).
   - Codex-style Typed Rollout Snapshot (Thread, rollout ID, contextual fragments).
   - Claude Code-style Living Working Memory (Session memory specification & compaction).
   - Full bidirectional translation and lossless replayability.
3. SyntheticLedgerInvariants:
   - Dual-entry accounting for Tool Call (Debit) vs Tool Result (Credit).
   - Enforces ledger closure across abnormal interruptions (Ctrl+C, timeout, OOM).
   - Generates synthetic tool results to guarantee zero protocol deserialization failures.
4. ArchitectureDecisionAdvisor:
   - Evaluates workload profiles (untrusted code, multi-tenant SaaS, IDE embed, team scale).
   - Maps to optimal architectural archetypes (Pi Micro-kernel vs Claude Code vs Codex).
   - Prescribes concrete defense knobs, sandbox tier, and storage strategies.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ============================================================================
# 0. 异常体系 (Exceptions)
# ============================================================================

class SynthesisError(Exception):
    """Base class for Day 25 architecture synthesis violations."""


class PrefixCacheViolation(SynthesisError):
    """Raised when prompt assembly breaks prefix caching invariants."""


class ProtocolLedgerUnbalancedError(SynthesisError):
    """Raised when an assistant tool call frame is left dangling without a tool result."""


class SessionPortabilityError(SynthesisError):
    """Raised when session cross-format conversion or replay fails."""


class InvalidWorkloadProfileError(SynthesisError):
    """Raised when architecture workload parameters are inconsistent."""


# ============================================================================
# 1. 提示缓存分界纪律校验器 (PrefixCacheDisciplineValidator)
# ============================================================================

class CacheInvalidationReason(str, Enum):
    NONE = "none"
    SYSTEM_PREFIX_MUTATED = "system_prefix_mutated"
    TIMESTAMP_INJECTED_IN_PREFIX = "timestamp_injected_in_prefix"
    RETROACTIVE_HISTORY_EDIT = "retroactive_history_edit"
    TOOL_SCHEMA_DRIFT = "tool_schema_drift"
    COMPACTION_EPOCH_RESET = "compaction_epoch_reset"


@dataclass
class PromptAssemblySnapshot:
    """Represents the complete prompt buffer submitted to the LLM in a single turn."""
    turn_index: int
    static_prefix: str          # System prompt, core rules, base instructions
    tools_declaration: str       # Serialized tools schema (must be deterministic)
    history_stream: List[str]    # Monotonically appended messages
    timestamp: float = field(default_factory=time.time)

    def compute_prefix_hash(self) -> str:
        """Hash the static portion that GPU KV cache must keep pinned."""
        content = f"{self.static_prefix}||{self.tools_declaration}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def compute_total_tokens_estimate(self) -> int:
        """Rough token estimator: 4 chars per token."""
        total_len = len(self.static_prefix) + len(self.tools_declaration) + sum(len(m) for m in self.history_stream)
        return max(1, total_len // 4)


class PrefixCacheDisciplineValidator:
    """Enforces GPU prefix caching discipline across consecutive dialogue turns."""

    def __init__(self, base_ttft_ms: float = 25.0, cold_penalty_factor: float = 8.0) -> None:
        self.base_ttft_ms = base_ttft_ms
        self.cold_penalty_factor = cold_penalty_factor
        self.baseline_prefix_hash: Optional[str] = None
        self.current_epoch: int = 0
        self.turns_history: List[PromptAssemblySnapshot] = []
        self.total_cache_hits: int = 0
        self.total_cache_misses: int = 0

    def register_turn(self, snapshot: PromptAssemblySnapshot) -> Tuple[bool, CacheInvalidationReason, float]:
        """Validates prefix caching invariants for the given turn.
        
        Returns:
            (is_cache_hit, reason_if_invalidated, estimated_ttft_ms)
        """
        # Rule 1: Detect volatile runtime tokens (like dynamic timestamps) mistakenly injected into static prefix
        volatile_patterns = ["2026-", "timestamp:", "clock:", "time_now="]
        for pat in volatile_patterns:
            if pat.lower() in snapshot.static_prefix.lower():
                self.total_cache_misses += 1
                return False, CacheInvalidationReason.TIMESTAMP_INJECTED_IN_PREFIX, self.base_ttft_ms * self.cold_penalty_factor

        current_prefix_hash = snapshot.compute_prefix_hash()

        # Initial turn of an epoch: defines baseline cache prefix
        if self.baseline_prefix_hash is None:
            self.baseline_prefix_hash = current_prefix_hash
            self.turns_history.append(snapshot)
            self.total_cache_hits += 1
            return True, CacheInvalidationReason.NONE, self.base_ttft_ms

        # Rule 2: Static prefix must not mutate
        if current_prefix_hash != self.baseline_prefix_hash:
            self.total_cache_misses += 1
            return False, CacheInvalidationReason.SYSTEM_PREFIX_MUTATED, self.base_ttft_ms * self.cold_penalty_factor

        # Rule 3: Dynamic history must be monotonic append-only
        if self.turns_history:
            prev_snapshot = self.turns_history[-1]
            prev_len = len(prev_snapshot.history_stream)
            curr_len = len(snapshot.history_stream)

            if curr_len < prev_len:
                self.total_cache_misses += 1
                return False, CacheInvalidationReason.RETROACTIVE_HISTORY_EDIT, self.base_ttft_ms * self.cold_penalty_factor

            # Check that previous prefix of history was not modified in place
            for i in range(prev_len):
                if snapshot.history_stream[i] != prev_snapshot.history_stream[i]:
                    self.total_cache_misses += 1
                    return False, CacheInvalidationReason.RETROACTIVE_HISTORY_EDIT, self.base_ttft_ms * self.cold_penalty_factor

        # All invariants hold! Cache hit confirmed
        self.turns_history.append(snapshot)
        self.total_cache_hits += 1
        return True, CacheInvalidationReason.NONE, self.base_ttft_ms

    def trigger_compaction_reset(self, new_summary: str, tools_schema: str) -> None:
        """Explicitly seals previous epoch and establishes a new prefix anchor after compaction."""
        self.current_epoch += 1
        new_static_prefix = f"[Compaction Epoch {self.current_epoch}]\nSummary: {new_summary}"
        self.baseline_prefix_hash = hashlib.sha256(f"{new_static_prefix}||{tools_schema}".encode("utf-8")).hexdigest()
        self.turns_history.clear()


# ============================================================================
# 2. 会话可移植性引擎 (SessionPortabilityEngine)
# ============================================================================

@dataclass
class PiEvent:
    """An append-only event in Pi's minimal JSONL stream."""
    event_id: str
    parent_id: Optional[str]
    turn_index: int
    kind: str  # "user_prompt", "assistant_thought", "tool_call", "tool_result", "system_prompt"
    payload: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps({
            "event_id": self.event_id,
            "parent_id": self.parent_id,
            "turn_index": self.turn_index,
            "kind": self.kind,
            "payload": self.payload,
            "timestamp": self.timestamp
        }, ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str) -> PiEvent:
        data = json.loads(json_str)
        return cls(
            event_id=data["event_id"],
            parent_id=data.get("parent_id"),
            turn_index=data["turn_index"],
            kind=data["kind"],
            payload=data["payload"],
            timestamp=data.get("timestamp", time.time())
        )


@dataclass
class PiSessionStream:
    """Pi-style zero-magic append-only session container supporting DAG branching."""
    session_id: str
    events: List[PiEvent] = field(default_factory=list)

    def append_event(self, kind: str, payload: Dict[str, Any], turn_index: int) -> PiEvent:
        parent_id = self.events[-1].event_id if self.events else None
        event_id = f"evt_{len(self.events) + 1}_{hashlib.md5(f'{kind}_{time.time()}'.encode()).hexdigest()[:6]}"
        evt = PiEvent(
            event_id=event_id,
            parent_id=parent_id,
            turn_index=turn_index,
            kind=kind,
            payload=payload
        )
        self.events.append(evt)
        return evt

    def serialize_to_jsonl(self) -> str:
        """Export session to standard append-only JSONL format."""
        return "\n".join(e.to_json() for e in self.events)

    @classmethod
    def deserialize_from_jsonl(cls, session_id: str, jsonl_content: str) -> PiSessionStream:
        events = []
        for line in jsonl_content.strip().split("\n"):
            line = line.strip()
            if line:
                events.append(PiEvent.from_json(line))
        return cls(session_id=session_id, events=events)

    def branch_from(self, event_id: str, new_session_id: str) -> PiSessionStream:
        """Branches a new session tree from any historical event without mutating the original."""
        idx = -1
        for i, e in enumerate(self.events):
            if e.event_id == event_id:
                idx = i
                break
        if idx == -1:
            raise SessionPortabilityError(f"Target branch event_id '{event_id}' not found.")
        branched_events = copy.deepcopy(self.events[:idx + 1])
        return PiSessionStream(session_id=new_session_id, events=branched_events)


@dataclass
class CodexRolloutSnapshot:
    """Codex-style typed rollout snapshot."""
    thread_id: str
    rollout_id: str
    active_turn: int
    context_fragments: List[Dict[str, Any]]
    execution_policy: str
    turns: List[Dict[str, Any]]
    status: str = "completed"


@dataclass
class ClaudeWorkingMemory:
    """Claude Code-style living working memory."""
    objective: str
    completed_milestones: List[str]
    pending_tasks: List[str]
    key_facts: Dict[str, str]
    recent_turns: List[Dict[str, Any]]


class SessionPortabilityEngine:
    """Bidirectional converter across Pi, Codex, and Claude Code representations."""

    @staticmethod
    def pi_to_codex(stream: PiSessionStream, policy: str = "workspace_strict") -> CodexRolloutSnapshot:
        """Converts Pi JSONL event stream to Codex typed rollout snapshot."""
        turns: List[Dict[str, Any]] = []
        fragments: List[Dict[str, Any]] = []
        current_turn: Dict[str, Any] = {}

        for evt in stream.events:
            if evt.kind == "system_prompt":
                fragments.append({
                    "fragment_id": evt.event_id,
                    "title": "SystemConstitution",
                    "content": evt.payload.get("text", "")
                })
            elif evt.kind == "user_prompt":
                if current_turn:
                    turns.append(current_turn)
                current_turn = {
                    "turn_index": evt.turn_index,
                    "user_input": evt.payload.get("text", ""),
                    "actions": []
                }
            elif evt.kind in ("tool_call", "tool_result"):
                if not current_turn:
                    current_turn = {"turn_index": evt.turn_index, "user_input": "", "actions": []}
                current_turn["actions"].append({
                    "type": evt.kind,
                    "payload": evt.payload
                })

        if current_turn:
            turns.append(current_turn)

        return CodexRolloutSnapshot(
            thread_id=stream.session_id,
            rollout_id=f"rollout_{stream.session_id}",
            active_turn=len(turns),
            context_fragments=fragments,
            execution_policy=policy,
            turns=turns,
            status="completed"
        )

    @staticmethod
    def codex_to_pi(snapshot: CodexRolloutSnapshot) -> PiSessionStream:
        """Converts Codex typed rollout snapshot to Pi append-only JSONL stream."""
        stream = PiSessionStream(session_id=snapshot.thread_id)

        # 1. System fragments
        for frag in snapshot.context_fragments:
            stream.append_event("system_prompt", {"text": frag.get("content", "")}, turn_index=0)

        # 2. Replay turns
        for turn in snapshot.turns:
            t_idx = turn.get("turn_index", 1)
            stream.append_event("user_prompt", {"text": turn.get("user_input", "")}, turn_index=t_idx)
            for action in turn.get("actions", []):
                stream.append_event(action.get("type", "action"), action.get("payload", {}), turn_index=t_idx)

        return stream

    @staticmethod
    def pi_to_claude_memory(stream: PiSessionStream, objective: str) -> ClaudeWorkingMemory:
        """Synthesizes Pi event stream into Claude Code-style living working memory."""
        milestones = []
        pending = []
        facts = {}
        recent = []

        for evt in stream.events:
            if evt.kind == "tool_result":
                output = str(evt.payload.get("output", ""))
                if "success" in output.lower():
                    milestones.append(f"Tool {evt.payload.get('tool_name', 'tool')} executed successfully")
            elif evt.kind == "user_prompt":
                recent.append({"role": "user", "content": evt.payload.get("text", "")})
            elif evt.kind == "assistant_thought":
                recent.append({"role": "assistant", "content": evt.payload.get("thought", "")})

        if not milestones:
            pending.append("Awaiting first task execution")
        facts["events_count"] = str(len(stream.events))
        facts["session_id"] = stream.session_id

        return ClaudeWorkingMemory(
            objective=objective,
            completed_milestones=milestones,
            pending_tasks=pending,
            key_facts=facts,
            recent_turns=recent[-5:]  # Keep last 5 turns
        )


# ============================================================================
# 3. 循环账本闭合与合成补账 (SyntheticLedgerInvariants)
# ============================================================================

@dataclass
class ToolCallEntry:
    call_id: str
    tool_name: str
    arguments: Dict[str, Any]
    status: str = "PENDING"  # "PENDING" | "COMPLETED" | "SYNTHETIC_CLOSED"
    result_payload: Optional[str] = None
    is_synthetic: bool = False


class SyntheticLedger:
    """Tracks dual-entry tool calls & results, enforcing zero-unbalanced-protocol invariant."""

    def __init__(self) -> None:
        self.ledger: Dict[str, ToolCallEntry] = {}

    def debit_call(self, call_id: str, tool_name: str, arguments: Dict[str, Any]) -> ToolCallEntry:
        """Records an assistant tool call frame."""
        entry = ToolCallEntry(call_id=call_id, tool_name=tool_name, arguments=arguments)
        self.ledger[call_id] = entry
        return entry

    def credit_result(self, call_id: str, result_output: str) -> ToolCallEntry:
        """Credits a real tool execution output."""
        if call_id not in self.ledger:
            raise ProtocolLedgerUnbalancedError(f"Cannot credit non-existent tool call '{call_id}'.")
        entry = self.ledger[call_id]
        entry.status = "COMPLETED"
        entry.result_payload = result_output
        return entry

    def abort_and_synthesize(self, reason: str = "User interruption (Ctrl+C)") -> List[ToolCallEntry]:
        """Synthesizes tool results for any pending uncredited calls to prevent protocol crash."""
        synthetic_closed = []
        for call_id, entry in self.ledger.items():
            if entry.status == "PENDING":
                entry.status = "SYNTHETIC_CLOSED"
                entry.result_payload = f"[SYNTHETIC_RESULT: Interrupted - {reason}]"
                entry.is_synthetic = True
                synthetic_closed.append(entry)
        return synthetic_closed

    def assert_ledger_balanced(self) -> None:
        """Asserts that zero tool calls remain unclosed."""
        dangling = [cid for cid, e in self.ledger.items() if e.status == "PENDING"]
        if dangling:
            raise ProtocolLedgerUnbalancedError(
                f"Protocol invariant violated: {len(dangling)} tool calls left dangling without results: {dangling}"
            )

    @property
    def is_balanced(self) -> bool:
        return all(e.status != "PENDING" for e in self.ledger.values())


# ============================================================================
# 4. 架构决策分析与选型求解器 (ArchitectureDecisionAdvisor)
# ============================================================================

class ArchitecturalArchetype(str, Enum):
    PI_MICRO_KERNEL = "pi_micro_kernel"
    CLAUDE_CODE_RUNTIME_RESILIENT = "claude_code_runtime_resilient"
    CODEX_TYPED_GOVERNANCE = "codex_typed_governance"


class SandboxIsolationTier(str, Enum):
    NONE = "none"
    LOCAL_DOCKER_NO_NET = "local_docker_no_network"
    MICRO_VM_FIRECRACKER = "micro_vm_firecracker"


class SessionStorageType(str, Enum):
    APPEND_ONLY_JSONL = "append_only_jsonl"
    TYPED_ROLLOUT_DB = "typed_rollout_db"
    LIVING_WORKING_MEMORY = "living_working_memory"


@dataclass
class WorkloadProfile:
    """Characteristics of a target production deployment."""
    untrusted_code_execution: bool
    multi_tenant_saas: bool
    strict_audit_compliance: bool
    custom_ide_embed: bool
    latency_critical_ttft: bool
    team_size: int = 1


@dataclass
class ArchitecturalPrescription:
    """Recommended architectural parameters for production deployment."""
    archetype: ArchitecturalArchetype
    sandbox_tier: SandboxIsolationTier
    session_storage: SessionStorageType
    prefix_caching_enforcement: str
    delegation_style: str
    rationale: str
    key_tradeoffs: List[str]


class ArchitectureDecisionAdvisor:
    """Solves for the optimal harness architecture given enterprise workload parameters."""

    @staticmethod
    def evaluate(profile: WorkloadProfile) -> ArchitecturalPrescription:
        # Case 1: High-risk, Multi-tenant SaaS or Strict Enterprise Audit -> Codex Archetype
        if profile.multi_tenant_saas or profile.strict_audit_compliance:
            sandbox = SandboxIsolationTier.MICRO_VM_FIRECRACKER if profile.multi_tenant_saas else SandboxIsolationTier.LOCAL_DOCKER_NO_NET
            return ArchitecturalPrescription(
                archetype=ArchitecturalArchetype.CODEX_TYPED_GOVERNANCE,
                sandbox_tier=sandbox,
                session_storage=SessionStorageType.TYPED_ROLLOUT_DB,
                prefix_caching_enforcement="STRICT_FRAGMENT_ISOLATION",
                delegation_style="TOOLIZED_DELEGATION_PROTOCOL",
                rationale="Multi-tenant or strict audit compliance mandates typed substrates, explicit sandbox isolation, and formal toolized delegation ledgers.",
                key_tradeoffs=[
                    "Higher system setup complexity",
                    "Overhead of relational/stateful DB rollout persistence",
                    "Requires strict pre-execution policy compilation"
                ]
            )

        # Case 2: Custom IDE Embed or High-throughput Low-latency Minimalist -> Pi Micro-Kernel
        if profile.custom_ide_embed and profile.latency_critical_ttft:
            sandbox = SandboxIsolationTier.LOCAL_DOCKER_NO_NET if profile.untrusted_code_execution else SandboxIsolationTier.NONE
            return ArchitecturalPrescription(
                archetype=ArchitecturalArchetype.PI_MICRO_KERNEL,
                sandbox_tier=sandbox,
                session_storage=SessionStorageType.APPEND_ONLY_JSONL,
                prefix_caching_enforcement="RAW_APPEND_ONLY_MONOTONIC",
                delegation_style="DIRECT_COROUTINE_DISPATCH",
                rationale="Embedded IDE integrations require minimal overhead, transparent zero-magic event loops, and predictable append-only file persistence.",
                key_tradeoffs=[
                    "Fewer batteries included; application must author domain self-healing",
                    "No built-in complex multi-agent orchestrator",
                    "Full history playback required for deep contextual queries"
                ]
            )

        # Case 3: Default Agile Coding Agent (Team development, complex multi-step reasoning) -> Claude Code Archetype
        sandbox = SandboxIsolationTier.LOCAL_DOCKER_NO_NET if profile.untrusted_code_execution else SandboxIsolationTier.NONE
        return ArchitecturalPrescription(
            archetype=ArchitecturalArchetype.CLAUDE_CODE_RUNTIME_RESILIENT,
            sandbox_tier=sandbox,
            session_storage=SessionStorageType.LIVING_WORKING_MEMORY,
            prefix_caching_enforcement="DYNAMIC_STREAM_WITH_MICROCOMPACT",
            delegation_style="SUBAGENT_CONTEXT_ISOLATION",
            rationale="Developer teams require maximum runtime resiliency, in-loop error recovery, living working memory summaries, and rapid adaptation via natural language markdown rules.",
            key_tradeoffs=[
                "High runtime complexity within Query Loop",
                "Working memory updates overwrite historical granular exploration traces",
                "Requires careful tuning of compaction heuristics"
            ]
        )
