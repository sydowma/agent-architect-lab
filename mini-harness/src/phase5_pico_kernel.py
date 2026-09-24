"""Phase 5: PicoAgents Teaching-Grade Micro-Kernel & Multi-Agent Streaming Middleware.

Inspired by Victor Dibia's 'Designing Multi-Agent Systems' (picoagents architecture):
1. Typed message & streaming chunk contracts (AgentMessage, AgentStreamChunk).
2. Pure Onion Middleware Pipeline (before_turn, on_stream_chunk, after_turn).
3. Production-grade Middleware implementations:
   - TokenBudgetMiddleware (enforces strict quota limits & early circuit-breaking).
   - StreamingLogMiddleware (captures real-time token telemetry without blocking).
   - ContextInjectMiddleware (injects cross-agent shared knowledge without prompt pollution).
4. PicoAgent: Transparent, zero-magic streaming agent with pluggable tools.
5. MultiAgentBus: Centralized, decoupled event bus supporting P2P and Broadcast routing.
"""

from __future__ import annotations

import abc
import copy
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple


# ============================================================================
# 0. 异常体系 (Exceptions)
# ============================================================================

class PicoKernelError(Exception):
    """Base exception for PicoAgents micro-kernel violations."""


class TokenBudgetExceededError(PicoKernelError):
    """Raised when an agent turn exceeds allocated token budget."""


class AgentNotFoundError(PicoKernelError):
    """Raised when sending a message to an unregistered agent ID."""


class MiddlewarePipelineError(PicoKernelError):
    """Raised when a middleware halts turn execution."""


# ============================================================================
# 1. 强类型消息与流式分块协议 (Typed Contracts)
# ============================================================================

class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class AgentMessage:
    """Strongly-typed message passed across agents and the bus."""
    message_id: str
    role: Role
    sender: str
    recipient: str  # specific agent_name or "*" for broadcast
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentStreamChunk:
    """Discrete streaming token packet emitted by an LLM generator."""
    chunk_id: str
    sender: str
    delta_text: str
    is_final: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentContext:
    """Shared execution context across middlewares and turns."""
    session_id: str
    turn_index: int
    shared_memory: Dict[str, Any] = field(default_factory=dict)
    telemetry: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# 2. 洋葱模型中间件流水线 (PicoMiddleware Pipeline)
# ============================================================================

class PicoMiddleware(abc.ABC):
    """Base class for PicoAgents onion middleware."""

    def before_turn(self, ctx: AgentContext, message: AgentMessage) -> AgentMessage:
        """Invoked before the model call begins. May modify message or halt execution."""
        return message

    def on_stream_chunk(self, ctx: AgentContext, chunk: AgentStreamChunk) -> AgentStreamChunk:
        """Invoked in real-time as each token chunk is emitted."""
        return chunk

    def after_turn(self, ctx: AgentContext, response: AgentMessage) -> AgentMessage:
        """Invoked after model streaming completes and final message is assembled."""
        return response


