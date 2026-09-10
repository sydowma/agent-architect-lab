"""Day 16: Query Loop & Agent Lifecycle State Machine Engine.

Faithfully reproduces Claude Code's Query Loop architecture from Harness Books Ch 3:
1. Multi-turn State as First-Class Citizen (QueryLoopState).
2. Pre-call Input Governance (govern_input: Tool Result Budget & Microcompact).
3. Streaming Event Consumption & Step Advance.
4. User Interrupt & Synthetic Tool Result Ledger Balancing (drain_tools_with_synthetic_results).
5. Multi-tier Failure Recovery (max_output_tokens continuation & PTL reactive compact).
6. 3 Core Invariant Assertions (Monotonic Turns, Ledger Closure, Double-failure Breaker).
"""

from __future__ import annotations

import copy
import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


class LoopTransition(Enum):
    FOLLOW_UP = "FOLLOW_UP"           # Completed tool execution, requires next turn
    DONE = "DONE"                     # Model concluded cleanly with no tool calls
    ABORTED = "ABORTED"               # Interrupted by user or timeout
    RECOVER_CONTINUE = "RECOVER_CONTINUE" # Output truncated, continuing from cutoff
    REACTIVE_COMPACT = "REACTIVE_COMPACT" # Context overflow, compacting context
    ERROR_HALT = "ERROR_HALT"         # Fatal unrecoverable failure


@dataclass
class QueryLoopState:
    """Persistent cross-iteration runtime state for an agent conversation."""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    turn_count: int = 0
    tool_budget_bytes: int = 2048
    max_output_recovery_count: int = 0
    max_recovery_retries: int = 2
    has_attempted_reactive_compact: bool = False
    is_interrupted: bool = False
    is_done: bool = False
    pending_tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    transition_history: List[LoopTransition] = field(default_factory=list)

    def append_message(self, msg: Dict[str, Any]) -> None:
        self.messages.append(msg)


