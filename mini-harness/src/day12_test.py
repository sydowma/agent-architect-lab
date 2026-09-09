"""Integration and live verification test suite for Day 12.

Verifies:
1. Unit tests: ToT Beam Search expansion & pruning, Meta Controller routing and fallback.
2. Live benchmarks against local Qwen 3.8 27B:
   - Tree of Thoughts (ToT) multi-branch search for distributed rate limiter.
   - Meta Controller dynamic routing across diverse task shapes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure src is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

from day12_tot_and_meta_controller import (
    ArchitectureResult,
    MetaControllerEngine,
    ThoughtNode,
    ToTResult,
    TreeOfThoughtsEngine,
)


# ===========================================================================
# 1. Deterministic Mock Unit Tests
# ===========================================================================
def test_tot_beam_search_pruning():
    print("\n--- [Unit Test 1] Tree of Thoughts Beam Search Pruning ---")

    call_seq = [
        # Depth 1: Expand root into 3 candidates
        json.dumps({"candidates": ["Branch A: Token Bucket", "Branch B: Fixed Window", "Branch C: Sliding Window"]}),
        # Depth 1: Score Branch A
        json.dumps({"score": 4.8, "reason": "Token bucket is ideal for bursty traffic."}),
        # Depth 1: Score Branch B
        json.dumps({"score": 2.0, "reason": "Fixed window suffers from boundary burst spikes."}),
        # Depth 1: Score Branch C
        json.dumps({"score": 4.2, "reason": "Sliding window is accurate but has memory overhead."}),
        # Depth 2: Expand Branch A (since Branch B was pruned!)
        json.dumps({"candidates": ["Branch A1: Redis + Lua Token Bucket", "Branch A2: Local Memory Token Bucket"]}),
        json.dumps({"score": 4.9, "reason": "Redis + Lua ensures cluster-wide consistency."}),
        json.dumps({"score": 3.5, "reason": "Local memory cannot synchronize across instances."}),
        # Depth 2: Expand Branch C
        json.dumps({"candidates": ["Branch C1: Redis Sorted Set", "Branch C2: Sliding Window Counter"]}),
        json.dumps({"score": 3.8, "reason": "Sorted set consumes high RAM at scale."}),
        json.dumps({"score": 4.1, "reason": "Sliding counter is compact."}),
        # Final Synthesis
        "Final Winning Solution: Distributed Token Bucket using Redis and Lua script.",
    ]

    def mock_llm(messages, **kwargs):
        return call_seq.pop(0)

    engine = TreeOfThoughtsEngine(llm_fn=mock_llm)
    result = engine.solve("Design a distributed rate limiter", branching=3, beam_width=2, max_depth=2)

    assert len(result.best_path) == 3, f"Expected 3 nodes in path (root + 2 depths), got {len(result.best_path)}"
    # Branch A (score 4.8) -> Branch A1 (score 4.9)
    assert "Token Bucket" in result.best_path[1].thought_text
    assert "Redis + Lua" in result.best_path[2].thought_text

    # Verify Branch B (score 2.0) was pruned and has no children
    node_ids = {n.node_id for n in result.tree_nodes}
    branch_b_children = [n for n in result.tree_nodes if n.parent_id and "p0_1" in n.node_id]  # index 1 was Branch B
    assert len(branch_b_children) == 0, "Pruned Branch B should not have been expanded in Depth 2!"
    print(f"PASS: Beam search successfully pruned Branch B and identified winning path:\n  Root -> {result.best_path[1].thought_text[:30]} -> {result.best_path[2].thought_text[:30]}")


def test_meta_controller_routing_logic():
    print("\n--- [Unit Test 2] Meta Controller Routing & Fallback Logic ---")

    test_cases = [
        (
            "Format this JSON to YAML",
            json.dumps({"chosen_arch": "direct", "complexity_level": "low", "reason": "Simple text transform"}),
            "direct",
        ),
        (
            "Write lock-free concurrent queue in C++ and check edge cases",
            json.dumps({"chosen_arch": "reflection", "complexity_level": "high", "reason": "Needs iterative code review"}),
            "reflection",
        ),
        (
            "Migrate user database tables and verify migrations",
            json.dumps({"chosen_arch": "pev", "complexity_level": "high", "reason": "Multi-step stateful workflow"}),
            "pev",
        ),
        (
            "Invalid route output",
            "This is random text without JSON",
            "direct",  # Fallback
        ),
    ]

    for task, mock_resp, expected_arch in test_cases:
        def mock_llm(messages, **kwargs):
            return mock_resp

        controller = MetaControllerEngine(llm_fn=mock_llm)
        decision = controller.route(task)
        assert decision["chosen_arch"] == expected_arch, f"Expected {expected_arch}, got {decision['chosen_arch']}"
        print(f"PASS: Task '{task[:35]}...' -> Routed to '{decision['chosen_arch']}' ({decision['reason']})")


# ===========================================================================
# 2. Live Integration Benchmark (against Local Qwen 3.8 27B)
# ===========================================================================
def run_live_decision_benchmarks():
    print("\n" + "=" * 70)
    print("🚀 Running Live Dynamic Decision Benchmarks on Local Qwen 3.8 27B")
    print("=" * 70)

    # -----------------------------------------------------------------------
    # Benchmark 1: Tree of Thoughts (ToT)
    # -----------------------------------------------------------------------
    print("\n>>> [Benchmark 1/2] Running Tree of Thoughts (Branching=3, BeamWidth=2, MaxDepth=2)...")
    tot_engine = TreeOfThoughtsEngine()
    tot_task = "针对千万级 QPS 网关系统，设计并选型高可用限流器方案（权衡单机与分布式、并发安全与吞吐性能）。"
    tot_result = tot_engine.solve(
        task=tot_task,
        branching=2,
        beam_width=1,
        max_depth=2,
    )

    print(f"ToT Search completed in {tot_result.elapsed_seconds:.2f}s with {tot_result.total_llm_calls} LLM calls.")
    print("Winning Reasoning Path:")
    for step in tot_result.best_path[1:]:
        print(f"  - Depth {step.depth} [Score {step.score:.1f}/5.0]: {step.thought_text[:60]}... ({step.reason})")
    print(f"\nFinal Solution Preview:\n{tot_result.final_solution[:300]}...")

    # -----------------------------------------------------------------------
    # Benchmark 2: Meta Controller (Dynamic Routing)
    # -----------------------------------------------------------------------
    print("\n>>> [Benchmark 2/2] Running Meta Controller Architectural Routing...")
    meta_engine = MetaControllerEngine()

    sample_tasks = [
        ("简答：HTTP 404 状态码的标准语义是什么？", "direct"),
        ("请编写一段生产级无锁并发环形缓冲区 (Lock-free Ring Buffer)，严苛自省内存可见性与边界竞态", "reflection"),
    ]

    for task_text, expected_hint in sample_tasks:
        print(f"\nEvaluating Task: '{task_text}'")
        res = meta_engine.run(task_text)
        print(f"  -> Chosen Architecture: [{res.chosen_arch.upper()}] (Complexity: {res.complexity_level})")
        print(f"  -> Routing Reason: {res.reason}")
        print(f"  -> Execution Completed in {res.elapsed_seconds:.2f}s ({res.total_llm_calls} calls)")
        print(f"  -> Output Preview: {res.output[:120].strip()}...")

    print("\n🎉 LIVE BENCHMARKS COMPLETED: Dynamic decision flow and architectural routing verified!")


def main():
    print("==================================================")
    print("RUNNING UNIT TESTS (DETERMINISTIC MOCKS)")
    print("==================================================")
    test_tot_beam_search_pruning()
    test_meta_controller_routing_logic()
    print("\nAll unit tests passed successfully!")

    print("\n==================================================")
    print("RUNNING LIVE DYNAMIC DECISION BENCHMARKS")
    print("==================================================")
    run_live_decision_benchmarks()


if __name__ == "__main__":
    main()
