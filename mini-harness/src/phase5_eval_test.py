"""Unit and Integration Tests for Phase 5 Topic 4: Multi-Agent Quantitative Evaluation Framework.

Verifies:
1. MultiAgentTelemetryTracker: Per-Agent token usage and cost attribution.
2. LLMAsJudge: Rubric-driven scoring, keyword coverage, forbidden keyword penalties, and redundancy penalties.
3. Position Bias Mitigation: Swap Evaluation correctly detects position bias and resolves ties.
4. Static DAG Workflow evaluation: Integrating telemetry tracker with DAG execution.
5. GroupChat Collaboration evaluation: Measuring turn efficiency and consensus.
6. Ablation Benchmarking: Comparing Single-Agent vs Multi-Agent topology and rendering BenchmarkReport.
"""

import time
import unittest
from phase5_eval_framework import (
    AgentTelemetry,
    BenchmarkReport,
    EvalCase,
    JudgeVerdict,
    LLMAsJudge,
    MultiAgentEvaluator,
    MultiAgentTelemetryTracker,
    RubricDimension,
    RunMetric,
    estimate_tokens,
)
from phase5_orchestration import (
    DAGTaskNode,
    DAGWorkflow,
    GroupChat,
    RoundRobinSelector,
)
from phase5_pico_kernel import (
    AgentContext,
    AgentMessage,
    MultiAgentBus,
    PicoAgent,
    Role,
)


