"""Unit and Integration Tests for Phase 5 Topic 1: PicoAgents Micro-Kernel.

Verifies:
1. PicoAgent streaming chunk emission and final aggregation.
2. Onion middleware execution order (M1 before -> M2 before -> M2 after -> M1 after).
3. TokenBudgetMiddleware quota enforcement and early circuit-breaking.
4. StreamingLogMiddleware telemetry collection.
5. ContextInjectMiddleware shared memory prompt augmentation.
6. MultiAgentBus point-to-point and broadcast routing.
"""

import unittest
from phase5_pico_kernel import (
    AgentContext,
    AgentMessage,
    AgentNotFoundError,
    AgentStreamChunk,
    ContextInjectMiddleware,
    MultiAgentBus,
    PicoAgent,
    PicoMiddleware,
    Role,
    StreamingLogMiddleware,
    TokenBudgetExceededError,
    TokenBudgetMiddleware,
)


class TestPicoAgentsMicroKernel(unittest.TestCase):

    def test_01_pico_agent_streaming_emission_and_assembly(self):
        """PicoAgent emits discrete stream chunks and aggregates final message."""
        def custom_stream(msg: AgentMessage):
            yield "Hello "
            yield "from "
            yield "streaming "
            yield "kernel."

        agent = PicoAgent(name="echo_bot", role_description="Echoes tokens", generator_fn=custom_stream)
        ctx = AgentContext(session_id="test_sess", turn_index=1)
        incoming = AgentMessage(
            message_id="msg_1",
            role=Role.USER,
            sender="mark",
            recipient="echo_bot",
            content="Hi",
        )

        chunks = list(agent.generate_stream(ctx, incoming))
        # 4 content chunks + 1 final chunk
        self.assertEqual(len(chunks), 5)
        self.assertFalse(chunks[0].is_final)
        self.assertEqual(chunks[0].delta_text, "Hello ")
        self.assertTrue(chunks[-1].is_final)
        self.assertEqual(chunks[-1].delta_text, "")

        # Verify aggregated memory
        self.assertEqual(len(agent.memory), 2)
        self.assertEqual(agent.memory[-1].content, "Hello from streaming kernel.")

    def test_02_onion_middleware_ordering(self):
        """Middlewares execute in onion order: M1 before -> M2 before -> M2 after -> M1 after."""
        order_trace = []

        class OrderTrackerMiddleware(PicoMiddleware):
            def __init__(self, name: str):
                self.name = name

            def before_turn(self, ctx: AgentContext, message: AgentMessage) -> AgentMessage:
                order_trace.append(f"{self.name}.before")
                return message

            def after_turn(self, ctx: AgentContext, response: AgentMessage) -> AgentMessage:
                order_trace.append(f"{self.name}.after")
                return response

        m1 = OrderTrackerMiddleware("M1")
        m2 = OrderTrackerMiddleware("M2")

        agent = PicoAgent(name="onion_bot", role_description="Tests onion order", middlewares=[m1, m2])
        ctx = AgentContext(session_id="test_sess", turn_index=1)
        msg = AgentMessage("msg_test", Role.USER, "user", "onion_bot", "Ping")

        agent.generate(ctx, msg)
        self.assertEqual(order_trace, ["M1.before", "M2.before", "M2.after", "M1.after"])

    def test_03_token_budget_middleware_circuit_breaker(self):
        """TokenBudgetMiddleware halts execution when token limits are breached."""
        # 1. Reject upfront if incoming message is too large
        tight_budget = TokenBudgetMiddleware(max_tokens=10)
        agent = PicoAgent(
            name="budget_bot",
            role_description="Budget tester",
            middlewares=[tight_budget],
        )
        ctx = AgentContext(session_id="test_sess", turn_index=1)
        huge_msg = AgentMessage("huge", Role.USER, "user", "budget_bot", "A" * 100)

        with self.assertRaises(TokenBudgetExceededError):
            agent.generate(ctx, huge_msg)

        # 2. Halt mid-stream if generated tokens exceed quota
        stream_budget = TokenBudgetMiddleware(max_tokens=5)
        def heavy_generator(msg: AgentMessage):
            yield "Short "
            yield "Now a very long token sequence that blows the limit completely!"

        leaky_agent = PicoAgent(
            name="leaky_bot",
            role_description="Leaky stream",
            middlewares=[stream_budget],
            generator_fn=heavy_generator,
        )
        small_msg = AgentMessage("small", Role.USER, "user", "leaky_bot", "Go")
        with self.assertRaises(TokenBudgetExceededError):
            list(leaky_agent.generate_stream(ctx, small_msg))

    def test_04_streaming_log_middleware_telemetry(self):
        """StreamingLogMiddleware captures token chunks and records count in telemetry."""
        logger_mw = StreamingLogMiddleware()
        agent = PicoAgent(
            name="telemetry_bot",
            role_description="Telemetry test",
            middlewares=[logger_mw],
        )
        ctx = AgentContext(session_id="test_sess", turn_index=1)
        msg = AgentMessage("m1", Role.USER, "user", "telemetry_bot", "Test stream")

        agent.generate(ctx, msg)
        self.assertGreater(ctx.telemetry.get("streamed_chunks_count", 0), 0)
        self.assertEqual(len(logger_mw.captured_chunks), ctx.telemetry["streamed_chunks_count"])

    def test_05_context_inject_middleware_augmentation(self):
        """ContextInjectMiddleware prepends shared team context before LLM sees it."""
        injector = ContextInjectMiddleware(context_key="sprint_goal")
        received_content = []

        def inspecting_generator(msg: AgentMessage):
            received_content.append(msg.content)
            yield "Acknowledged."

        agent = PicoAgent(
            name="worker",
            role_description="Worker",
            middlewares=[injector],
            generator_fn=inspecting_generator,
        )
        ctx = AgentContext(
            session_id="test_sess",
            turn_index=1,
            shared_memory={"sprint_goal": "Deliver mini-harness v2.0"},
        )
        msg = AgentMessage("m1", Role.USER, "lead", "worker", "Implement feature X")

        agent.generate(ctx, msg)
        self.assertEqual(len(received_content), 1)
        self.assertIn("[SHARED TEAM CONTEXT: Deliver mini-harness v2.0]", received_content[0])
        self.assertIn("Implement feature X", received_content[0])

    def test_06_multi_agent_bus_p2p_and_broadcast(self):
        """MultiAgentBus correctly routes point-to-point and broadcast messages."""
        bus = MultiAgentBus(session_id="team_bus")
        planner = PicoAgent(name="planner", role_description="System architect")
        coder = PicoAgent(name="coder", role_description="Software engineer")
        reviewer = PicoAgent(name="reviewer", role_description="Code auditor")

        bus.register(planner)
        bus.register(coder)
        bus.register(reviewer)

        # 1. P2P Routing: user -> planner
        resp = bus.send_p2p(sender="user", recipient="planner", content="Design database schema")
        self.assertEqual(resp.sender, "planner")
        self.assertEqual(resp.recipient, "user")
        self.assertEqual(len(bus.history), 2)  # input + response

        # 2. Broadcast Routing: lead -> all registered (planner, coder, reviewer)
        broadcast_resps = bus.broadcast(sender="lead", content="All hands: code freeze at 5 PM")
        self.assertEqual(len(broadcast_resps), 3)
        self.assertEqual({r.sender for r in broadcast_resps}, {"planner", "coder", "reviewer"})

        # 3. Unregistered Recipient Error
        with self.assertRaises(AgentNotFoundError):
            bus.send_p2p(sender="user", recipient="ghost_agent", content="Hello?")


if __name__ == "__main__":
    unittest.main(verbosity=2)
