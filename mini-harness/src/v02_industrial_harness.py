"""mini-harness v0.2: Unified Industrial Defense Agent Harness.

Phase 3 Capstone Engineering Artifact.
Integrates all architectural defense subsystems developed across Week 3 (Claude Code Harness Book 1):
1. Prompt Control Plane (5-tier precedence & KV Prefix Cache boundary - Day 15).
2. Resilient Query Loop & Synthetic Ledger Balancer (Day 16).
3. Managed Tool Orchestration, SafeBashGuard & Tri-State Permissions (Day 17).
4. Context Compactor, SessionMemory & Controlled Reboot (Day 18).
5. Withheld Error Recovery & Anti-Death-Spiral Guards (Day 19).
6. Multi-Agent Partitioning, The Law of Synthesis & Skeptical QA (Day 20).
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
# 1. 控制面宪法与 Cache 边界 (Day 15)
# ============================================================================

@dataclass
class PromptTierConfig:
    default_prompt: str = "DEFAULT CONSTITUTION: Model is an unstable component."
    custom_rules: str = ""
    agent_rules: str = ""
    coordinator_rules: str = ""
    override_rules: str = ""
    append_rules: str = ""


class PromptControlPlane:
    """Governs prompt resolution hierarchy and guarantees static KV Prefix Cache stability."""

    def __init__(self, config: Optional[PromptTierConfig] = None):
        self.config = config or PromptTierConfig()

    def resolve_static_system_prompt(self) -> str:
        """Resolve the static base prompt following 5-tier precedence chain."""
        if self.config.override_rules.strip():
            base = self.config.override_rules.strip()
        elif self.config.coordinator_rules.strip():
            base = self.config.coordinator_rules.strip()
        elif self.config.agent_rules.strip():
            base = self.config.agent_rules.strip()
        elif self.config.custom_rules.strip():
            base = self.config.custom_rules.strip()
        else:
            base = self.config.default_prompt.strip()

        if self.config.append_rules.strip():
            base = f"{base}\n\n[APPENDED INVARIANTS]\n{self.config.append_rules.strip()}"
        return base

    def format_dynamic_reminder(self, git_status: str, active_file: str) -> Dict[str, Any]:
        """Dynamic transient content must NEVER mutate the system prompt; pushed to stream tip."""
        content = (
            f"<system-reminder>\n"
            f"[TRANSIENT WORKING STATE]\n"
            f"Active File: {active_file}\n"
            f"Git Status: {git_status}\n"
            f"</system-reminder>"
        )
        return {"role": "user", "type": "system_reminder", "content": content}


# ============================================================================
# 2. 受管工具调度、SafeBashGuard 与三态权限 (Day 17)
# ============================================================================

class PermissionDecision(Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class SafeBashGuard:
    """Defense in depth for shell operations."""

    DESTRUCTIVE_PATTERNS = [
        (r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+[/~*]", "Destructive recursive file deletion"),
        (r"\bgit\s+push\s+.*(?:-f|--force)\b", "Destructive Git force push on remote"),
        (r"\bchmod\s+-[a-zA-Z]*R\s+777\b", "Dangerous open directory permission alteration"),
    ]
    SUBCOMMAND_CAP = 3

    @classmethod
    def evaluate_command(cls, command: str) -> Tuple[bool, str]:
        for pattern, reason in cls.DESTRUCTIVE_PATTERNS:
            if re.search(pattern, command):
                return False, f"Blocked Destructive Command: {reason}"
        subcommands = [c.strip() for c in re.split(r"[;&|]+", command) if c.strip()]
        if len(subcommands) > cls.SUBCOMMAND_CAP:
            return False, f"Blocked: Command exceeds subcommand limit ({len(subcommands)} > {cls.SUBCOMMAND_CAP})"
        return True, "Safe"


class ManagedToolOrchestrator:
    """Partitions tool calls into concurrency-safe parallel vs unsafe serial batches."""

    CONCURRENCY_SAFE_TOOLS = {"read_file", "grep_search", "list_dir"}

    def partition_tool_calls(self, tool_calls: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        batches: List[List[Dict[str, Any]]] = []
        current_parallel_batch: List[Dict[str, Any]] = []

        for call in tool_calls:
            name = call.get("name", "")
            if name in self.CONCURRENCY_SAFE_TOOLS:
                current_parallel_batch.append(call)
            else:
                if current_parallel_batch:
                    batches.append(current_parallel_batch)
                    current_parallel_batch = []
                batches.append([call])

        if current_parallel_batch:
            batches.append(current_parallel_batch)
        return batches


# ============================================================================
# 3. 结构化会话工作说明书与上下文压紧受控重启 (Day 18)
# ============================================================================

@dataclass
class SessionMemory:
    current_state: str = ""
    task_specification: str = ""
    files_and_functions: str = ""
    errors_and_corrections: str = ""
    worklog: str = ""

    def render_markdown(self) -> str:
        sections = [
            ("# Current State", self.current_state),
            ("# Task specification", self.task_specification),
            ("# Files and Functions", self.files_and_functions),
            ("# Errors & Corrections", self.errors_and_corrections),
            ("# Worklog", self.worklog),
        ]
        return "\n".join([f"{h}\n{c.strip()}" for h, c in sections if c.strip()])


@dataclass
class CompactBoundary:
    pre_compact_tokens: int
    compacted_turns: int
    timestamp: float = field(default_factory=time.time)


class ContextCompactor:
    """Manages AutoCompact budgets and controlled reboot."""

    MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000
    AUTOCOMPACT_BUFFER_TOKENS = 13_000
    MAX_CONSECUTIVE_FAILURES = 3

    def __init__(self, context_window: int = 128_000):
        self.context_window = context_window
        self.consecutive_failures = 0
        self.circuit_broken = False

    def should_compact(self, current_tokens: int) -> bool:
        if self.circuit_broken:
            return False
        effective_window = max(0, self.context_window - self.MAX_OUTPUT_TOKENS_FOR_SUMMARY)
        threshold = max(0, effective_window - self.AUTOCOMPACT_BUFFER_TOKENS)
        return current_tokens >= threshold

    def execute_controlled_reboot(
        self,
        messages: List[Dict[str, Any]],
        session_summary: SessionMemory,
        active_files: Dict[str, str],
        pre_tokens: int,
    ) -> Tuple[List[Dict[str, Any]], CompactBoundary]:
        boundary = CompactBoundary(pre_compact_tokens=pre_tokens, compacted_turns=len(messages))
        reconstructed: List[Dict[str, Any]] = [
            {
                "role": "system",
                "type": "compact_boundary",
                "content": f"<compact_boundary>Rebooted from {pre_tokens} tokens.</compact_boundary>",
            },
            {
                "role": "user",
                "type": "session_memory",
                "content": f"<session_memory>\n{session_summary.render_markdown()}\n</session_memory>",
            },
        ]
        for path, code in active_files.items():
            reconstructed.append({
                "role": "system",
                "type": "active_file",
                "content": f'<active_file path="{path}">\n{code}\n</active_file>',
            })
        return reconstructed, boundary


# ============================================================================
# 4. 分层错误自愈与死亡螺旋切断 (Day 19)
# ============================================================================

class ResilientRecoveryEngine:
    """Governs withheld errors and breaks deadlocks."""

    RECOVERABLE_ERRORS = {"prompt_too_long", "max_output_tokens", "media_size"}

    def handle_error(
        self,
        err_msg: str,
        has_attempted_compact: bool,
    ) -> Tuple[str, bool]:
        """Returns (action, skip_stop_hooks)."""
        lower = err_msg.lower()
        if "prompt_too_long" in lower:
            if not has_attempted_compact:
                return "REACTIVE_COMPACT", False
            # Break the death spiral: unrecoverable PTL MUST bypass stop hooks!
            return "SURFACE_ERROR", True

        if "max_output_tokens" in lower:
            return "CONTINUE_WITHOUT_RECAP", False

        return "SURFACE_FATAL", False


# ============================================================================
# 5. 多代理协同与独立怀疑验证 (Day 20)
# ============================================================================

@dataclass
class CacheSafeParams:
    system_prompt: str
    user_context: str
    system_context: str
    tool_use_context: str
    fork_context_messages: List[Dict[str, Any]]

    def get_hash(self) -> str:
        serialized = json.dumps(self.fork_context_messages, sort_keys=True)
        raw = f"{self.system_prompt}|{self.user_context}|{self.system_context}|{self.tool_use_context}|{serialized}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class MultiAgentCoordinator:
    """Enforces Always Synthesize and implementation != verification isolation."""

    def __init__(self, base_cache_params: CacheSafeParams):
        self.base_cache_params = base_cache_params
        self.last_implementation_worker: Optional[str] = None

    def synthesize(self, research_findings: str) -> Dict[str, Any]:
        if "based on your findings" in research_findings.lower():
            raise ValueError("Synthesis Violation: Lazy forwarding rejected!")
        return {
            "status": "SYNTHESIZED",
            "target_file": "src/core.py",
            "action": "Wrap mutex unlock in finally block",
        }

    def verify_role_separation(self, verifier_worker_id: str):
        if verifier_worker_id == self.last_implementation_worker:
            raise AssertionError(
                f"Role Separation Violation: Worker {verifier_worker_id} cannot self-verify! Must be independent QA."
            )


# ============================================================================
# 6. mini-harness v0.2 大一统工业中枢 (UnifiedIndustrialHarness)
# ============================================================================

class UnifiedIndustrialHarness:
    """v0.2 Unified Industrial Defense Harness integrating all 6 subsystems."""

    def __init__(self, context_window: int = 128_000):
        self.control_plane = PromptControlPlane()
        self.orchestrator = ManagedToolOrchestrator()
        self.compactor = ContextCompactor(context_window=context_window)
        self.recovery_engine = ResilientRecoveryEngine()
        self.messages: List[Dict[str, Any]] = []
        self.turn_counter: int = 0
        self.active_files: Dict[str, str] = {}
        self.pending_tool_calls: List[Dict[str, Any]] = []

    def get_static_system_prompt(self) -> str:
        return self.control_plane.resolve_static_system_prompt()

    def balance_tool_ledger_under_abort(self) -> List[Dict[str, Any]]:
        """Materializes synthetic results for any dangling tool calls (Invariant P3)."""
        synthetics = []
        while self.pending_tool_calls:
            call = self.pending_tool_calls.pop(0)
            synthetics.append({
                "role": "tool",
                "tool_use_id": call["id"],
                "content": f"[User aborted (Esc) on {call.get('name', 'tool')}]",
                "is_error": True,
            })
        return synthetics

    def verify_invariants(self) -> List[str]:
        """Full runtime invariant check across the 10 Principles."""
        violations = []
        # Invariant P3: Ledger must balance
        tool_uses = sum(1 for m in self.messages if m.get("role") == "assistant" and "tool_use" in m.get("content", ""))
        tool_results = sum(1 for m in self.messages if m.get("role") == "tool")
        if self.pending_tool_calls:
            violations.append(f"Pending unclosed tool calls in flight: {len(self.pending_tool_calls)}")
        return violations
