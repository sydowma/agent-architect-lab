"""mini-harness: Multi-Agent Systems & Blackboard Architecture.

Implements native, zero-dependency multi-agent collaboration patterns:
1. SupervisorMultiAgent: Centralized manager routing to specialized agents with safety clamp and dedicated writer.
2. BlackboardSystem: Decentralized shared-state bus (HEARSAY-II style) with self-assessed bidding,
   mechanical arbitration, and domination decay.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# LLM Host Client (Zero Dependency)
# ---------------------------------------------------------------------------
DEFAULT_ENDPOINT = "http://127.0.0.1:12340/v1"
DEFAULT_MODEL = "qwen/qwen3.8-27b"


def call_llm(
    messages: List[Dict[str, str]],
    model: str = DEFAULT_MODEL,
    endpoint: str = DEFAULT_ENDPOINT,
    temperature: float = 0.4,
    max_tokens: int = 3500,
    timeout: float = 180.0,
) -> str:
    """Invokes OpenAI-compatible local endpoint (LM Studio)."""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{endpoint}/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        msg = res["choices"][0]["message"]
        content = msg.get("content") or ""
        return content.strip()


def parse_json_from_response(raw: str) -> Dict[str, Any]:
    """Robust extractor for JSON blocks from LLM markdown/text output."""
    raw = raw.strip()
    if raw.startswith("```json") and raw.endswith("```"):
        raw = raw[7:-3].strip()
    elif raw.startswith("```") and raw.endswith("```"):
        raw = raw[3:-3].strip()

    match = re.search(r"(\{[\s\S]*\})", raw)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(raw)
    except Exception as e:
        raise ValueError(f"Failed to parse valid JSON from output:\n{raw}\nError: {e}")


# ---------------------------------------------------------------------------
# Common Role Specification
# ---------------------------------------------------------------------------
@dataclass
class AgentRole:
    name: str
    system_prompt: str
    description: str


# ===========================================================================
# Pattern 1: Centralized Supervisor (Star Topology)
# ===========================================================================
@dataclass
class SpecialistOutput:
    role: str
    reasoning: str
    content: str


@dataclass
class SupervisorTraceStep:
    step_index: int
    decision_type: str  # "model_decision" | "safety_clamp"
    selected_agent: str
    reason: str


@dataclass
class SupervisorResult:
    task: str
    specialist_outputs: List[SpecialistOutput]
    final_report: str
    trace: List[SupervisorTraceStep]
    total_llm_calls: int
    elapsed_seconds: float


class SupervisorMultiAgent:
    """Supervisor (Star Topology) Multi-Agent Engine.

    Features:
      - Central Supervisor orchestrator with structured JSON decision schema.
      - State tracking: prevents routing loops via proactive Host Safety Clamping.
      - Dedicated Writer node: prevents recency bias and stylistic distortion.
    """

    def __init__(
        self,
        roles: Dict[str, AgentRole],
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_ENDPOINT,
        max_rounds: int = 5,
        llm_fn: Optional[Callable[..., str]] = None,
    ):
        self.roles = roles
        self.model = model
        self.endpoint = endpoint
        self.max_rounds = max_rounds
        self.llm_fn = llm_fn or call_llm

    def _call(self, messages: List[Dict[str, str]], max_tokens: int = 3500) -> str:
        return self.llm_fn(
            messages=messages,
            model=self.model,
            endpoint=self.endpoint,
            max_tokens=max_tokens,
        )

    def run(self, task: str) -> SupervisorResult:
        start_time = time.time()
        llm_call_count = 0
        trace: List[SupervisorTraceStep] = []
        specialist_outputs: List[SpecialistOutput] = []
        contributed_roles: set[str] = set()

        all_role_names = list(self.roles.keys())
        allowed_next = all_role_names + ["writer", "FINISH"]

        writer_executed = False
        final_report = ""

        step_idx = 0
        while step_idx < self.max_rounds:
            step_idx += 1
            remaining_roles = [r for r in all_role_names if r not in contributed_roles]

            # 1. Prepare Supervisor Context
            history_summary = "\n".join(
                f"- [Specialist: {o.role}] -> {o.content[:150]}..."
                for o in specialist_outputs
            ) or "(No specialists have contributed yet)"

            supervisor_prompt = (
                f"You are the central SUPERVISOR orchestrating a team of specialists to solve a user task.\n\n"
                f"## Available Specialists\n"
                + "\n".join(f"- {r.name}: {r.description}" for r in self.roles.values())
                + f"\n- writer: Synthesizes all specialist findings into a comprehensive final report.\n"
                f"- FINISH: Signals completion (ONLY permitted after writer has executed).\n\n"
                f"## Current Status\n"
                f"Task: {task}\n"
                f"Contributed so far: {list(contributed_roles)}\n"
                f"Remaining uncalled specialists: {remaining_roles}\n"
                f"Writer completed: {writer_executed}\n"
                f"Summary of findings:\n{history_summary}\n\n"
                f"## Instructions\n"
                f"Decide who acts next. Return ONLY a JSON object formatted exactly as:\n"
                f"{{\n"
                f'  "next_agent": "<one of: {", ".join(allowed_next)}>\",\n'
                f'  "reason": "<one sentence explaining the rationale>"\n'
                f"}}"
            )

            # 2. Invoke Supervisor LLM
            llm_call_count += 1
            raw_decision = self._call(
                [
                    {"role": "system", "content": "You are a disciplined engineering supervisor. Output strict JSON only."},
                    {"role": "user", "content": supervisor_prompt},
                ],
                max_tokens=1500,
            )

            try:
                decision = parse_json_from_response(raw_decision)
                next_agent = decision.get("next_agent", "")
                reason = decision.get("reason", "No reason provided")
            except Exception:
                # Fallback on parse failure
                next_agent = remaining_roles[0] if remaining_roles else "writer"
                reason = "JSON parse fallback"

            decision_type = "model_decision"

            # 3. Host Safety Clamp (Enforce zero loops and proper phase transition)
            if next_agent in self.roles:
                if next_agent in contributed_roles:
                    # Looping detected! Clamp to remaining specialist or writer
                    if remaining_roles:
                        original = next_agent
                        next_agent = remaining_roles[0]
                        reason = f"[Safety Clamp] '{original}' already contributed; redirected to remaining specialist '{next_agent}'."
                        decision_type = "safety_clamp"
                    else:
                        next_agent = "writer"
                        reason = "[Safety Clamp] All specialists have already contributed; forced routing to 'writer'."
                        decision_type = "safety_clamp"
            elif next_agent == "FINISH":
                if not writer_executed:
                    next_agent = "writer"
                    reason = "[Safety Clamp] Writer has not synthesized results yet; forced routing to 'writer' before FINISH."
                    decision_type = "safety_clamp"
                else:
                    trace.append(SupervisorTraceStep(step_idx, decision_type, "FINISH", reason))
                    break
            elif next_agent == "writer":
                if writer_executed:
                    # Writer already ran, finish
                    trace.append(SupervisorTraceStep(step_idx, decision_type, "FINISH", "Writer already completed."))
                    break
            else:
                # Invalid name fallback
                next_agent = remaining_roles[0] if remaining_roles else ("writer" if not writer_executed else "FINISH")
                reason = f"[Safety Clamp] Unknown target '{next_agent}' clamped to '{next_agent}'."
                decision_type = "safety_clamp"

            trace.append(SupervisorTraceStep(step_idx, decision_type, next_agent, reason))

            # 4. Execute Selected Agent
            if next_agent in self.roles:
                role_spec = self.roles[next_agent]
                specialist_context = (
                    f"## Background & Overall Task\n{task}\n\n"
                    f"## Specialist Role\n{role_spec.system_prompt}\n\n"
                    f"## Findings from Previous Specialists\n{history_summary}\n\n"
                    f"## Your Mission\n"
                    f"Provide your professional analysis from the perspective of {role_spec.name}. "
                    f"Deliver 3-4 dense, concrete, bullet points with technical reasoning. "
                    f"Be concise, fact-based, rigorous, and direct (under 300 words). Avoid repeating earlier points."
                )
                llm_call_count += 1
                content = self._call(
                    [
                        {"role": "system", "content": role_spec.system_prompt},
                        {"role": "user", "content": specialist_context},
                    ],
                    max_tokens=1500,
                )
                specialist_outputs.append(
                    SpecialistOutput(role=next_agent, reasoning=reason, content=content)
                )
                contributed_roles.add(next_agent)

            elif next_agent == "writer":
                # Execute Dedicated Writer
                full_findings = "\n\n".join(
                    f"### Perspective: {o.role.upper()}\n{o.content}"
                    for o in specialist_outputs
                )
                writer_prompt = (
                    f"You are the senior synthesis writer. Combine all specialist findings into a cohesive, "
                    f"structured final report for the user's task.\n\n"
                    f"## Original Task\n{task}\n\n"
                    f"## Specialist Findings\n{full_findings}\n\n"
                    f"## Requirements\n"
                    f"- Executive summary & final recommendation.\n"
                    f"- Markdown comparison table of trade-offs.\n"
                    f"- Concrete actionable conclusion.\n"
                    f"- Direct and compact (under 400 words)."
                )
                llm_call_count += 1
                final_report = self._call(
                    [
                        {"role": "system", "content": "You are a master synthesis writer and tech lead. Output crisp markdown."},
                        {"role": "user", "content": writer_prompt},
                    ],
                    max_tokens=1800,
                )
                writer_executed = True

            elif next_agent == "FINISH":
                break

        elapsed = time.time() - start_time
        return SupervisorResult(
            task=task,
            specialist_outputs=specialist_outputs,
            final_report=final_report,
            trace=trace,
            total_llm_calls=llm_call_count,
            elapsed_seconds=elapsed,
        )


# ===========================================================================
# Pattern 2: Decentralized Blackboard (HEARSAY-II Style)
# ===========================================================================
@dataclass
class BlackboardBid:
    role: str
    will_contribute: bool
    confidence: int  # 1-5
    effective_confidence: float
    preview: str
    reason: str = ""


@dataclass
class BlackboardEntry:
    round_index: int
    role: str
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class BlackboardResult:
    task: str
    blackboard: List[BlackboardEntry]
    final_synthesis: str
    bidding_history: List[List[BlackboardBid]]
    total_llm_calls: int
    elapsed_seconds: float


class BlackboardSystem:
    """Decentralized Multi-Agent Blackboard Architecture.

    Features:
      - Shared Blackboard: visible append-only ledger for all Knowledge Sources.
      - Self-Assessed Bidding: each agent assesses relevance and bids (confidence 1-5).
      - Deterministic Mechanical Arbiter: Python max() selection without supervisor LLM.
      - Domination Decay: penalties for frequently-contributing agents to foster diverse debate.
      - Convergence: terminates naturally when no agent bids >= min_confidence.
    """

    def __init__(
        self,
        knowledge_sources: Dict[str, AgentRole],
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_ENDPOINT,
        max_rounds: int = 4,
        min_confidence: int = 3,
        domination_decay: float = 1.0,
        llm_fn: Optional[Callable[..., str]] = None,
    ):
        self.knowledge_sources = knowledge_sources
        self.model = model
        self.endpoint = endpoint
        self.max_rounds = max_rounds
        self.min_confidence = min_confidence
        self.domination_decay = domination_decay
        self.llm_fn = llm_fn or call_llm

    def _call(self, messages: List[Dict[str, str]], max_tokens: int = 3500) -> str:
        return self.llm_fn(
            messages=messages,
            model=self.model,
            endpoint=self.endpoint,
            max_tokens=max_tokens,
        )

    def _format_blackboard(self, entries: List[BlackboardEntry]) -> str:
        if not entries:
            return "(Blackboard is currently empty. Be the first to post a hypothesis or fact.)"
        return "\n\n".join(
            f"[Round {e.round_index} | {e.role.upper()}]:\n{e.content}"
            for e in entries
        )

    def run(self, task: str) -> BlackboardResult:
        start_time = time.time()
        llm_call_count = 0
        blackboard: List[BlackboardEntry] = []
        bidding_history: List[List[BlackboardBid]] = []
        contribution_counts: Dict[str, int] = {k: 0 for k in self.knowledge_sources}

        for round_idx in range(1, self.max_rounds + 1):
            current_board_str = self._format_blackboard(blackboard)
            counts_str = ", ".join(f"{r}={c}" for r, c in contribution_counts.items())

            # 1. Bidding Round: Query each Knowledge Source
            round_bids: List[BlackboardBid] = []
            for role_name, role_spec in self.knowledge_sources.items():
                my_count = contribution_counts[role_name]
                fairness_prompt = (
                    f"You have already contributed {my_count} time(s) to this blackboard.\n"
                    if my_count > 0
                    else "You have NOT contributed yet to this blackboard.\n"
                )

                bid_eval_prompt = (
                    f"## Role\n{role_spec.system_prompt}\n\n"
                    f"## Core Problem / Task\n{task}\n\n"
                    f"## Current Blackboard Status\n{current_board_str}\n\n"
                    f"## Contribution Record\n{counts_str}\n\n"
                    f"## Self-Assessment Instructions\n{fairness_prompt}"
                    f"- Evaluate if you have substantive, unique, NEW insights to add given what is ALREADY on the blackboard.\n"
                    f"- If your perspective is already adequately addressed or you have nothing novel to add, set will_contribute=false.\n"
                    f"- Rate your confidence from 1 (marginal) to 5 (critical breakthrough).\n"
                    f"- Output strictly a JSON object formatted as:\n"
                    f"{{\n"
                    f'  "will_contribute": true/false,\n'
                    f'  "confidence": <integer 1 to 5>,\n'
                    f'  "preview": "<1-2 sentence preview of your proposed contribution>",\n'
                    f'  "reason": "<why this perspective is needed now>"\n'
                    f"}}"
                )

                llm_call_count += 1
                raw_bid = self._call(
                    [
                        {"role": "system", "content": "You are a self-governing knowledge source in a blackboard system. Output JSON only."},
                        {"role": "user", "content": bid_eval_prompt},
                    ],
                    max_tokens=1500,
                )

                try:
                    bid_dict = parse_json_from_response(raw_bid)
                    will_c = bool(bid_dict.get("will_contribute", False))
                    raw_conf = int(bid_dict.get("confidence", 1))
                    preview = str(bid_dict.get("preview", ""))
                    reason = str(bid_dict.get("reason", ""))
                except Exception:
                    will_c = False
                    raw_conf = 1
                    preview = "(Failed to parse bid)"
                    reason = "Parse error"

                # Calculate effective confidence with Domination Decay
                decay_penalty = my_count * self.domination_decay
                eff_conf = max(0.0, float(raw_conf) - decay_penalty)

                round_bids.append(
                    BlackboardBid(
                        role=role_name,
                        will_contribute=will_c,
                        confidence=raw_conf,
                        effective_confidence=eff_conf,
                        preview=preview,
                        reason=reason,
                    )
                )

            bidding_history.append(round_bids)

            # 2. Host Mechanical Arbiter (Pure Python Deterministic Selection)
            eligible_bids = [
                b for b in round_bids
                if b.will_contribute and b.effective_confidence >= self.min_confidence
            ]

            if not eligible_bids:
                # Natural convergence: no agent has strong enough novel input
                break

            # Select winner: highest effective confidence (tie-breaking by lowest previous contribution count)
            winner = max(
                eligible_bids,
                key=lambda b: (b.effective_confidence, -contribution_counts[b.role]),
            )

            # 3. Winner Acts and Writes to Blackboard
            winner_role = self.knowledge_sources[winner.role]
            act_prompt = (
                f"## Role\n{winner_role.system_prompt}\n\n"
                f"## Core Problem / Task\n{task}\n\n"
                f"## Blackboard Context\n{current_board_str}\n\n"
                f"## Your Winning Bid\nPreview: {winner.preview}\n\n"
                f"## Action Instructions\n"
                f"Execute your contribution now in 2-3 dense, technical paragraphs or bullet points (under 250 words). "
                f"Focus on directly solving the open questions on the blackboard."
            )

            llm_call_count += 1
            act_content = self._call(
                [
                    {"role": "system", "content": winner_role.system_prompt},
                    {"role": "user", "content": act_prompt},
                ],
                max_tokens=1500,
            )

            blackboard.append(
                BlackboardEntry(
                    round_index=round_idx,
                    role=winner.role,
                    content=act_content,
                )
            )
            contribution_counts[winner.role] += 1

        # 4. Final Synthesis pass across all blackboard contributions
        final_board_str = self._format_blackboard(blackboard)
        synthesis_prompt = (
            f"You are the master arbiter and final synthesis author.\n\n"
            f"## Original Question / Task\n{task}\n\n"
            f"## Complete Blackboard Trace\n{final_board_str}\n\n"
            f"## Synthesis Requirements\n"
            f"Synthesize the collaborative findings from all knowledge sources into a concise decision report:\n"
            f"1. Executive Summary & Core Conclusion\n"
            f"2. Key Arguments & Evidence from the Blackboard\n"
            f"3. Risk Analysis & Trade-offs\n"
            f"4. Actionable Final Guidance\n"
            f"Keep direct, structured, and under 400 words."
        )
        llm_call_count += 1
        final_synthesis = self._call(
            [
                {"role": "system", "content": "You are a master technical lead synthesizing complex debate results. Output crisp markdown."},
                {"role": "user", "content": synthesis_prompt},
            ],
            max_tokens=1800,
        )

        elapsed = time.time() - start_time
        return BlackboardResult(
            task=task,
            blackboard=blackboard,
            final_synthesis=final_synthesis,
            bidding_history=bidding_history,
            total_llm_calls=llm_call_count,
            elapsed_seconds=elapsed,
        )