class TestMultiAgentEvalFramework(unittest.TestCase):
    """Test suite for Phase 5 Topic 4 Multi-Agent Evaluation & LLM-as-Judge."""

    def test_01_telemetry_collection_and_attribution(self):
        """Telemetry tracker captures per-agent prompt/completion tokens and cost attribution."""
        tracker = MultiAgentTelemetryTracker(run_id="run_001", topology_type="dag_pipeline")

        # Simulate turns for 3 agents
        tracker.record_turn(
            agent_name="planner",
            prompt_text="User objective: build auth API",
            response_text="Plan: 1. DB schema, 2. Endpoint, 3. Tests",
            duration=0.01,
        )
        tracker.record_turn(
            agent_name="coder",
            prompt_text="Implement JWT endpoint based on plan",
            response_text="def login(): return generate_jwt_token()",
            duration=0.02,
        )
        tracker.record_turn(
            agent_name="reviewer",
            prompt_text="Review the code for vulnerabilities",
            response_text="Code looks good. APPROVED",
            duration=0.01,
        )

        metric = tracker.finalize(is_success=True)

        self.assertEqual(metric.run_id, "run_001")
        self.assertEqual(metric.turn_count, 3)
        self.assertTrue(metric.total_tokens > 0)
        self.assertIn("planner", metric.agent_metrics)
        self.assertIn("coder", metric.agent_metrics)
        self.assertIn("reviewer", metric.agent_metrics)

        # Check attribution percentages sum to ~100%
        attribution = metric.calculate_attribution()
        total_pct = sum(attribution.values())
        self.assertAlmostEqual(total_pct, 100.0, delta=1.0)
        self.assertTrue(all(p > 0.0 for p in attribution.values()))

    def test_02_llm_as_judge_rubric_scoring(self):
        """LLMAsJudge computes weighted rubric scores, enforces forbidden keywords, and penalizes redundancy."""
        rubrics = [
            RubricDimension(name="functional_correctness", weight=0.6, description="Correct business logic"),
            RubricDimension(name="security", weight=0.4, description="No security leaks"),
        ]

        case = EvalCase(
            case_id="case_jwt",
            task_prompt="Implement secure JWT token verification",
            expected_keywords=["jwt", "verify", "signature"],
            forbidden_keywords=["eval(", "hardcoded_secret"],
            rubric_dimensions=rubrics,
        )

        judge = LLMAsJudge(pass_threshold=0.75)

        # 1. High-quality output
        good_output = "We verify the jwt signature using public key and return claims."
        verdict_good = judge.evaluate(case, good_output)
        self.assertTrue(verdict_good.passed)
        self.assertGreaterEqual(verdict_good.overall_score, 0.75)
        self.assertEqual(verdict_good.redundancy_penalty, 0.0)

        # 2. Violating forbidden keyword
        bad_output = "We check jwt signature and use hardcoded_secret = '123' to verify."
        verdict_bad = judge.evaluate(case, bad_output)
        self.assertFalse(verdict_bad.passed)
        self.assertEqual(verdict_bad.dimension_scores.get("security_constraint"), 0.0)

        # 3. Redundancy penalty in conversation history
        redundant_history = [
            AgentMessage(message_id="1", role=Role.USER, sender="User", recipient="*", content="Do task"),
            AgentMessage(message_id="2", role=Role.ASSISTANT, sender="A1", recipient="*", content="I agree completely."),
            AgentMessage(message_id="3", role=Role.ASSISTANT, sender="A2", recipient="*", content="I agree completely."),
            AgentMessage(message_id="4", role=Role.ASSISTANT, sender="A3", recipient="*", content="I agree completely."),
        ]
        verdict_red = judge.evaluate(case, good_output, turn_history=redundant_history)
        self.assertGreater(verdict_red.redundancy_penalty, 0.0)
        self.assertLess(verdict_red.overall_score, verdict_good.overall_score)

    def test_03_position_bias_mitigation(self):
        """Swap Evaluation identifies position bias and prevents biased comparative judgements."""
        judge = LLMAsJudge()
        case = EvalCase(case_id="comp_1", task_prompt="Select best code architecture")

        # Scenario A: Biased judge that always picks whichever option is presented first ("option_1")
        def biased_judge_fn(prompt: str, opt1: str, opt2: str) -> str:
            return "option_1"

        res_biased = judge.swap_evaluate_pair(
            case=case,
            output_a="Architecture Microservice",
            output_b="Architecture Monolith",
            pairwise_judge_fn=biased_judge_fn,
        )

        # In Round 1, option_1 was A -> says A won
        # In Round 2, option_1 was B -> says B won
        # Swap evaluation should flag position bias and declare a tie!
        self.assertTrue(res_biased["position_bias_detected"])
        self.assertFalse(res_biased["decisive"])
        self.assertEqual(res_biased["final_winner"], "tie")

        # Scenario B: Consistent, objective judge that always picks the truly superior option (Option A)
        def objective_judge_fn(prompt: str, opt1: str, opt2: str) -> str:
            if "Clean Architecture" in opt1:
                return "option_1"
            elif "Clean Architecture" in opt2:
                return "option_2"
            return "tie"

        res_obj = judge.swap_evaluate_pair(
            case=case,
            output_a="Clean Architecture with strict boundaries",
            output_b="Messy Spaghetti code",
            pairwise_judge_fn=objective_judge_fn,
        )

        self.assertFalse(res_obj["position_bias_detected"])
        self.assertTrue(res_obj["decisive"])
        self.assertEqual(res_obj["final_winner"], "option_1")

    def test_04_dag_workflow_evaluation(self):
        """Evaluates static DAG workflow execution with telemetry capture and rubric scoring."""
        bus = MultiAgentBus()
        tracker = MultiAgentTelemetryTracker(run_id="dag_eval_run", topology_type="dag_pipeline")

        def agent_fn(name: str):
            def _gen(msg: AgentMessage):
                prompt = msg.content
                resp = f"Result of {name}: processed [token verified, schema parsed, audit logged]."
                tracker.record_turn(agent_name=name, prompt_text=prompt, response_text=resp, duration=0.005)
                return [resp]
            return _gen

        bus.register(PicoAgent(name="extractor", role_description="Extractor", generator_fn=agent_fn("extractor")))
        bus.register(PicoAgent(name="auditor", role_description="Auditor", generator_fn=agent_fn("auditor")))

        dag = DAGWorkflow(name="auth_pipeline")
        n1 = DAGTaskNode(task_id="step1", assigned_agent="extractor", instruction="Extract and verify token")
        n2 = DAGTaskNode(task_id="step2", assigned_agent="auditor", instruction="Audit logging", depends_on={"step1"})
        dag.add_node(n1)
        dag.add_node(n2)

        results = dag.execute(bus)
        metric = tracker.finalize(is_success=True)

        final_output = results["step2"]
        case = EvalCase(
            case_id="dag_case_1",
            task_prompt="Process user token and log audit",
            expected_keywords=["token verified", "audit logged"],
        )

        judge = LLMAsJudge()
        verdict = judge.evaluate(case, final_output)

        self.assertTrue(verdict.passed)
        self.assertEqual(verdict.overall_score, 1.0)
        self.assertEqual(metric.turn_count, 2)
        self.assertIn("extractor", metric.agent_metrics)
        self.assertIn("auditor", metric.agent_metrics)

    def test_05_groupchat_collaboration_evaluation(self):
        """Measures GroupChat collaboration turn efficiency and consensus termination."""
        tracker = MultiAgentTelemetryTracker(run_id="gc_eval_run", topology_type="group_chat")

        def make_debater(name: str, response: str):
            def _gen(msg: AgentMessage):
                tracker.record_turn(agent_name=name, prompt_text=msg.content, response_text=response, duration=0.005)
                return [response]
            return PicoAgent(name=name, role_description=name, generator_fn=_gen)

        a1 = make_debater("lead", "I propose schema v2. [CONSENSUS]")
        selector = RoundRobinSelector(participants=["lead"])
        chat = GroupChat(agents=[a1], selector=selector, max_turns=5)

        messages = chat.run(initial_topic="Review schema")
        metric = tracker.finalize(is_success=True)

        # Terminated on turn 1 due to [CONSENSUS]
        self.assertEqual(metric.turn_count, 1)
        self.assertEqual(len(messages), 2)  # init + 1 turn
        self.assertIn("[CONSENSUS]", messages[-1].content)

    def test_06_ablation_benchmarking_and_report(self):
        """MultiAgentEvaluator runs ablation benchmarking across test cases and outputs formatted report."""
        cases = [
            EvalCase(
                case_id="case_1",
                task_prompt="Format database report",
                expected_keywords=["database", "report", "success"],
            ),
            EvalCase(
                case_id="case_2",
                task_prompt="Verify security credentials",
                expected_keywords=["credentials", "valid", "auth"],
            ),
        ]

        evaluator = MultiAgentEvaluator()

        # Runner A: Single Agent Baseline
        def single_agent_runner(case: EvalCase) -> tuple[str, RunMetric]:
            tracker = MultiAgentTelemetryTracker(run_id=f"single_{case.case_id}", topology_type="single_agent")
            resp = f"Executed {case.task_prompt}: database report success credentials valid auth."
            tracker.record_turn("solo_agent", case.task_prompt, resp, duration=0.005)
            return resp, tracker.finalize()

        report_single = evaluator.evaluate_runner("Single_Agent_Baseline", cases, single_agent_runner)

        self.assertEqual(report_single.total_cases, 2)
        self.assertEqual(report_single.pass_rate, 1.0)
        self.assertGreater(report_single.mean_tokens, 0)
        self.assertGreater(report_single.cost_efficiency_index, 0.0)

        # Check Markdown table rendering
        md_table = report_single.to_markdown_table()
        self.assertIn("| **Topology** | `Single_Agent_Baseline` |", md_table)
        self.assertIn("| **Mean Task Score** |", md_table)
        self.assertIn("| **Cost-Efficiency Index (CEI)** |", md_table)


if __name__ == "__main__":
    unittest.main()
