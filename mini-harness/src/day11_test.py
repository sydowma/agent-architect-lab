"""Integration and verification test suite for Day 11 Hierarchical Memory.

Verifies:
1. Unit tests: GraphMemory N-hop BFS traversal, loop defense, EpisodicMemory token similarity.
2. Live integration test against local Qwen 3.8 27B:
   - Multi-turn interaction with memory accumulation
   - Automatic triple extraction
   - Multi-hop relational query solving
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Ensure src is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

from day11_hierarchical_memory import (
    EpisodicMemory,
    GraphMemory,
    HierarchicalMemoryAgent,
    Triple,
    normalize_token,
)


# ===========================================================================
# 1. Deterministic Unit Tests
# ===========================================================================
def test_graph_memory_traversal_and_cycle_resilience():
    print("\n--- [Unit Test 1] GraphMemory N-hop BFS & Cycle Defense ---")
    graph = GraphMemory()

    # Build multi-hop graph:
    # mark -> develops -> mini_harness
    # mini_harness -> reviewed_by -> alice
    # alice -> works_at -> deepmind
    # alice -> collaborates_with -> mark  (creates cycle!)
    graph.add_triple("mark", "develops", "mini_harness")
    graph.add_triple("mini_harness", "reviewed_by", "alice")
    graph.add_triple("alice", "works_at", "deepmind")
    graph.add_triple("alice", "collaborates_with", "mark")  # Cycle

    # 1-hop traversal from mark
    hop1 = graph.traverse_n_hop(["mark"], max_depth=1)
    subjects_hop1 = {(t.subject, t.predicate, t.object) for t in hop1}
    assert ("mark", "develops", "mini_harness") in subjects_hop1, "1-hop must reach mini_harness"
    print(f"PASS: 1-hop traversal retrieved {len(hop1)} triples.")

    # 2-hop traversal from mark (should reach alice)
    hop2 = graph.traverse_n_hop(["mark"], max_depth=2)
    subjects_hop2 = {(t.subject, t.predicate, t.object) for t in hop2}
    assert ("mini_harness", "reviewed_by", "alice") in subjects_hop2, "2-hop must reach alice"
    print(f"PASS: 2-hop traversal retrieved {len(hop2)} triples.")

    # 3-hop traversal from mark (should reach deepmind)
    hop3 = graph.traverse_n_hop(["mark"], max_depth=3)
    subjects_hop3 = {(t.subject, t.predicate, t.object) for t in hop3}
    assert ("alice", "works_at", "deepmind") in subjects_hop3, "3-hop must reach deepmind"
    print(f"PASS: 3-hop traversal retrieved {len(hop3)} triples without infinite cycle.")


def test_episodic_memory_similarity_search():
    print("\n--- [Unit Test 2] EpisodicMemory Semantic Recall ---")
    ep = EpisodicMemory()

    ep.add_episode(
        user_query="How do I configure Redis inside a Docker container?",
        agent_response="Use redis.conf with bind 0.0.0.0 and port 6379 in your docker-compose file.",
    )
    ep.add_episode(
        user_query="Tell me a funny joke about cats.",
        agent_response="Why was the cat sitting on the computer? To keep an eye on the mouse!",
    )

    # Query matching episode 1
    results = ep.search_similar("Docker Redis deployment settings", top_k=1)
    assert len(results) == 1, "Should find at least 1 match"
    matched_ep, score = results[0]
    assert matched_ep.episode_id == 1, f"Expected episode 1, got {matched_ep.episode_id}"
    print(f"PASS: Episodic similarity correctly recalled episode #{matched_ep.episode_id} with score {score:.3f}.")


def test_agent_mock_flow():
    print("\n--- [Unit Test 3] HierarchicalMemoryAgent Mocked Chat Cycle ---")
    mock_responses = [
        # Answer
        "Mark is developing mini_harness.",
        # Triple extraction
        json.dumps({"triples": [{"subject": "mark", "predicate": "develops", "object": "mini_harness"}]}),
    ]

    def mock_llm(messages, **kwargs):
        return mock_responses.pop(0)

    agent = HierarchicalMemoryAgent(llm_fn=mock_llm)
    res = agent.chat("I am Mark and I am building mini_harness.")

    assert len(res["new_triples"]) == 1
    assert res["new_triples"][0].subject == "mark"
    assert res["new_triples"][0].object == "mini_harness"
    print(f"PASS: Full chat cycle executed, ingested triple: {res['new_triples'][0].to_readable()}")


# ===========================================================================
# 2. Live Integration Test against Local LM Studio (Qwen 3.8 27B)
# ===========================================================================
def run_live_memory_benchmark():
    print("\n" + "=" * 70)
    print("🚀 Running Live Hierarchical Memory Test on Local Qwen 3.8 27B")
    print("=" * 70)

    agent = HierarchicalMemoryAgent()

    # Turn 1: Ingest background
    print("\n>>> [Turn 1] User inputs project context...")
    t1 = agent.chat("我是架构师 Mark，我负责主导研发 mini_harness 自动化 Agent 框架，它依赖 Python_314。")
    print(f"Assistant: {t1['answer']}")
    print(f"Ingested Triples ({len(t1['new_triples'])}): {[t.to_readable() for t in t1['new_triples']]}")

    # Turn 2: Ingest organization relationship
    print("\n>>> [Turn 2] User inputs personnel relationship...")
    t2 = agent.chat("mini_harness 项目的核心代码审阅人是 Alice，Alice 所在的机构是 DeepMind。")
    print(f"Assistant: {t2['answer']}")
    print(f"Ingested Triples ({len(t2['new_triples'])}): {[t.to_readable() for t in t2['new_triples']]}")

    # Turn 3: Multi-hop question
    print("\n>>> [Turn 3] User asks multi-hop question requiring graph reasoning...")
    t3 = agent.chat("Mark 负责的项目由谁来进行代码审阅？审阅人所在的机构是哪家？")
    print(f"Assistant: {t3['answer']}")
    print(f"Retrieved Graph Triples ({len(t3['retrieved_triples'])}): {[t.to_readable() for t in t3['retrieved_triples']]}")
    print(f"Retrieved Episodic Past IDs: {t3['retrieved_episodes']}")

    # Verification:
    # The answer should mention Alice and DeepMind accurately based strictly on memory
    ans_lower = t3["answer"].lower()
    assert "alice" in ans_lower, "Answer must identify Alice as reviewer"
    assert "deepmind" in ans_lower, "Answer must identify DeepMind as organization"
    print("\n🎉 LIVE VERIFICATION SUCCESSFUL: Agent accurately deduced multi-hop relations from graph memory!")


def main():
    print("==================================================")
    print("RUNNING UNIT TESTS (DETERMINISTIC MOCKS)")
    print("==================================================")
    test_graph_memory_traversal_and_cycle_resilience();
    test_episodic_memory_similarity_search();
    test_agent_mock_flow();
    print("\nAll unit tests passed successfully!")

    print("\n==================================================")
    print("RUNNING LIVE MEMORY INTEGRATION TEST")
    print("==================================================")
    run_live_memory_benchmark()


if __name__ == "__main__":
    main()
