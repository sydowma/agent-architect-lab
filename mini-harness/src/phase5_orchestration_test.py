"""Unit and Integration Tests for Phase 5 Topic 2: Multi-Agent Orchestration Patterns.

Verifies:
1. Static DAG Workflow: topological execution order, upstream context passing.
2. Static DAG Workflow: Kahn's cycle detection and validation errors.
3. Dynamic GroupChat: RoundRobin turn order and rotation.
4. Dynamic GroupChat: AIDriven context/keyword routing.
5. Dynamic GroupChat: consensus early-termination criteria.
6. Magentic-One: Outer-Inner loop happy path execution.
7. Magentic-One: Dynamic Replanning on subtask failure with fallback route.
8. Magentic-One: Max replans exhaustion raising ReplanningLimitExceededError.
9. Magentic-One: Custom step validator integration.
"""

import unittest
from phase5_pico_kernel import (
    AgentContext,
    AgentMessage,
    MultiAgentBus,
    PicoAgent,
    Role,
)
from phase5_orchestration import (
    AIDrivenSelector,
    DAGTaskNode,
    DAGWorkflow,
    GroupChat,
    MagenticOrchestrator,
    PlanLedger,
    PlanStep,
    ReplanningLimitExceededError,
    RoundRobinSelector,
    TaskStatus,
    WorkflowCycleError,
    WorkflowExecutionError,
)


class TestDAGWorkflow(unittest.TestCase):
    """Test suite for Static DAG Workflow orchestration."""

    def setUp(self):
        self.bus = MultiAgentBus()

    def test_01_dag_linear_execution(self):
        """Linear pipeline (A -> B -> C) executes in strict topological order with upstream context."""
        def make_agent(name: str):
            return PicoAgent(
                name=name,
                role_description=f"Agent {name}",
                generator_fn=lambda msg: [f"Output from {name} processed: '{msg.content}'"],
            )

        agent_a = make_agent("agent_a")
        agent_b = make_agent("agent_b")
        agent_c = make_agent("agent_c")

        self.bus.register(agent_a)
        self.bus.register(agent_b)
        self.bus.register(agent_c)

        dag = DAGWorkflow(name="linear_pipeline")
        node_a = DAGTaskNode(task_id="step1", assigned_agent="agent_a", instruction="Extract data")
        node_b = DAGTaskNode(task_id="step2", assigned_agent="agent_b", instruction="Transform data")
        node_b.add_dependency("step1")
        node_c = DAGTaskNode(task_id="step3", assigned_agent="agent_c", instruction="Load data")
        node_c.add_dependency("step2")

        dag.add_node(node_a)
        dag.add_node(node_b)
        dag.add_node(node_c)

        results = dag.execute(self.bus)

        self.assertEqual(len(results), 3)
        self.assertIn("Extract data", results["step1"])
        self.assertIn("[Output of step1]", results["step2"])
        self.assertIn("[Output of step2]", results["step3"])
        self.assertEqual(node_a.status, TaskStatus.COMPLETED)
        self.assertEqual(node_b.status, TaskStatus.COMPLETED)
        self.assertEqual(node_c.status, TaskStatus.COMPLETED)

    def test_02_dag_diamond_branching_execution(self):
        """Diamond workflow (root -> [branch_a, branch_b] -> merge) aggregates both upstream outputs."""
        def mock_gen(msg: AgentMessage):
            return [f"processed: {msg.content}"]

        for name in ["root_agent", "worker_1", "worker_2", "merge_agent"]:
            self.bus.register(PicoAgent(name=name, role_description=name, generator_fn=mock_gen))

        dag = DAGWorkflow(name="diamond_pipeline")
        n_root = DAGTaskNode(task_id="root", assigned_agent="root_agent", instruction="Initial split")
        n_b1 = DAGTaskNode(task_id="branch_1", assigned_agent="worker_1", instruction="Compute metric A", depends_on={"root"})
        n_b2 = DAGTaskNode(task_id="branch_2", assigned_agent="worker_2", instruction="Compute metric B", depends_on={"root"})
        n_merge = DAGTaskNode(task_id="merge", assigned_agent="merge_agent", instruction="Synthesize", depends_on={"branch_1", "branch_2"})

        dag.add_node(n_root)
        dag.add_node(n_b1)
        dag.add_node(n_b2)
        dag.add_node(n_merge)

        results = dag.execute(self.bus)

        self.assertEqual(len(results), 4)
        # Merge task context must contain both branch_1 and branch_2 outputs
        merge_output = results["merge"]
        self.assertIn("[Output of branch_1]", merge_output)
        self.assertIn("[Output of branch_2]", merge_output)

    def test_03_dag_cycle_detection_kahn(self):
        """Kahn's cycle detection detects circular dependencies (A -> B -> C -> A) and raises WorkflowCycleError."""
        dag = DAGWorkflow(name="cyclic_pipeline")
        n1 = DAGTaskNode(task_id="task_a", assigned_agent="agent_a", instruction="A", depends_on={"task_c"})
        n2 = DAGTaskNode(task_id="task_b", assigned_agent="agent_b", instruction="B", depends_on={"task_a"})
        n3 = DAGTaskNode(task_id="task_c", assigned_agent="agent_c", instruction="C", depends_on={"task_b"})

        dag.add_node(n1)
        dag.add_node(n2)
        dag.add_node(n3)

        with self.assertRaises(WorkflowCycleError):
            dag.detect_cycles()

    def test_04_dag_self_loop_cycle_detection(self):
        """Self-referential loop (A -> A) is detected and blocked."""
        dag = DAGWorkflow(name="self_loop")
        n1 = DAGTaskNode(task_id="task_a", assigned_agent="agent_a", instruction="A", depends_on={"task_a"})
        dag.add_node(n1)

        with self.assertRaises(WorkflowCycleError):
            dag.detect_cycles()

    def test_05_dag_missing_dependency_error(self):
        """Referencing an undeclared dependency raises WorkflowExecutionError."""
        dag = DAGWorkflow(name="broken_dag")
        n1 = DAGTaskNode(task_id="task_a", assigned_agent="agent_a", instruction="A", depends_on={"missing_task"})
        dag.add_node(n1)

        with self.assertRaises(WorkflowExecutionError):
            dag.detect_cycles()


