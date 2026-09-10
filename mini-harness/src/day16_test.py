"""Day 16 Deterministic Test Suite for Query Loop & Agent Lifecycle State Machine.

Validates:
1. Turn monotonicity and state progression (Invariant 1).
2. Interrupt handling with Synthetic Tool Result ledger balancing (Invariant 2).
3. Pre-call Input Governance (Tool Result Budget truncation & Microcompact).
4. Multi-tier Output Truncation Recovery (RECOVER_CONTINUE meta-instruction).
5. End-to-end autonomous Query Loop execution with local model (google/gemma-4-12b-qat).
"""

from __future__ import annotations

import os
import sys

# Add src to path
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SRC_DIR)

from day16_query_loop import LoopTransition, QueryLoopEngine, QueryLoopState


def test_1_turn_monotonicity():
    """Verifies that turn_count progresses monotonically and detects time-travel regressions."""
    engine = QueryLoopEngine()
    state = QueryLoopState()

    prev_turn = state.turn_count
    # Advance step 1
    step1 = {"content": "Thinking...", "finish_reason": "stop", "tool_calls": []}
    t1 = engine.advance_turn(state, step1)
    assert t1 == LoopTransition.DONE
    assert state.turn_count == 1
    valid, errs = engine.verify_invariants(state, prev_turn)
    assert valid is True, f"Turn 1 should satisfy invariants: {errs}"

    # Verify time-travel detection
    valid_bad, errs_bad = engine.verify_invariants(state, previous_turn=5)
    assert valid_bad is False
    assert any("Turn count regression" in e for e in errs_bad)
    print("✅ Test 1 Passed: Turn monotonicity and regression detection verified.")


def test_2_synthetic_tool_result_ledger_balance():
    """Verifies that an interrupted tool call is synthesized to balance the causal ledger."""
    engine = QueryLoopEngine()
    state = QueryLoopState()

    # Step: Assistant emits 2 tool calls
    asst_msg = {
        "role": "assistant",
        "content": "Executing tools...",
        "tool_calls": [
            {"id": "call_abc_1", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}},
            {"id": "call_abc_2", "function": {"name": "run_bash", "arguments": '{"cmd": "pytest"}'}}
        ]
    }
    state.append_message(asst_msg)
    state.pending_tool_calls = [
        {"id": "call_abc_1", "function": {"name": "read_file"}},
        {"id": "call_abc_2", "function": {"name": "run_bash"}}
    ]

    # If we verify right now without draining, pending_tool_calls is populated so no leak yet
    # Now simulate User Abort (Ctrl+C)
    synthetic_count = engine.drain_tools_with_synthetic_results(state, reason="User pressed Ctrl+C")
    assert synthetic_count == 2
    assert state.is_interrupted is True
    assert len(state.pending_tool_calls) == 0

    # Invariant 2 Check: Every tool_use MUST have matching tool_result!
    valid, errs = engine.verify_invariants(state, previous_turn=0)
    assert valid is True, f"Synthetic drainage must balance ledger: {errs}"

    # Verify that removing one tool_result breaks Invariant 2
    state.messages.pop() # Remove one synthetic result
    valid_broken, errs_broken = engine.verify_invariants(state, previous_turn=0)
    assert valid_broken is False
    assert any("Unbalanced tool ledger" in e for e in errs_broken)
    print("✅ Test 2 Passed: Synthetic tool result ledger balancing verified under interrupt.")


def test_3_input_governance_budget_and_microcompact():
    """Verifies Tool Result Budget truncation and historical microcompact."""
    engine = QueryLoopEngine(tool_budget_bytes=500)
    state = QueryLoopState(tool_budget_bytes=500)

    # 1. Large tool output exceeds 500 bytes
    huge_output = "X" * 2000
    state.append_message({"role": "tool", "tool_call_id": "c1", "content": huge_output})

    gov_stats = engine.govern_input(state)
    assert gov_stats["budget_truncated"] == 1
    truncated_content = state.messages[0]["content"]
    assert len(truncated_content) < 700
    assert "Output truncated by Tool Result Budget" in truncated_content
    assert "1500 bytes snipped" in truncated_content

    # 2. Add 5 more messages so the first tool message becomes historical
    for i in range(5):
        state.append_message({"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"})

    # Add another tool message with 15 lines
    multi_line_output = "\n".join([f"line_{n}: log data" for n in range(15)])
    state.messages.insert(1, {"role": "tool", "tool_call_id": "c0", "content": multi_line_output})

    gov_stats_2 = engine.govern_input(state)
    assert gov_stats_2["microcompacted"] >= 1
    compacted_content = state.messages[1]["content"]
    assert "[Microcompacted Historical Tool Result]" in compacted_content
    assert "collapsed by microcompact" in compacted_content
    print("✅ Test 3 Passed: Pre-call input governance (Budget + Microcompact) verified.")


