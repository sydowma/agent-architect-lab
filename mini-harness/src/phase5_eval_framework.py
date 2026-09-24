"""Phase 5: Multi-Agent Quantitative Evaluation and LLM-as-Judge Framework.

Implements zero-dependency multi-agent quantitative evaluation and attribution:
1. Telemetry & Cost Attribution (Per-Agent token usage, latency, TTFT, cost breakdown).
2. Four-Dimensional Evaluation Metrics (Task Efficacy, Collaboration Quality, Cost Attribution, Fault Tolerance).
3. LLM-as-Judge Engine (Rubric-driven scoring, chain-of-thought rationale).
4. Bias Mitigation Architecture (Position-bias Swap Evaluation, length penalty).
5. Topology Ablation Benchmarking (Single Agent vs DAG vs GroupChat vs Magentic-One).
"""

from __future__ import annotations

import collections
import copy
import dataclasses
from dataclasses import dataclass, field
import json
import re
import statistics
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from phase5_pico_kernel import AgentMessage, MultiAgentBus, PicoAgent, Role


# ============================================================================
# 1. 遥测与数据实体 (Telemetry & Domain Models)
# ============================================================================

def estimate_tokens(text: str) -> int:
    """Zero-dependency heuristic token estimator (~4 chars or 0.75 words per token)."""
    if not text:
        return 0
    words = len(text.strip().split())
    chars = len(text)
    return max(1, int((words * 1.3 + chars / 4.0) / 2.0))


@dataclass
class AgentTelemetry:
    """Per-agent execution telemetry and cost slice."""
    agent_name: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    turn_count: int = 0
    duration_sec: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class RunMetric:
    """Global execution metric for a single multi-agent run."""
    run_id: str
    topology_type: str
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_sec: float = 0.0
    turn_count: int = 0
    agent_metrics: Dict[str, AgentTelemetry] = field(default_factory=dict)
    is_success: bool = True
    error_message: Optional[str] = None
    cost_efficiency_index: float = 0.0

    def calculate_attribution(self) -> Dict[str, float]:
        """Calculates percentage of total tokens consumed by each agent."""
        if self.total_tokens == 0:
            return {name: 0.0 for name in self.agent_metrics}
        return {
            name: round((atm.total_tokens / self.total_tokens) * 100.0, 2)
            for name, atm in self.agent_metrics.items()
        }


@dataclass
class RubricDimension:
    """Individual criterion in a structured grading rubric."""
    name: str
    weight: float
    description: str


@dataclass
class EvalCase:
    """Benchmark test case with ground truth criteria and rubrics."""
    case_id: str
    task_prompt: str
    expected_keywords: List[str] = field(default_factory=list)
    forbidden_keywords: List[str] = field(default_factory=list)
    rubric_dimensions: List[RubricDimension] = field(default_factory=list)
    reference_solution: Optional[str] = None


@dataclass
class JudgeVerdict:
    """Structured decision returned by the LLM-as-Judge evaluator."""
    case_id: str
    overall_score: float  # 0.0 to 1.0
    dimension_scores: Dict[str, float]
    passed: bool
    rationale: str
    redundancy_penalty: float = 0.0


# ============================================================================
# 2. 遥测追踪收集器 (MultiAgentTelemetryTracker)
# ============================================================================

class MultiAgentTelemetryTracker:
    """Intercepts and records granular token consumption and latency per agent."""

    def __init__(self, run_id: str, topology_type: str):
        self.run_id = run_id
        self.topology_type = topology_type
        self.start_time = time.time()
        self.agent_telemetry: Dict[str, AgentTelemetry] = {}
        self.total_turns = 0

    def record_turn(
        self,
        agent_name: str,
        prompt_text: str,
        response_text: str,
        duration: float = 0.0,
    ) -> None:
        """Records telemetry for an individual agent turn."""
        if agent_name not in self.agent_telemetry:
            self.agent_telemetry[agent_name] = AgentTelemetry(agent_name=agent_name)

        p_tokens = estimate_tokens(prompt_text)
        c_tokens = estimate_tokens(response_text)

        atm = self.agent_telemetry[agent_name]
        atm.prompt_tokens += p_tokens
        atm.completion_tokens += c_tokens
        atm.turn_count += 1
        atm.duration_sec += duration
        self.total_turns += 1

    def finalize(self, is_success: bool = True, error: Optional[str] = None) -> RunMetric:
        """Computes aggregated metric report."""
        total_p = sum(a.prompt_tokens for a in self.agent_telemetry.values())
        total_c = sum(a.completion_tokens for a in self.agent_telemetry.values())
        total_tok = total_p + total_c
        duration = round(time.time() - self.start_time, 4)

        return RunMetric(
            run_id=self.run_id,
            topology_type=self.topology_type,
            total_tokens=total_tok,
            prompt_tokens=total_p,
            completion_tokens=total_c,
            duration_sec=duration,
            turn_count=self.total_turns,
            agent_metrics=self.agent_telemetry,
            is_success=is_success,
            error_message=error,
        )