class TestGroupChat(unittest.TestCase):
    """Test suite for Dynamic Roundtable GroupChat."""

    def test_06_groupchat_round_robin_rotation(self):
        """RoundRobinSelector rotates turns fairly across all participants in order."""
        turn_history: list[str] = []

        def make_agent(name: str):
            def _gen(msg: AgentMessage):
                turn_history.append(name)
                return [f"I am {name} speaking."]
            return PicoAgent(name=name, role_description=name, generator_fn=_gen)

        alice = make_agent("Alice")
        bob = make_agent("Bob")
        charlie = make_agent("Charlie")

        selector = RoundRobinSelector(participants=["Alice", "Bob", "Charlie"])
        chat = GroupChat(agents=[alice, bob, charlie], selector=selector, max_turns=6)

        messages = chat.run(initial_topic="Discuss sprint planning")

        # 1 init message + 6 turn messages = 7 total messages
        self.assertEqual(len(messages), 7)
        expected_turns = ["Alice", "Bob", "Charlie", "Alice", "Bob", "Charlie"]
        self.assertEqual(turn_history, expected_turns)

    def test_07_groupchat_ai_driven_keyword_routing(self):
        """AIDrivenSelector routes speaker turns dynamically based on contextual keywords."""
        responses = {
            "coder": "I completed the API endpoint. We need to check security token leak and auth rules.",
            "reviewer_security": "Security audit passed. Now please inspect the database sql schema table.",
            "db_specialist": "Database migration looks clean. [CONSENSUS] Architecture approved.",
        }

        agents = [
            PicoAgent(name="coder", role_description="Coder", generator_fn=lambda m: [responses["coder"]]),
            PicoAgent(name="reviewer_security", role_description="Security", generator_fn=lambda m: [responses["reviewer_security"]]),
            PicoAgent(name="db_specialist", role_description="DB", generator_fn=lambda m: [responses["db_specialist"]]),
        ]

        moderator = PicoAgent(name="moderator", role_description="AI Mod", generator_fn=lambda m: ["ok"])
        selector = AIDrivenSelector(manager_agent=moderator)
        chat = GroupChat(agents=agents, selector=selector, max_turns=5)

        messages = chat.run(initial_topic="Please implement code for new system feature and create patch")

        speakers = [m.sender for m in messages if m.sender != "Moderator"]
        # Coder speaks first -> mentions security -> reviewer_security speaks -> mentions database -> db_specialist speaks
        self.assertEqual(speakers[:3], ["coder", "reviewer_security", "db_specialist"])
        # And because db_specialist emitted [CONSENSUS], it terminates in 3 turns!
        self.assertEqual(len(speakers), 3)

    def test_08_groupchat_consensus_termination(self):
        """GroupChat immediately terminates when any agent emits a consensus keyword."""
        def quick_agree(msg: AgentMessage):
            return ["Everything looks solid. APPROVED"]

        agent = PicoAgent(name="lead", role_description="Lead", generator_fn=quick_agree)
        selector = RoundRobinSelector(participants=["lead"])
        chat = GroupChat(agents=[agent], selector=selector, max_turns=10)

        messages = chat.run(initial_topic="Quick signoff")
        # Should terminate at turn 1, not running up to 10
        self.assertEqual(len(messages), 2)  # init + 1 turn