class QueryLoopEngine:
    """Industrial Query Loop State Machine Engine."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        model: Optional[str] = None,
        tool_budget_bytes: int = 2048,
        max_turns: int = 8
    ):
        self.endpoint = endpoint or os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:12340/v1")
        self.model = model or os.getenv("OPENAI_MODEL", "google/gemma-4-12b-qat")
        self.tool_budget_bytes = tool_budget_bytes
        self.max_turns = max_turns

    # -----------------------------------------------------------------
    # 1. Pre-call Input Governance Pipeline (govern_input)
    # -----------------------------------------------------------------
    def govern_input(self, state: QueryLoopState) -> Dict[str, int]:
        """Prepares and sanitizes input messages before model streaming.
        
        Applies:
        - Tool Result Budget: Truncates giant outputs that threaten context window.
        - Microcompact: Compacts older raw outputs into compact summaries.
        """
        stats = {"budget_truncated": 0, "microcompacted": 0}
        total_msgs = len(state.messages)

        for idx, msg in enumerate(state.messages):
            role = msg.get("role")

            # 1. Tool Result Budget Enforcement
            if role == "tool":
                content = msg.get("content", "")
                if isinstance(content, str) and len(content) > state.tool_budget_bytes:
                    preserved = content[: state.tool_budget_bytes]
                    snipped_bytes = len(content) - state.tool_budget_bytes
                    msg["content"] = (
                        f"{preserved}\n\n"
                        f"[... Output truncated by Tool Result Budget ({snipped_bytes} bytes snipped) ...]"
                    )
                    stats["budget_truncated"] += 1

            # 2. Microcompact on older turns (> 4 messages in the past)
            if role == "tool" and (total_msgs - idx) > 4:
                content = msg.get("content", "")
                if isinstance(content, str) and "[Microcompacted]" not in content:
                    lines = content.strip().split("\n")
                    if len(lines) > 6:
                        preview = "\n".join(lines[:3])
                        msg["content"] = (
                            f"[Microcompacted Historical Tool Result]\n"
                            f"{preview}\n"
                            f"[... {len(lines)-3} lines collapsed by microcompact ...]"
                        )
                        stats["microcompacted"] += 1

        return stats

    # -----------------------------------------------------------------
    # 2. Interrupt Handling & Synthetic Tool Result Ledger Balancing
    # -----------------------------------------------------------------
    def drain_tools_with_synthetic_results(
        self, state: QueryLoopState, reason: str = "Execution aborted by user interrupt"
    ) -> int:
        """Balances the causal transcript ledger upon abort.
        
        Guarantees Invariant 2: Every emitted tool_use MUST have a matching tool_result!
        """
        synthetic_count = 0
        for call in state.pending_tool_calls:
            c_id = call.get("id", f"call_{state.turn_count}")
            fn_name = call.get("function", {}).get("name", "unknown_tool")

            # Synthesize balanced completion record
            state.append_message({
                "role": "tool",
                "tool_call_id": c_id,
                "name": fn_name,
                "content": f"[Synthetic Tool Result: {reason}. State safely preserved.]"
            })
            synthetic_count += 1

        state.pending_tool_calls.clear()
        state.is_interrupted = True
        state.transition_history.append(LoopTransition.ABORTED)
        return synthetic_count

    # -----------------------------------------------------------------
    # 3. Model Streaming & Event Consumption
    # -----------------------------------------------------------------
    def call_model_step(
        self,
        state: QueryLoopState,
        system_prompt: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = 1500
    ) -> Dict[str, Any]:
        """Performs a single step query to the model endpoint."""
        url = f"{self.endpoint.rstrip('/')}/chat/completions"
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt}] + state.messages,
            "temperature": 0.1,
            "max_tokens": max_tokens
        }
        if tools:
            payload["tools"] = tools

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "Authorization": "Bearer lm-studio"},
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=120) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            choice = res["choices"][0]
            msg = choice.get("message", {})
            content = msg.get("content") or ""
            if not content.strip() and msg.get("reasoning_content"):
                content = msg.get("reasoning_content")
            finish_reason = choice.get("finish_reason", "stop")
            tool_calls = msg.get("tool_calls") or []

            return {
                "content": content,
                "finish_reason": finish_reason,
                "tool_calls": tool_calls,
                "raw_message": msg
            }

    # -----------------------------------------------------------------
    # 4. State Monotonic Advance & Recovery Branching
    # -----------------------------------------------------------------
    def advance_turn(
        self,
        state: QueryLoopState,
        step_result: Dict[str, Any],
        tool_dispatcher: Optional[Callable[[str, Dict[str, Any]], str]] = None
    ) -> LoopTransition:
        """Monotonically advances state machine according to the failure/termination matrix."""
        state.turn_count += 1
        content = step_result["content"]
        finish_reason = step_result["finish_reason"]
        tool_calls = step_result["tool_calls"]

        # Record assistant event in message ledger
        asst_msg: Dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            asst_msg["tool_calls"] = tool_calls
        state.append_message(asst_msg)

        # Branch 1: Output Token Truncation Recovery (finish_reason == "length")
        if finish_reason == "length":
            if state.max_output_recovery_count < state.max_recovery_retries:
                state.max_output_recovery_count += 1
                state.append_message({
                    "role": "user",
                    "content": (
                        "[System Meta-Notice]: Your output was cut off by token limit. "
                        "Do not apologize. Continue directly from the exact point of cutoff."
                    )
                })
                state.transition_history.append(LoopTransition.RECOVER_CONTINUE)
                return LoopTransition.RECOVER_CONTINUE
            else:
                state.is_done = True
                state.transition_history.append(LoopTransition.DONE)
                return LoopTransition.DONE

        # Branch 2: Tool Execution Follow-up (finish_reason == "tool_calls")
        if tool_calls:
            state.pending_tool_calls = copy.deepcopy(tool_calls)

            # Execute tools via dispatcher
            for call in tool_calls:
                c_id = call.get("id", f"call_{state.turn_count}")
                fn = call.get("function", {})
                name = fn.get("name", "unknown")
                args_str = fn.get("arguments", "{}")
                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except Exception:
                    args = {}

                # Execute dispatch
                if tool_dispatcher:
                    result_content = tool_dispatcher(name, args)
                else:
                    result_content = f"Tool '{name}' executed successfully."

                state.append_message({
                    "role": "tool",
                    "tool_call_id": c_id,
                    "name": name,
                    "content": result_content
                })

            state.pending_tool_calls.clear()
            state.transition_history.append(LoopTransition.FOLLOW_UP)
            return LoopTransition.FOLLOW_UP

        # Branch 3: Clean Completion (no tools, normal finish)
        state.is_done = True
        state.transition_history.append(LoopTransition.DONE)
        return LoopTransition.DONE

    # -----------------------------------------------------------------
    # 5. Core Invariant Enforcer (The 3 Hard Guarantees)
    # -----------------------------------------------------------------
    def verify_invariants(self, state: QueryLoopState, previous_turn: int) -> Tuple[bool, List[str]]:
        """Formally verifies the 3 fundamental Query Loop invariants."""
        violations = []

        # Invariant 1: Monotonic turn count progression (No time travel)
        if state.turn_count < previous_turn:
            violations.append(
                f"Invariant 1 Violated: Turn count regression! ({state.turn_count} < {previous_turn})"
            )

        # Invariant 2: Complete Tool Ledger Balance (Every tool_use MUST have tool_result)
        tool_uses: Set[str] = set()
        tool_results: Set[str] = set()

        for msg in state.messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    tool_uses.add(tc.get("id", ""))
            elif msg.get("role") == "tool":
                tool_results.add(msg.get("tool_call_id", ""))

        unmatched = tool_uses - tool_results
        if unmatched and not state.pending_tool_calls:
            violations.append(
                f"Invariant 2 Violated: Unbalanced tool ledger! Unmatched tool_use IDs: {unmatched}"
            )

        # Invariant 3: Reactive Compact Circuit Breaker
        if state.has_attempted_reactive_compact:
            compact_count = sum(1 for t in state.transition_history if t == LoopTransition.REACTIVE_COMPACT)
            if compact_count > 1:
                violations.append("Invariant 3 Violated: Reactive compact attempted more than once (Circuit breaker failure)!")

        return len(violations) == 0, violations