# ============================================================================
# 3. 评测裁判引擎与偏置防护 (LLMAsJudge Engine)
# ============================================================================

class LLMAsJudge:
    """Structured rubric-guided evaluator with cognitive bias mitigation."""

    def __init__(
        self,
        custom_judge_fn: Optional[Callable[[str, str], Dict[str, Any]]] = None,
        pass_threshold: float = 0.70,
    ):
        self.custom_judge_fn = custom_judge_fn
        self.pass_threshold = pass_threshold

    def evaluate(
        self,
        case: EvalCase,
        output_text: str,
        turn_history: Optional[List[AgentMessage]] = None,
    ) -> JudgeVerdict:
        """Evaluates model output against case ground-truth and rubric dimensions."""
        dim_scores: Dict[str, float] = {}
        rationales: List[str] = []

        # 1. 确定性断言检查 (Deterministic Assertion Check)
        lower_output = output_text.lower()
        hard_constraint_failed = False
        if case.expected_keywords:
            matched = [kw for kw in case.expected_keywords if kw.lower() in lower_output]
            kw_score = len(matched) / len(case.expected_keywords)
            dim_scores["keyword_coverage"] = round(kw_score, 2)
            rationales.append(f"Matched {len(matched)}/{len(case.expected_keywords)} required keywords.")
        else:
            dim_scores["keyword_coverage"] = 1.0

        # Forbidden keywords penalty (Hard safety constraint)
        if case.forbidden_keywords:
            violating = [kw for kw in case.forbidden_keywords if kw.lower() in lower_output]
            if violating:
                dim_scores["security_constraint"] = 0.0
                hard_constraint_failed = True
                rationales.append(f"Violated forbidden keywords: {violating}")
            else:
                dim_scores["security_constraint"] = 1.0

        # 2. 协作冗余惩罚 (Redundancy / Loop Penalty)
        redundancy_penalty = 0.0
        if turn_history and len(turn_history) > 2:
            repeated_msgs = 0
            seen_texts: set[str] = set()
            for msg in turn_history:
                norm = msg.content.strip().lower()[:50]
                if norm in seen_texts:
                    repeated_msgs += 1
                seen_texts.add(norm)
            redundancy_rate = repeated_msgs / len(turn_history)
            redundancy_penalty = round(min(0.3, redundancy_rate * 0.5), 2)
            if redundancy_penalty > 0:
                rationales.append(f"Applied redundancy penalty: -{redundancy_penalty}")

        # 3. 结构化量规评定 (Rubric Dimensions Evaluation)
        if case.rubric_dimensions:
            for rubric in case.rubric_dimensions:
                if hard_constraint_failed and "sec" in rubric.name.lower():
                    r_score = 0.0
                    rationales.append(f"Rubric '{rubric.name}': Failed due to safety constraint violation.")
                elif self.custom_judge_fn:
                    res = self.custom_judge_fn(rubric.description, output_text)
                    r_score = float(res.get("score", 0.8))
                    rationales.append(f"Rubric '{rubric.name}': {res.get('reason', 'Passed')}")
                else:
                    # Heuristic fallback: check if output matches rubric key terms
                    r_score = 1.0 if not any(err in lower_output for err in ["error", "fail", "null"]) else 0.5
                dim_scores[rubric.name] = r_score

        # 4. 加权计算综合得分
        if case.rubric_dimensions:
            total_weight = sum(r.weight for r in case.rubric_dimensions)
            weighted_sum = sum(dim_scores.get(r.name, 0.5) * r.weight for r in case.rubric_dimensions)
            base_score = weighted_sum / (total_weight if total_weight > 0 else 1.0)
        else:
            base_score = sum(dim_scores.values()) / max(1, len(dim_scores))

        if hard_constraint_failed:
            final_score = 0.0
            passed = False
        else:
            final_score = max(0.0, round(base_score - redundancy_penalty, 2))
            passed = final_score >= self.pass_threshold

        return JudgeVerdict(
            case_id=case.case_id,
            overall_score=final_score,
            dimension_scores=dim_scores,
            passed=passed,
            rationale=" | ".join(rationales) if rationales else "All criteria satisfied.",
            redundancy_penalty=redundancy_penalty,
        )

    def swap_evaluate_pair(
        self,
        case: EvalCase,
        output_a: str,
        output_b: str,
        pairwise_judge_fn: Callable[[str, str, str], str],
    ) -> Dict[str, Any]:
        """Mitigates Position Bias via Swap Evaluation (Round 1: [A, B] vs Round 2: [B, A])."""
        # Round 1: A first, B second
        v1 = pairwise_judge_fn(case.task_prompt, output_a, output_b)
        # Round 2: B first, A second
        v2 = pairwise_judge_fn(case.task_prompt, output_b, output_a)

        # Normalize v2 outcome
        mapped_v2: str
        if v2 == "option_1":
            mapped_v2 = "option_2"  # Option 1 was B, so B won
        elif v2 == "option_2":
            mapped_v2 = "option_1"  # Option 2 was A, so A won
        else:
            mapped_v2 = "tie"

        decisive: bool = (v1 == mapped_v2) and (v1 in ["option_1", "option_2"])
        winner = v1 if decisive else "tie"
        position_bias_detected = (v1 != mapped_v2) and (v1 != "tie") and (mapped_v2 != "tie")

        return {
            "round_1_winner": v1,
            "round_2_normalized_winner": mapped_v2,
            "final_winner": winner,
            "decisive": decisive,
            "position_bias_detected": position_bias_detected,
        }