class TestMagenticOneOrchestrator(unittest.TestCase):
    """Test suite for Magentic-One Planning Engine and Dynamic Replanning."""

    def setUp(self):
        self.bus = MultiAgentBus()

    def test_09_magentic_one_happy_path(self):
        """Standard plan executes sequentially through Outer/Inner loops without replanning."""
        worker = PicoAgent(
            name="worker",
            role_description="General worker",
            generator_fn=lambda msg: [f"Successfully completed: {msg.content[:20]}"],
        )
        self.bus.register(worker)

        orchestrator = MagenticOrchestrator(bus=self.bus, max_replans=2)
        initial_steps = [
            PlanStep(step_id=1, title="Step 1", assignee="worker", instruction="Do part 1"),
            PlanStep(step_id=2, title="Step 2", assignee="worker", instruction="Do part 2"),
        ]

        ledger = orchestrator.run_plan(objective="Deliver Feature", initial_steps=initial_steps)

        self.assertEqual(ledger.version, 1)
        self.assertEqual(ledger.replan_count, 0)
        self.assertTrue(all(s.status == TaskStatus.COMPLETED for s in ledger.steps))
        self.assertEqual(len(ledger.steps), 2)

    def test_10_magentic_one_dynamic_replanning_on_failure(self):
        """Subtask failure triggers Outer Loop dynamic replanning and recovers with fallback."""
        attempt_counts = {"count": 0}

        def flaky_worker_gen(msg: AgentMessage):
            attempt_counts["count"] += 1
            if "[REPLANNED]" in msg.content:
                return ["Fallback strategy succeeded!"]
            return ["[ERROR] Database connection refused"]

        flaky_worker = PicoAgent(
            name="flaky_worker",
            role_description="Flaky worker",
            generator_fn=flaky_worker_gen,
        )
        self.bus.register(flaky_worker)

        orchestrator = MagenticOrchestrator(bus=self.bus, max_replans=2)
        initial_steps = [
            PlanStep(step_id=1, title="Connect DB", assignee="flaky_worker", instruction="Connect to primary cluster"),
        ]

        ledger = orchestrator.run_plan(objective="DB Operations", initial_steps=initial_steps)

        # Verified replanning: version bumped to 2, replan_count=1, step status COMPLETED
        self.assertEqual(ledger.replan_count, 1)
        self.assertEqual(ledger.version, 2)
        self.assertEqual(ledger.steps[0].status, TaskStatus.COMPLETED)
        self.assertIn("Fallback Route", ledger.steps[0].title)
        self.assertEqual(ledger.steps[0].result, "Fallback strategy succeeded!")

    def test_11_magentic_one_replan_limit_exceeded(self):
        """When subtask continually fails and exceeds max_replans, ReplanningLimitExceededError is raised."""
        broken_worker = PicoAgent(
            name="broken_worker",
            role_description="Broken",
            generator_fn=lambda msg: ["[ERROR] Permanent hardware failure"],
        )
        self.bus.register(broken_worker)

        orchestrator = MagenticOrchestrator(bus=self.bus, max_replans=1)
        initial_steps = [
            PlanStep(step_id=1, title="Critical task", assignee="broken_worker", instruction="Execute"),
        ]

        with self.assertRaises(ReplanningLimitExceededError):
            orchestrator.run_plan(objective="Mission Critical", initial_steps=initial_steps)

    def test_12_magentic_one_custom_step_validator(self):
        """External step_validator can invalidate results and trigger replanning."""
        responses = ["Answer without signature", "Answer with SIGNED: ok"]
        call_idx = [0]

        def validating_worker(msg: AgentMessage):
            resp = responses[call_idx[0]]
            call_idx[0] += 1
            return [resp]

        agent = PicoAgent(name="signer", role_description="Signer", generator_fn=validating_worker)
        self.bus.register(agent)

        def custom_validator(step: PlanStep, result: str) -> bool:
            return "SIGNED: ok" in result

        orchestrator = MagenticOrchestrator(bus=self.bus, max_replans=2)
        initial_steps = [
            PlanStep(step_id=1, title="Sign document", assignee="signer", instruction="Produce signature"),
        ]

        ledger = orchestrator.run_plan(
            objective="Sign Contract",
            initial_steps=initial_steps,
            step_validator=custom_validator,
        )

        self.assertEqual(ledger.replan_count, 1)
        self.assertEqual(ledger.steps[0].status, TaskStatus.COMPLETED)
        self.assertIn("SIGNED: ok", ledger.steps[0].result)


if __name__ == "__main__":
    unittest.main()
