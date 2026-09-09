"""mini-harness Day 12: Dynamic Decision Flow.

Implements two advanced dynamic reasoning and routing patterns:
1. TreeOfThoughtsEngine: Beam search over reasoning tree with multi-branch expansion,
   independent evaluation, and top-N pruning.
2. MetaControllerEngine: Dynamic architectural router that classifies task shape and
   dispatches to the optimal engine (Direct, ReAct, Reflection, PEV).
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Ensure src path is accessible
sys.path.insert(0, str(Path(__file__).parent))

DEFAULT_ENDPOINT = "http://127.0.0.1:12340/v1"
DEFAULT_MODEL = "qwen/qwen3.8-27b"


def call_llm(
    messages: List[Dict[str, str]],
    model: str = DEFAULT_MODEL,
    endpoint: str = DEFAULT_ENDPOINT,
    temperature: float = 0.4,
    max_tokens: int = 1500,
    timeout: float = 180.0,
) -> str:
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
    raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        pass

    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except Exception:
            pass

    start_idx = raw.find("{")
    while start_idx != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start_idx, len(raw)):
            ch = raw[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if not in_string:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = raw[start_idx : i + 1]
                        try:
                            return json.loads(candidate)
                        except Exception:
                            break
        start_idx = raw.find("{", start_idx + 1)

    raise ValueError(f"Failed to parse valid JSON from output:\n{raw}")


# ===========================================================================
# Pattern 1: Tree of Thoughts (ToT) Engine
# ===========================================================================
@dataclass
class ThoughtNode:
    node_id: str
    parent_id: Optional[str]
    thought_text: str
    score: float  # 1.0 - 5.0
    reason: str
    depth: int
    cumulative_score: float = 0.0


@dataclass
class ToTResult:
    task: str
    best_path: List[ThoughtNode]
    final_solution: str
    tree_nodes: List[ThoughtNode]
    total_llm_calls: int
    elapsed_seconds: float


class TreeOfThoughtsEngine:
    """Tree of Thoughts (ToT) Beam Search Engine.

    Executes multi-branch generation, strict objective scoring, and beam pruning.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_ENDPOINT,
        llm_fn: Optional[Callable[..., str]] = None,
    ):
        self.model = model
        self.endpoint = endpoint
        self.llm_fn = llm_fn or call_llm

    def _call(self, messages: List[Dict[str, str]], max_tokens: int = 1500, temp: float = 0.4) -> str:
        return self.llm_fn(
            messages=messages,
            model=self.model,
            endpoint=self.endpoint,
            temperature=temp,
            max_tokens=max_tokens,
        )

    def expand(self, task: str, path: List[ThoughtNode], k: int) -> List[str]:
        """Generates k substantively diverse next thought candidates."""
        path_str = "\n".join(f"Step {n.depth}: {n.thought_text}" for n in path) or "(Root: starting point)"
        prompt = (
            f"You are exploring a reasoning path to solve a complex engineering task.\n\n"
            f"## Task\n{task}\n\n"
            f"## Reasoning Path So Far\n{path_str}\n\n"
            f"## Mission\n"
            f"Propose exactly {k} distinct, creative, and substantively DIFFERENT next steps or technical approaches.\n"
            f"RULES:\n"
            f"- Each candidate must explore a unique angle (e.g. approach A vs approach B vs approach C).\n"
            f"- Avoid paraphrasing or near-duplicates.\n"
            f"- Output strictly a JSON object formatted as:\n"
            f"{{\n"
            f'  "candidates": [\n'
            f'    "candidate 1 description...",\n'
            f'    "candidate 2 description...",\n'
            f'    "candidate 3 description..."\n'
            f"  ]\n"
            f"}}"
        )
        raw = self._call(
            [
                {"role": "system", "content": "You are a creative technical architect generating diverse reasoning branches. Output strict JSON only."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=2000,
            temp=0.7,
        )
        try:
            parsed = parse_json_from_response(raw)
            candidates = parsed.get("candidates", [])
            if isinstance(candidates, list) and candidates:
                return [str(c).strip() for c in candidates[:k]]
        except Exception:
            pass
        return [f"Alternative approach {i+1} for: {task[:50]}" for i in range(k)]

    def score_thought(self, task: str, path: List[ThoughtNode], candidate_text: str) -> Tuple[float, str]:
        """Strict objective evaluation of a thought candidate (1.0 to 5.0)."""
        path_str = "\n".join(f"Step {n.depth}: {n.thought_text}" for n in path) or "(Root: starting point)"
        prompt = (
            f"You are a strict, objective technical evaluator judging a proposed step toward solving a task.\n\n"
            f"## Task\n{task}\n\n"
            f"## Prior Steps\n{path_str}\n\n"
            f"## Candidate Step to Evaluate\n{candidate_text}\n\n"
            f"## Scoring Rubric (1.0 to 5.0)\n"
            f"- 5.0: Exceptional; directly advances the solution with superior soundness.\n"
            f"- 4.0: Strong; logical and promising with minor trade-offs.\n"
            f"- 3.0: Mediocre; viable but has noticeable risks or inefficiencies.\n"
            f"- 2.0: Weak; flawed logic or superficial.\n"
            f"- 1.0: Dead end; completely off-track or invalid.\n\n"
            f"Output strictly a JSON object formatted as:\n"
            f"{{\n"
            f'  "score": <float between 1.0 and 5.0>,\n'
            f'  "reason": "<1-2 sentence justification>"\n'
            f"}}"
        )
        raw = self._call(
            [
                {"role": "system", "content": "You are a ruthless technical evaluator. Output strict JSON only."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1000,
            temp=0.1,
        )
        try:
            parsed = parse_json_from_response(raw)
            score = float(parsed.get("score", 3.0))
            reason = str(parsed.get("reason", "Standard evaluation"))
            return max(1.0, min(5.0, score)), reason
        except Exception:
            return 3.0, "Parse fallback score"

    def solve(
        self,
        task: str,
        branching: int = 3,
        beam_width: int = 2,
        max_depth: int = 2,
    ) -> ToTResult:
        start_time = time.time()
        llm_call_count = 0
        all_nodes: List[ThoughtNode] = []

        # Root node
        root = ThoughtNode(
            node_id="root",
            parent_id=None,
            thought_text=task,
            score=5.0,
            reason="Root task",
            depth=0,
            cumulative_score=0.0,
        )
        all_nodes.append(root)

        # Beam search frontier: list of paths, where each path is List[ThoughtNode]
        frontier: List[List[ThoughtNode]] = [[root]]

        for depth in range(1, max_depth + 1):
            candidates_pool: List[Tuple[List[ThoughtNode], ThoughtNode]] = []

            for path in frontier:
                parent = path[-1]
                # 1. Expand
                llm_call_count += 1
                candidate_texts = self.expand(task, path[1:], k=branching)

                # 2. Score each candidate
                for idx, text in enumerate(candidate_texts):
                    llm_call_count += 1
                    score, reason = self.score_thought(task, path[1:], text)
                    child_id = f"d{depth}_p{parent.node_id}_{idx}"
                    child_node = ThoughtNode(
                        node_id=child_id,
                        parent_id=parent.node_id,
                        thought_text=text,
                        score=score,
                        reason=reason,
                        depth=depth,
                        cumulative_score=parent.cumulative_score + score,
                    )
                    all_nodes.append(child_node)
                    candidates_pool.append((path + [child_node], child_node))

            # 3. Beam Prune: Keep top beam_width paths based on cumulative / average score
            candidates_pool.sort(key=lambda x: x[1].cumulative_score / depth, reverse=True)
            frontier = [path for path, node in candidates_pool[:beam_width]]

            if not frontier:
                break

        # Best complete path
        best_path = frontier[0] if frontier else [root]

        # 4. Final synthesis along best path
        path_summary = "\n\n".join(
            f"Step {n.depth} (Score {n.score:.1f}/5.0): {n.thought_text}\nReason: {n.reason}"
            for n in best_path[1:]
        )
        synthesis_prompt = (
            f"You are the senior lead engineer synthesizing the winning solution from a Tree of Thoughts search.\n\n"
            f"## Task\n{task}\n\n"
            f"## Selected Best Reasoning Path\n{path_summary}\n\n"
            f"## Instructions\n"
            f"Synthesize the thoughts from this winning path into a clear, comprehensive, and actionable final solution.\n"
            f"Provide an architectural overview and concrete implementation steps in markdown."
        )
        llm_call_count += 1
        final_solution = self._call(
            [
                {"role": "system", "content": "You are a master software architect delivering the final solution."},
                {"role": "user", "content": synthesis_prompt},
            ],
            max_tokens=1800,
            temp=0.3,
        )

        elapsed = time.time() - start_time
        return ToTResult(
            task=task,
            best_path=best_path,
            final_solution=final_solution,
            tree_nodes=all_nodes,
            total_llm_calls=llm_call_count,
            elapsed_seconds=elapsed,
        )


# ===========================================================================
# Pattern 2: Meta Controller (Architectural Router)
# ===========================================================================
@dataclass
class ArchitectureResult:
    task: str
    chosen_arch: str
    reason: str
    complexity_level: str
    output: str
    total_llm_calls: int
    elapsed_seconds: float


class MetaControllerEngine:
    """Meta Controller: Classifies task shape and routes to the optimal architecture.

    Supported Roster:
      - 'direct': Direct one-shot completion (low complexity, fast).
      - 'react': ReAct loop (investigative search, tool calling).
      - 'reflection': Reflection loop (code polishing, quality-sensitive generation).
      - 'pev': Plan-Execute-Verify (multi-step tasks, test verification).
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_ENDPOINT,
        llm_fn: Optional[Callable[..., str]] = None,
    ):
        self.model = model
        self.endpoint = endpoint
        self.llm_fn = llm_fn or call_llm

    def _call(self, messages: List[Dict[str, str]], max_tokens: int = 1500) -> str:
        return self.llm_fn(
            messages=messages,
            model=self.model,
            endpoint=self.endpoint,
            max_tokens=max_tokens,
        )

    def route(self, task: str) -> Dict[str, str]:
        """Classifies task and selects the target architecture."""
        prompt = (
            f"You are the central Meta Controller deciding which Agent Architecture should execute a user task.\n\n"
            f"## User Task\n{task}\n\n"
            f"## Available Architectures\n"
            f"1. 'direct': Direct one-shot generation. Best for simple questions, basic text formatting, or trivial code.\n"
            f"2. 'react': ReAct loop (Thought-Action-Observation). Best for multi-step information lookup or exploratory tool calling.\n"
            f"3. 'reflection': Reflection loop (Generator-Critic). Best for single-artifact code/algorithm creation where correctness, edge-cases, and code quality must be iteratively polished.\n"
            f"4. 'pev': Plan-Execute-Verify (Planner-Executor-Verifier). Best for complex multi-step workflows, file operations, or tasks requiring physical execution gates.\n\n"
            f"## Instructions\n"
            f"Select the single most appropriate architecture. Output strictly a JSON object formatted as:\n"
            f"{{\n"
            f'  "chosen_arch": "<one of: direct, react, reflection, pev>",\n'
            f'  "complexity_level": "<low, medium, high>",\n'
            f'  "reason": "<one sentence explaining why this architecture fits the task>"\n'
            f"}}"
        )
        raw = self._call(
            [
                {"role": "system", "content": "You are a pragmatic systems architect routing tasks to architectures. Output strict JSON only."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=800,
        )
        try:
            parsed = parse_json_from_response(raw)
            chosen = str(parsed.get("chosen_arch", "direct")).lower().strip()
            if chosen not in ["direct", "react", "reflection", "pev"]:
                chosen = "direct"
            complexity = str(parsed.get("complexity_level", "medium"))
            reason = str(parsed.get("reason", "Default route"))
            return {"chosen_arch": chosen, "complexity_level": complexity, "reason": reason}
        except Exception:
            return {"chosen_arch": "direct", "complexity_level": "low", "reason": "Fallback to direct"}

    def run(self, task: str) -> ArchitectureResult:
        start_time = time.time()
        routing_decision = self.route(task)
        chosen = routing_decision["chosen_arch"]
        reason = routing_decision["reason"]
        complexity = routing_decision["complexity_level"]

        total_calls = 1  # 1 for routing decision
        output_text = ""

        # Dispatch
        if chosen == "direct":
            total_calls += 1
            output_text = self._call(
                [
                    {"role": "system", "content": "You are a concise, helpful engineering assistant."},
                    {"role": "user", "content": task},
                ],
                max_tokens=1500,
            )

        elif chosen == "reflection":
            from day08_reflection import ReflectionAgent

            agent = ReflectionAgent(target_score=85, max_iterations=2)
            ref_res = agent.run(task)
            total_calls += ref_res.get("total_iterations", 1) * 2
            output_text = (
                f"### [Reflection Engine Output]\n\n"
                f"{ref_res.get('final_draft', '')}\n\n"
                f"**Review History ({ref_res.get('total_iterations', 1)} rounds, Final Score: {ref_res.get('final_score', 0)}/100):**\n"
                + "\n".join(f"- Round {t['iteration']} Score: {t['score']}/100 | Assessment: {t['summary']}" for t in ref_res.get("trace", []))
            )

        elif chosen == "pev":
            from day09_pev import PEVAgent

            agent = PEVAgent(max_retries=2)
            plan_res = agent.run(task)
            total_calls += len(plan_res.steps) * 2
            output_text = (
                f"### [PEV Engine Output]\n\n"
                f"Status: {'SUCCESS' if plan_res.success else 'FAILED'}\n"
                f"Execution Trace ({len(plan_res.steps)} steps):\n"
                + "\n".join(f"- Step {s.step_id} [{s.description}]: Verified={s.verified}" for s in plan_res.steps)
                + f"\n\nFinal Output:\n{plan_res.final_output}"
            )

        elif chosen == "react":
            from core import HarnessEngine

            engine = HarnessEngine(max_steps=4)
            react_res = engine.run(task)
            total_calls += len(react_res.history)
            output_text = (
                f"### [ReAct Engine Output]\n\n"
                f"{react_res.final_answer}\n\n"
                f"(Completed in {len(react_res.history)} steps, Rollbacks: {react_res.metrics.total_rollbacks})"
            )

        elapsed = time.time() - start_time
        return ArchitectureResult(
            task=task,
            chosen_arch=chosen,
            reason=reason,
            complexity_level=complexity,
            output=output_text,
            total_llm_calls=total_calls,
            elapsed_seconds=elapsed,
        )
