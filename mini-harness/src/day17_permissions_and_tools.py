"""Day 17: Managed Tool Orchestration, Permissions & HITL Interrupt Engine.

Faithfully reproduces Claude Code's architectural principles from Harness Books Ch 4:
1. Managed Tool Execution vs Raw Invocation.
2. Concurrency-Safe Partitioning (partitionToolCalls & isConcurrencySafe).
3. Context Modifier Sequential Replay.
4. Tri-State Permission Hierarchy (ALLOW, DENY, ASK).
5. Permission Invariants (No auto-escalation, Sticky Denials).
6. SafeBashGuard High-Pressure Shell Security (Subcommand count limits & dangerous regexes).
7. Sibling Error Cancellation & Synthetic Result Materialization.
"""

from __future__ import annotations

import copy
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


class PermissionDecision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class PermissionEvaluation:
    decision: PermissionDecision
    reason: str
    tool_use_id: str
    risk_level: str = "low"  # low, medium, high, critical


@dataclass
class ToolCallRecord:
    id: str
    name: str
    arguments: Dict[str, Any]
    original_index: int


@dataclass
class ToolExecutionBatch:
    is_parallel: bool
    calls: List[ToolCallRecord]


class SafeBashGuard:
    """High-pressure security filter for Shell/Bash operations."""

    DESTRUCTIVE_PATTERNS = [
        (r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+[/~*]", "Destructive recursive file deletion"),
        (r"\bgit\s+push\s+.*(?:-f|--force)\b", "Destructive Git force push on remote"),
        (r"\bchmod\s+-[a-zA-Z]*R\s+777\b", "Dangerous open directory permission alteration"),
        (r"\bmkfs\b", "Destructive filesystem formatting"),
        (r"\bsudo\b|\bsu\s+", "Privilege escalation attempt forbidden in agent harness"),
        (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;", "Fork bomb denial-of-service attack"),
    ]

    def __init__(self, max_subcommands: int = 3):
        self.max_subcommands = max_subcommands

    def inspect_command(self, cmd: str) -> Tuple[bool, Optional[str]]:
        """Inspects shell command for safety violations. Returns (is_safe, violation_reason)."""
        cmd_clean = cmd.strip()

        # 1. Subcommand count bounds check (prevent escaping parser via chaining)
        subcommands = re.split(r"&&|\|\||;|\|", cmd_clean)
        if len(subcommands) > self.max_subcommands:
            return False, f"Subcommand count ({len(subcommands)}) exceeds maximum limit of {self.max_subcommands}."

        # 2. Destructive regex rule checking
        for pattern, desc in self.DESTRUCTIVE_PATTERNS:
            if re.search(pattern, cmd_clean, re.IGNORECASE):
                return False, f"Blocked high-risk shell operation: {desc}"

        return True, None


class PermissionManager:
    """Manages the tri-state permission chain and enforces Sticky Denials."""

    READ_ONLY_TOOLS = {"read_file", "grep_search", "list_dir", "get_system_time", "check_health"}
    MUTATING_TOOLS = {"write_file", "edit_file", "delete_file", "git_commit"}
    HIGH_RISK_TOOLS = {"run_bash", "shell_exec", "terminal_command"}

    def __init__(self, auto_approve_mutations: bool = False):
        self.auto_approve_mutations = auto_approve_mutations
        self.sticky_denials: Set[str] = set()
        self.bash_guard = SafeBashGuard()

    def evaluate(self, tool_record: ToolCallRecord) -> PermissionEvaluation:
        t_id = tool_record.id
        name = tool_record.name
        args = tool_record.arguments

        # Invariant 3 Check: Sticky Deny
        if t_id in self.sticky_denials:
            return PermissionEvaluation(
                decision=PermissionDecision.DENY,
                reason="Sticky Deny: This tool call ID has already been rejected and cannot be retried.",
                tool_use_id=t_id,
                risk_level="high"
            )

        # 1. High-risk Bash inspection
        if name in self.HIGH_RISK_TOOLS:
            cmd = args.get("cmd", args.get("command", ""))
            is_safe, reason = self.bash_guard.inspect_command(cmd)
            if not is_safe:
                self.sticky_denials.add(t_id)
                return PermissionEvaluation(
                    decision=PermissionDecision.DENY,
                    reason=f"Security Policy Denied: {reason}",
                    tool_use_id=t_id,
                    risk_level="critical"
                )
            # Safe bash commands require user approval (ASK)
            return PermissionEvaluation(
                decision=PermissionDecision.ASK,
                reason=f"Shell command execution requires human confirmation: `{cmd}`",
                tool_use_id=t_id,
                risk_level="high"
            )

        # 2. Read-only tools are automatically allowed
        if name in self.READ_ONLY_TOOLS:
            return PermissionEvaluation(
                decision=PermissionDecision.ALLOW,
                reason="Read-only inspection tool is concurrency and safety permitted.",
                tool_use_id=t_id,
                risk_level="low"
            )

        # 3. Mutating tools trigger ASK unless auto-approved
        if name in self.MUTATING_TOOLS:
            if self.auto_approve_mutations:
                return PermissionEvaluation(
                    decision=PermissionDecision.ALLOW,
                    reason="Mutation pre-authorized by policy flag.",
                    tool_use_id=t_id,
                    risk_level="medium"
                )
            return PermissionEvaluation(
                decision=PermissionDecision.ASK,
                reason=f"Modifying workspace file via `{name}` requires confirmation.",
                tool_use_id=t_id,
                risk_level="medium"
            )

        # Fallback unknown tool -> ASK
        return PermissionEvaluation(
            decision=PermissionDecision.ASK,
            reason=f"Unrecognized tool `{name}` requires operator authorization.",
            tool_use_id=t_id,
            risk_level="medium"
        )

    def resolve_ask(self, tool_use_id: str, approved: bool, reason: str = "") -> PermissionEvaluation:
        """Resolves an ASK state through human operator intervention."""
        if approved:
            return PermissionEvaluation(
                decision=PermissionDecision.ALLOW,
                reason=f"Human Approved: {reason}" if reason else "Approved by user in HITL loop.",
                tool_use_id=tool_use_id,
                risk_level="medium"
            )
        else:
            self.sticky_denials.add(tool_use_id)
            return PermissionEvaluation(
                decision=PermissionDecision.DENY,
                reason=f"Human Denied: {reason}" if reason else "Rejected by user in HITL loop.",
                tool_use_id=tool_use_id,
                risk_level="medium"
            )


class ToolOrchestrator:
    """Partitions and coordinates tool execution maintaining causal consistency."""

    def __init__(self, permission_manager: Optional[PermissionManager] = None):
        self.permission_manager = permission_manager or PermissionManager()

    def is_concurrency_safe(self, name: str, args: Dict[str, Any]) -> bool:
        """Determines if a tool call is read-only and safe for parallel batching."""
        return name in PermissionManager.READ_ONLY_TOOLS

    def partition_tool_calls(self, raw_calls: List[Dict[str, Any]]) -> List[ToolExecutionBatch]:
        """Partitions tool calls into parallel-safe batches and serial units."""
        batches: List[ToolExecutionBatch] = []
        current_parallel_batch: List[ToolCallRecord] = []

        for idx, call in enumerate(raw_calls):
            c_id = call.get("id", f"call_{idx}")
            fn = call.get("function", {})
            name = fn.get("name", "unknown")
            args = fn.get("arguments", {})
            if isinstance(args, str):
                import json
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}

            record = ToolCallRecord(id=c_id, name=name, arguments=args, original_index=idx)

            if self.is_concurrency_safe(name, args):
                current_parallel_batch.append(record)
            else:
                # Flush existing parallel batch before serial call
                if current_parallel_batch:
                    batches.append(ToolExecutionBatch(is_parallel=True, calls=current_parallel_batch))
                    current_parallel_batch = []
                # Add unsafe call as isolated serial batch
                batches.append(ToolExecutionBatch(is_parallel=False, calls=[record]))

        # Flush trailing parallel batch
        if current_parallel_batch:
            batches.append(ToolExecutionBatch(is_parallel=True, calls=current_parallel_batch))

        return batches

    def execute_batch(
        self,
        batch: ToolExecutionBatch,
        tool_runner: Callable[[str, Dict[str, Any]], str],
        hitl_callback: Optional[Callable[[PermissionEvaluation], bool]] = None
    ) -> List[Tuple[ToolCallRecord, str]]:
        """Executes a batch (parallel or serial) and preserves sequential context order."""
        results: List[Tuple[ToolCallRecord, str]] = []

        if not batch.is_parallel:
            # Serial execution
            for rec in batch.calls:
                eval_res = self.permission_manager.evaluate(rec)
                if eval_res.decision == PermissionDecision.ASK and hitl_callback:
                    approved = hitl_callback(eval_res)
                    eval_res = self.permission_manager.resolve_ask(rec.id, approved)

                if eval_res.decision == PermissionDecision.DENY:
                    results.append((rec, f"[Permission Denied]: {eval_res.reason}"))
                else:
                    try:
                        out = tool_runner(rec.name, rec.arguments)
                        results.append((rec, out))
                    except Exception as e:
                        results.append((rec, f"[ToolExecutionError]: {str(e)}"))
            return results

        # Parallel execution with Sibling Error Abort
        # Even though threads finish out-of-order, results are buffered and replayed in order!
        buffered_results: Dict[int, str] = {}
        has_sibling_error = threading.Event()
        sibling_error_reason: List[str] = []

        def worker(rec: ToolCallRecord):
            if has_sibling_error.is_set():
                buffered_results[rec.original_index] = "[Synthetic Cancelled]: Cancelled due to sibling tool error."
                return

            eval_res = self.permission_manager.evaluate(rec)
            if eval_res.decision == PermissionDecision.DENY:
                buffered_results[rec.original_index] = f"[Permission Denied]: {eval_res.reason}"
                return

            try:
                out = tool_runner(rec.name, rec.arguments)
                if "[Error]" in out or "[Exception]" in out:
                    has_sibling_error.set()
                    sibling_error_reason.append(f"Sibling `{rec.name}` produced failure output.")
                buffered_results[rec.original_index] = out
            except Exception as e:
                has_sibling_error.set()
                sibling_error_reason.append(str(e))
                buffered_results[rec.original_index] = f"[ToolExecutionError]: {str(e)}"

        with ThreadPoolExecutor(max_workers=min(4, len(batch.calls))) as pool:
            futures = [pool.submit(worker, rec) for rec in batch.calls]
            for f in futures:
                f.result()

        # Replay context modifiers strictly in original index order!
        sorted_calls = sorted(batch.calls, key=lambda c: c.original_index)
        for rec in sorted_calls:
            results.append((rec, buffered_results.get(rec.original_index, "[Unknown Failure]")))

        return results


def verify_permission_invariants(eval_res: PermissionEvaluation, is_retrying_denied: bool = False) -> Tuple[bool, List[str]]:
    """Formal invariant check on permission evaluation results."""
    violations = []

    # Invariant 1: Tri-State Completeness (No boolean laziness)
    if eval_res.decision not in {PermissionDecision.ALLOW, PermissionDecision.DENY, PermissionDecision.ASK}:
        violations.append(f"Invariant 1 Violated: Invalid decision state `{eval_res.decision}`.")

    # Invariant 2: No Auto-Escalation
    # An ASK evaluation must never be treated as ALLOW without explicit approval
    if eval_res.decision == PermissionDecision.ASK and "Auto-Approved" in eval_res.reason:
        violations.append("Invariant 2 Violated: ASK auto-escalated to ALLOW without authorization.")

    # Invariant 3: Sticky Deny
    if is_retrying_denied and eval_res.decision != PermissionDecision.DENY:
        violations.append("Invariant 3 Violated: Deny state was not sticky; previously rejected ID was permitted.")

    return len(violations) == 0, violations