# ============================================================================
# 4. 拓扑基准与消融评测器 (MultiAgentEvaluator & BenchmarkReport)
# ============================================================================

@dataclass
class BenchmarkReport:
    """Consolidated benchmark summary for a multi-agent system or topology."""
    topology_name: str
    total_cases: int
    mean_score: float
    pass_rate: float
    mean_tokens: float
    mean_duration_sec: float
    cost_efficiency_index: float  # (mean_score * 1000) / mean_tokens
    case_results: List[Dict[str, Any]] = field(default_factory=list)

    def to_markdown_table(self) -> str:
        """Renders benchmark summary as a formatted Markdown table."""
        header = "| Metric | Value |\n| :--- | :--- |\n"
        rows = [
            f"| **Topology** | `{self.topology_name}` |",
            f"| **Evaluated Cases** | {self.total_cases} |",
            f"| **Mean Task Score** | {self.mean_score:.2f} / 1.00 |",
            f"| **Pass Rate** | {self.pass_rate * 100.0:.1f}% |",
            f"| **Mean Token Consumption** | {int(self.mean_tokens)} tokens |",
            f"| **Mean Latency** | {self.mean_duration_sec:.3f} s |",
            f"| **Cost-Efficiency Index (CEI)** | {self.cost_efficiency_index:.2f} |",
        ]
        return header + "\n".join(rows)


class MultiAgentEvaluator:
    """Benchmark runner for multi-agent architectures and ablation studies."""

    def __init__(self, judge: Optional[LLMAsJudge] = None):
        self.judge = judge or LLMAsJudge()

    def evaluate_runner(
        self,
        topology_name: str,
        cases: List[EvalCase],
        runner_fn: Callable[[EvalCase], Tuple[str, RunMetric]],
    ) -> BenchmarkReport:
        """Executes test cases against a topology runner function and compiles BenchmarkReport."""
        results: List[Dict[str, Any]] = []
        scores: List[float] = []
        passes: List[bool] = []
        tokens_list: List[int] = []
        durations: List[float] = []

        for case in cases:
            output_text, metric = runner_fn(case)
            verdict = self.judge.evaluate(case, output_text)

            scores.append(verdict.overall_score)
            passes.append(verdict.passed)
            tokens_list.append(metric.total_tokens)
            durations.append(metric.duration_sec)

            results.append({
                "case_id": case.case_id,
                "score": verdict.overall_score,
                "passed": verdict.passed,
                "tokens": metric.total_tokens,
                "duration_sec": metric.duration_sec,
                "attribution": metric.calculate_attribution(),
                "rationale": verdict.rationale,
            })

        mean_sc = statistics.mean(scores) if scores else 0.0
        pass_rt = (sum(1 for p in passes if p) / len(passes)) if passes else 0.0
        mean_tok = statistics.mean(tokens_list) if tokens_list else 1.0
        mean_dur = statistics.mean(durations) if durations else 0.0
        cei = (mean_sc * 1000.0) / (mean_tok if mean_tok > 0 else 1.0)

        return BenchmarkReport(
            topology_name=topology_name,
            total_cases=len(cases),
            mean_score=round(mean_sc, 2),
            pass_rate=round(pass_rt, 2),
            mean_tokens=round(mean_tok, 1),
            mean_duration_sec=round(mean_dur, 4),
            cost_efficiency_index=round(cei, 2),
            case_results=results,
        )
