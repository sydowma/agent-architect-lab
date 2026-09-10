"""Day 19: Error Recovery, Resilience & Process Governance Engine.

Faithfully reproduces Claude Code's architectural principles from Harness Books Ch 6:
1. Errors as First-Class Citizens on the Main Path.
2. Withheld Recoverable Errors pattern (prompt_too_long, media_size, max_output_tokens).
3. Stratified PTL Recovery Ladder (Collapse drain -> Reactive compact -> Surface with skip_stop_hooks).
4. Breaking the Death Spiral (skipStopHooks when PTL cannot be recovered).
5. Compaction Escape Hatch (truncateHeadForPTLRetry).
6. MOT Continuation over Recap (Cap boost -> Meta continuation message).
7. Abort Semantic Closure (Synthetic tool results for dangling tool calls, Abort != Compact success).
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


class RecoverableErrorType(Enum):
    PROMPT_TOO_LONG = "prompt_too_long"
    MAX_OUTPUT_TOKENS = "max_output_tokens"
    MEDIA_SIZE_EXCEEDED = "media_size"


class RecoveryAction(Enum):
    DRAIN_COLLAPSE = "drain_collapse"
    REACTIVE_COMPACT = "reactive_compact"
    SURFACE_ERROR = "surface_error"
    BOOST_TOKEN_CAP = "boost_token_cap"
    CONTINUE_WITHOUT_RECAP = "continue_without_recap"


@dataclass
class WithheldError:
    """Represents an error withheld from immediate user surfacing for recovery attempts."""
    error_type: RecoverableErrorType
    raw_message: str
    turn_id: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class RecoveryDecision:
    """The outcome of error recovery routing."""
    action: RecoveryAction
    reason: str
    skip_stop_hooks: bool = False
    new_token_cap: Optional[int] = None
    meta_message: Optional[Dict[str, Any]] = None


@dataclass
class RecoveryContextState:
    """Tracks state variables relevant to resilience and loop prevention across turns."""
    turn_id: str = "turn_0"
    staged_collapse_count: int = 0
    has_attempted_reactive_compact: bool = False
    current_output_cap: int = 4096
    max_output_cap_limit: int = 16384
    mot_recovery_count: int = 0
    consecutive_compact_failures: int = 0
    max_consecutive_compact_failures: int = 3
    circuit_broken: bool = False


class CompactionEscapeHatch:
    """Handles emergency recovery when the compaction process itself encounters PTL."""

    @staticmethod
    def truncate_head_for_ptl_retry(
        messages: List[Dict[str, Any]], drop_round_count: int = 1
    ) -> List[Dict[str, Any]]:
        """Safely drop oldest full conversational rounds from the head.
        
        Preserves the system prompt (if present at index 0) and the most recent context.
        """
        if not messages:
            return []

        has_system = messages[0].get("role") == "system"
        sys_msg = [messages[0]] if has_system else []
        pool = messages[1:] if has_system else messages[:]

        # A round consists of a user message and assistant/tool responses
        rounds: List[List[Dict[str, Any]]] = []
        current_round: List[Dict[str, Any]] = []

        for msg in pool:
            if msg.get("role") == "user" and current_round:
                rounds.append(current_round)
                current_round = [msg]
            else:
                current_round.append(msg)
        if current_round:
            rounds.append(current_round)

        if len(rounds) <= drop_round_count:
            # Drop earliest messages but keep at least the latest round
            remaining_rounds = rounds[-1:] if rounds else []
        else:
            remaining_rounds = rounds[drop_round_count:]

        flattened = [msg for r in remaining_rounds for msg in r]
        return sys_msg + flattened


class ResilientQueryRecoveryEngine:
    """Core runtime engine governing error withholding, stratified healing & anti-deadlock guards."""

    RECOVERABLE_ERROR_STRINGS = {
        "prompt_too_long": RecoverableErrorType.PROMPT_TOO_LONG,
        "context_length_exceeded": RecoverableErrorType.PROMPT_TOO_LONG,
        "max_output_tokens": RecoverableErrorType.MAX_OUTPUT_TOKENS,
        "output_length_exceeded": RecoverableErrorType.MAX_OUTPUT_TOKENS,
        "media_size": RecoverableErrorType.MEDIA_SIZE_EXCEEDED,
        "image_payload_too_large": RecoverableErrorType.MEDIA_SIZE_EXCEEDED,
    }

    CONTINUATION_INSTRUCTION = (
        "Continue directly. Do NOT apologize, do NOT recap. "
        "If interrupted mid-sentence, continue seamlessly from the exact break point. "
        "Break remaining work into smaller steps."
    )

    def identify_error(self, error_str: str) -> Optional[RecoverableErrorType]:
        """Check if an error belongs to the withheld recoverable whitelist."""
        lower_err = error_str.lower()
        for key, err_type in self.RECOVERABLE_ERROR_STRINGS.items():
            if key in lower_err:
                return err_type
        return None

    def route_error_recovery(
        self,
        raw_error: str,
        state: RecoveryContextState,
        messages: List[Dict[str, Any]],
    ) -> RecoveryDecision:
        """Stratified recovery decision tree following Claude Code Ch 6."""
        err_type = self.identify_error(raw_error)

        # 1. Non-recoverable error -> Surface immediately, normal hooks
        if not err_type:
            return RecoveryDecision(
                action=RecoveryAction.SURFACE_ERROR,
                reason=f"Non-recoverable fatal error: {raw_error}",
                skip_stop_hooks=False,
            )

        # 2. PROMPT_TOO_LONG Recovery Ladder
        if err_type == RecoverableErrorType.PROMPT_TOO_LONG:
            # Layer 1: Context collapse drain (cheapest, conservative)
            if state.staged_collapse_count > 0:
                state.staged_collapse_count -= 1
                return RecoveryDecision(
                    action=RecoveryAction.DRAIN_COLLAPSE,
                    reason="Drain staged context collapse to recover space without full rewrite.",
                    skip_stop_hooks=False,
                )

            # Layer 2: Reactive compact (full rewrite, once per cycle)
            if not state.has_attempted_reactive_compact:
                state.has_attempted_reactive_compact = True
                return RecoveryDecision(
                    action=RecoveryAction.REACTIVE_COMPACT,
                    reason="Execute reactive compaction and controlled reboot.",
                    skip_stop_hooks=False,
                )

            # Layer 3: Surface error with STOP HOOK BYPASS!
            # Invariant: Breaking the death spiral (error -> hook blocking -> retry -> error)
            return RecoveryDecision(
                action=RecoveryAction.SURFACE_ERROR,
                reason="Context limit unrecoverable. Exceeded after reactive compact.",
                skip_stop_hooks=True,  # Mandatory to avoid death spiral!
            )

        # 3. MAX_OUTPUT_TOKENS Recovery Ladder
        if err_type == RecoverableErrorType.MAX_OUTPUT_TOKENS:
            # Layer 1: Boost cap to max without meta noise
            if state.current_output_cap < state.max_output_cap_limit:
                new_cap = state.max_output_cap_limit
                state.current_output_cap = new_cap
                return RecoveryDecision(
                    action=RecoveryAction.BOOST_TOKEN_CAP,
                    reason=f"Boost output token cap from {state.current_output_cap} to {new_cap}.",
                    new_token_cap=new_cap,
                    skip_stop_hooks=False,
                )

            # Layer 2: Continuation instruction (No apology, no recap)
            state.mot_recovery_count += 1
            meta_msg = {
                "role": "user",
                "content": self.CONTINUATION_INSTRUCTION,
                "type": "mot_continuation_meta",
            }
            return RecoveryDecision(
                action=RecoveryAction.CONTINUE_WITHOUT_RECAP,
                reason="Cap already at MAX limit. Instruct model to seamlessly continue without recap.",
                meta_message=meta_msg,
                skip_stop_hooks=False,
            )

        # 4. MEDIA_SIZE_EXCEEDED
        if err_type == RecoverableErrorType.MEDIA_SIZE_EXCEEDED:
            return RecoveryDecision(
                action=RecoveryAction.REACTIVE_COMPACT,
                reason="Strip heavy media blobs and execute compact.",
                skip_stop_hooks=False,
            )

        return RecoveryDecision(
            action=RecoveryAction.SURFACE_ERROR,
            reason="Exhausted all recovery strategies.",
            skip_stop_hooks=False,
        )

    def handle_user_abort(
        self,
        pending_tool_calls: List[Dict[str, Any]],
        is_compacting: bool = False,
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """Ensures ledger consistency upon Esc / Abort.
        
        1. Materializes synthetic tool results for all dangling calls.
        2. Invariant: User abort during compact MUST NEVER be counted as success.
        Returns: (synthetic_results, is_compact_success_allowed)
        """
        synthetic_results = []
        for call in pending_tool_calls:
            tool_id = call.get("id", "unknown_tool")
            tool_name = call.get("name", "unknown")
            synthetic_results.append({
                "role": "tool",
                "tool_use_id": tool_id,
                "content": f"[User aborted execution (Esc) on {tool_name}]",
                "is_error": True,
            })

        # Compact aborted by user must NEVER be registered as summary success!
        compact_success_allowed = not is_compacting
        return synthetic_results, compact_success_allowed

    def verify_resilience_invariants(self, state: RecoveryContextState) -> List[str]:
        """Validate runtime invariants defined in Claude Code Ch 6."""
        violations = []
        if state.consecutive_compact_failures >= state.max_consecutive_compact_failures and not state.circuit_broken:
            violations.append("Invariant Breached: Consecutive failures exceeded limit without circuit breaking!")
        return violations