class TokenBudgetMiddleware(PicoMiddleware):
    """Monitors token consumption and trips a circuit-breaker if budget is exceeded."""

    def __init__(self, max_tokens: int = 1000):
        self.max_tokens = max_tokens
        self.used_tokens = 0

    def before_turn(self, ctx: AgentContext, message: AgentMessage) -> AgentMessage:
        # Estimate incoming tokens
        est_tokens = len(message.content) // 4
        if self.used_tokens + est_tokens > self.max_tokens:
            raise TokenBudgetExceededError(
                f"Token budget exceeded: {self.used_tokens + est_tokens} > limit {self.max_tokens}"
            )
        return message

    def on_stream_chunk(self, ctx: AgentContext, chunk: AgentStreamChunk) -> AgentStreamChunk:
        # Increment used tokens on every chunk
        self.used_tokens += max(1, len(chunk.delta_text) // 4)
        if self.used_tokens > self.max_tokens:
            raise TokenBudgetExceededError(
                f"Token budget blown during stream: {self.used_tokens} > {self.max_tokens}"
            )
        return chunk

    def after_turn(self, ctx: AgentContext, response: AgentMessage) -> AgentMessage:
        ctx.telemetry["total_tokens_used"] = self.used_tokens
        return response


class StreamingLogMiddleware(PicoMiddleware):
    """Captures real-time token chunks for observable telemetry."""

    def __init__(self):
        self.captured_chunks: List[AgentStreamChunk] = []

    def on_stream_chunk(self, ctx: AgentContext, chunk: AgentStreamChunk) -> AgentStreamChunk:
        self.captured_chunks.append(chunk)
        return chunk

    def after_turn(self, ctx: AgentContext, response: AgentMessage) -> AgentMessage:
        ctx.telemetry["streamed_chunks_count"] = len(self.captured_chunks)
        return response


class ContextInjectMiddleware(PicoMiddleware):
    """Injects cross-agent shared memory context into the prompt before execution."""

    def __init__(self, context_key: str = "team_goal"):
        self.context_key = context_key

    def before_turn(self, ctx: AgentContext, message: AgentMessage) -> AgentMessage:
        if self.context_key in ctx.shared_memory:
            shared_info = ctx.shared_memory[self.context_key]
            augmented_content = f"[SHARED TEAM CONTEXT: {shared_info}]\n{message.content}"
            # Return new augmented message
            return AgentMessage(
                message_id=message.message_id,
                role=message.role,
                sender=message.sender,
                recipient=message.recipient,
                content=augmented_content,
                timestamp=message.timestamp,
                metadata=message.metadata,
            )
        return message


# ============================================================================
# 3. 零黑盒微智能体 (PicoAgent)
# ============================================================================

class PicoAgent:
    """Transparent, streaming-first agent executing through an onion middleware stack."""

    def __init__(
        self,
        name: str,
        role_description: str,
        system_prompt: str = "",
        middlewares: Optional[List[PicoMiddleware]] = None,
        generator_fn: Optional[Callable[[AgentMessage], Iterator[str]]] = None,
    ):
        self.name = name
        self.role_description = role_description
        self.system_prompt = system_prompt or f"You are {name}, specialized in {role_description}."
        self.middlewares: List[PicoMiddleware] = middlewares or []
        self.generator_fn = generator_fn or self._default_mock_generator
        self.memory: List[AgentMessage] = []

    def _default_mock_generator(self, msg: AgentMessage) -> Iterator[str]:
        """Default streaming generator for testing and standalone execution."""
        tokens = [
            f"[{self.name} received]: ",
            msg.content[:20],
            "... Analyzing requirements. ",
            "Proposed solution verified."
        ]
        for t in tokens:
            time.sleep(0.001)
            yield t

    def add_middleware(self, middleware: PicoMiddleware) -> None:
        self.middlewares.append(middleware)

    def generate_stream(self, ctx: AgentContext, incoming_msg: AgentMessage) -> Iterator[AgentStreamChunk]:
        """Executes turn as a generator, passing through the onion pipeline."""
        # 1. Before turn middleware chain
        msg = incoming_msg
        for mw in self.middlewares:
            msg = mw.before_turn(ctx, msg)

        self.memory.append(msg)

        # 2. Generator streaming with on_stream_chunk interception
        accumulated_text: List[str] = []
        chunk_idx = 0
        for delta in self.generator_fn(msg):
            chunk_idx += 1
            raw_chunk = AgentStreamChunk(
                chunk_id=f"chk_{self.name}_{ctx.turn_index}_{chunk_idx}",
                sender=self.name,
                delta_text=delta,
                is_final=False,
            )

            # Pass chunk through middleware pipeline
            processed_chunk = raw_chunk
            for mw in self.middlewares:
                processed_chunk = mw.on_stream_chunk(ctx, processed_chunk)

            accumulated_text.append(processed_chunk.delta_text)
            yield processed_chunk

        # Final chunk
        final_chunk = AgentStreamChunk(
            chunk_id=f"chk_{self.name}_{ctx.turn_index}_final",
            sender=self.name,
            delta_text="",
            is_final=True,
        )
        for mw in self.middlewares:
            final_chunk = mw.on_stream_chunk(ctx, final_chunk)
        yield final_chunk

        # 3. Assemble response and run after_turn chain
        full_content = "".join(accumulated_text)
        response_msg = AgentMessage(
            message_id=f"msg_{self.name}_{uuid.uuid4().hex[:6]}",
            role=Role.ASSISTANT,
            sender=self.name,
            recipient=incoming_msg.sender,
            content=full_content,
        )

        for mw in reversed(self.middlewares):
            response_msg = mw.after_turn(ctx, response_msg)

        self.memory.append(response_msg)

    def generate(self, ctx: AgentContext, incoming_msg: AgentMessage) -> AgentMessage:
        """Non-streaming convenience helper that consumes the stream and returns final message."""
        stream = self.generate_stream(ctx, incoming_msg)
        # Consume entire stream
        for _ in stream:
            pass
        return self.memory[-1]


# ============================================================================
# 4. 多智能体协作消息总线 (MultiAgentBus)
# ============================================================================

class MultiAgentBus:
    """Decoupled messaging bus for Multi-Agent coordination (Broadcast & P2P)."""

    def __init__(self, session_id: str = "pico_bus"):
        self.session_id = session_id
        self.agents: Dict[str, PicoAgent] = {}
        self.history: List[AgentMessage] = []
        self.shared_memory: Dict[str, Any] = {}
        self.turn_counter = 0

    def register(self, agent: PicoAgent) -> None:
        self.agents[agent.name] = agent

    def send_p2p(self, sender: str, recipient: str, content: str) -> AgentMessage:
        """Direct Point-to-Point message routing."""
        if recipient not in self.agents:
            raise AgentNotFoundError(f"Recipient agent '{recipient}' is not registered on the bus.")

        self.turn_counter += 1
        msg = AgentMessage(
            message_id=f"bus_{self.turn_counter}_{uuid.uuid4().hex[:6]}",
            role=Role.USER,
            sender=sender,
            recipient=recipient,
            content=content,
        )
        self.history.append(msg)

        ctx = AgentContext(
            session_id=self.session_id,
            turn_index=self.turn_counter,
            shared_memory=self.shared_memory,
        )

        target_agent = self.agents[recipient]
        response = target_agent.generate(ctx, msg)
        self.history.append(response)
        return response

    def broadcast(self, sender: str, content: str) -> List[AgentMessage]:
        """Broadcasts a message to all registered agents except the sender."""
        responses = []
        for name, agent in self.agents.items():
            if name == sender:
                continue
            res = self.send_p2p(sender=sender, recipient=name, content=content)
            responses.append(res)
        return responses