def test_4_output_truncation_recovery():
    """Verifies that token length cutoff triggers RECOVER_CONTINUE meta-instruction."""
    engine = QueryLoopEngine()
    state = QueryLoopState()

    # Step: Assistant truncated by token limit
    truncated_step = {
        "content": "def calculate_hash(data):\n    # unfinished...",
        "finish_reason": "length",
        "tool_calls": []
    }

    trans = engine.advance_turn(state, truncated_step)
    assert trans == LoopTransition.RECOVER_CONTINUE
    assert state.max_output_recovery_count == 1
    assert state.is_done is False

    # Check that Meta-Notice was appended
    last_msg = state.messages[-1]
    assert last_msg["role"] == "user"
    assert "Your output was cut off by token limit" in last_msg["content"]
    print("✅ Test 4 Passed: Output truncation recovery (RECOVER_CONTINUE) verified.")


def test_5_end_to_end_query_loop_with_model():
    """Runs a live end-to-end 2-turn query loop with local model using a mock tool."""
    engine = QueryLoopEngine(max_turns=4)
    state = QueryLoopState()

    tools = [
        {
            "type": "function",
            "function": {
                "name": "check_code_health",
                "description": "Checks code health of a Python module and returns diagnostic warnings.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "The file to inspect"}
                    },
                    "required": ["filename"]
                }
            }
        }
    ]

    def mock_dispatcher(name: str, args: dict) -> str:
        if name == "check_code_health":
            fname = args.get("filename", "unknown.py")
            return f"Health check for {fname}: Found 1 warning (unused variable 'temp_result' on line 42)."
        return "Unknown tool"

    system_prompt = (
        "You are an autonomous engineering agent.\n"
        "Your task: Check the health of 'server.py' using the check_code_health tool, "
        "and once you receive the diagnostic, conclude in 1 clear summary sentence."
    )

    state.append_message({
        "role": "user",
        "content": "Please inspect server.py with check_code_health and report the summary."
    })

    print("  [E2E Query Loop] Starting multi-turn execution...")
    step_count = 0

    while not state.is_done and step_count < 3:
        step_count += 1
        # 1. Govern input
        engine.govern_input(state)

        # 2. Call model
        step_res = engine.call_model_step(state, system_prompt, tools=tools, max_tokens=200)
        prev_turn = state.turn_count

        # 3. Advance turn
        trans = engine.advance_turn(state, step_res, tool_dispatcher=mock_dispatcher)
        print(f"  Turn {state.turn_count} -> Transition: {trans.value} | Content: {repr(step_res['content'][:60])}")

        # 4. Verify invariants
        valid, errs = engine.verify_invariants(state, prev_turn)
        assert valid is True, f"Invariants failed at turn {state.turn_count}: {errs}"

        if trans == LoopTransition.DONE:
            break

    assert state.turn_count >= 1
    assert any(t == LoopTransition.FOLLOW_UP or t == LoopTransition.DONE for t in state.transition_history)
    print(f"✅ Test 5 Passed: End-to-end Query Loop completed in {state.turn_count} turns with valid invariant ledger.")


def main():
    print("="*65)
    print(" 🚀 RUNNING DAY 16 QUERY LOOP & HEARTBEAT TEST SUITE")
    print("="*65)
    test_1_turn_monotonicity()
    test_2_synthetic_tool_result_ledger_balance()
    test_3_input_governance_budget_and_microcompact()
    test_4_output_truncation_recovery()
    test_5_end_to_end_query_loop_with_model()
    print("\n🎉 ALL 5 DAY 16 QUERY LOOP TESTS PASSED WITH 100% SUCCESS!")


if __name__ == "__main__":
    main()
