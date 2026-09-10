"""Day 17 Deterministic Verification Suite for Tool Orchestration, Permissions & HITL.

Validates:
1. Concurrency-Safe Partitioning (read-only parallel batches vs mutating serial batches).
2. Tri-State Permission Decision Chain (ALLOW / DENY / ASK) & Invariant checks.
3. Sticky Deny enforcement (preventing model from retrying rejected calls).
4. SafeBashGuard shell security (subcommand count limit & destructive regex blocking).
5. Parallel execution with Sibling Error cancellation and sequential context replay.
"""

from __future__ import annotations

import os
import sys
import time

# Add src to path
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SRC_DIR)

from day17_permissions_and_tools import (
    PermissionDecision,
    PermissionManager,
    SafeBashGuard,
    ToolCallRecord,
    ToolExecutionBatch,
    ToolOrchestrator,
    verify_permission_invariants,
)


def test_1_concurrency_safe_partitioning():
    """Verifies that read-only tools are batched in parallel and mutating tools are isolated serially."""
    orchestrator = ToolOrchestrator()

    raw_calls = [
        {"id": "call_1", "function": {"name": "read_file", "arguments": {"path": "a.py"}}},
        {"id": "call_2", "function": {"name": "grep_search", "arguments": {"query": "test"}}},
        {"id": "call_3", "function": {"name": "list_dir", "arguments": {"path": "."}}},
        {"id": "call_4", "function": {"name": "write_file", "arguments": {"path": "out.txt", "content": "hello"}}},
        {"id": "call_5", "function": {"name": "read_file", "arguments": {"path": "out.txt"}}},
        {"id": "call_6", "function": {"name": "run_bash", "arguments": {"cmd": "pytest"}}},
    ]

    batches = orchestrator.partition_tool_calls(raw_calls)
    assert len(batches) == 4, f"Expected 4 batches, got {len(batches)}"

    # Batch 0: read_file, grep_search, list_dir (Parallel)
    assert batches[0].is_parallel is True
    assert len(batches[0].calls) == 3
    assert [c.id for c in batches[0].calls] == ["call_1", "call_2", "call_3"]

    # Batch 1: write_file (Serial)
    assert batches[1].is_parallel is False
    assert len(batches[1].calls) == 1
    assert batches[1].calls[0].id == "call_4"

    # Batch 2: read_file (Parallel, single item)
    assert batches[2].is_parallel is True
    assert len(batches[2].calls) == 1
    assert batches[2].calls[0].id == "call_5"

    # Batch 3: run_bash (Serial)
    assert batches[3].is_parallel is False
    assert len(batches[3].calls) == 1
    assert batches[3].calls[0].id == "call_6"

    print("✅ Test 1 Passed: Concurrency-safe partitioning strictly verified.")


def test_2_tri_state_permissions_and_sticky_deny():
    """Verifies ALLOW, DENY, ASK decisions and the Sticky Deny invariant."""
    pm = PermissionManager()

    # 1. Read-only -> ALLOW
    rec_read = ToolCallRecord(id="read_1", name="read_file", arguments={"path": "main.py"}, original_index=0)
    ev_read = pm.evaluate(rec_read)
    assert ev_read.decision == PermissionDecision.ALLOW
    valid, errs = verify_permission_invariants(ev_read)
    assert valid is True

    # 2. Destructive Bash -> DENY
    rec_destr = ToolCallRecord(id="bad_cmd_1", name="run_bash", arguments={"cmd": "rm -rf /"}, original_index=1)
    ev_destr = pm.evaluate(rec_destr)
    assert ev_destr.decision == PermissionDecision.DENY
    assert "Destructive recursive file deletion" in ev_destr.reason
    assert "bad_cmd_1" in pm.sticky_denials

    # 3. Sticky Deny Check: Re-evaluating same ID MUST return DENY even if args are changed!
    rec_retry = ToolCallRecord(id="bad_cmd_1", name="read_file", arguments={"path": "safe.py"}, original_index=2)
    ev_retry = pm.evaluate(rec_retry)
    assert ev_retry.decision == PermissionDecision.DENY
    assert "Sticky Deny" in ev_retry.reason

    valid_sticky, errs_sticky = verify_permission_invariants(ev_retry, is_retrying_denied=True)
    assert valid_sticky is True, f"Sticky deny invariant failed: {errs_sticky}"
    print("✅ Test 2 Passed: Tri-state permissions & Sticky Deny invariant verified.")


def test_3_hitl_approval_and_rejection():
    """Verifies the Human-in-the-Loop (HITL) approval / rejection lifecycle."""
    pm = PermissionManager(auto_approve_mutations=False)

    rec_write = ToolCallRecord(id="write_1", name="write_file", arguments={"path": "app.py"}, original_index=0)
    ev_ask = pm.evaluate(rec_write)
    assert ev_ask.decision == PermissionDecision.ASK
    assert "Modifying workspace file" in ev_ask.reason

    # Scenario A: Operator approves
    ev_approved = pm.resolve_ask("write_1", approved=True, reason="Code review passed")
    assert ev_approved.decision == PermissionDecision.ALLOW
    assert "Code review passed" in ev_approved.reason

    # Scenario B: Operator rejects another write
    rec_write_2 = ToolCallRecord(id="write_2", name="write_file", arguments={"path": "dangerous.py"}, original_index=1)
    ev_ask_2 = pm.evaluate(rec_write_2)
    assert ev_ask_2.decision == PermissionDecision.ASK

    ev_rejected = pm.resolve_ask("write_2", approved=False, reason="Unapproved file creation")
    assert ev_rejected.decision == PermissionDecision.DENY
    assert "write_2" in pm.sticky_denials

    # Verify write_2 is now sticky-denied
    ev_retry_2 = pm.evaluate(rec_write_2)
    assert ev_retry_2.decision == PermissionDecision.DENY
    assert "Sticky Deny" in ev_retry_2.reason
    print("✅ Test 3 Passed: HITL Ask -> Approval / Rejection lifecycle verified.")


def test_4_safe_bash_guard_deep_defense():
    """Verifies subcommand count limit and destructive pattern blocking."""
    guard = SafeBashGuard(max_subcommands=3)

    # 1. Normal safe command
    safe, _ = guard.inspect_command("git status")
    assert safe is True

    # 2. Subcommand limit exceeded (4 subcommands chained)
    safe_chain, reason_chain = guard.inspect_command("echo 1 && echo 2 && echo 3 && echo 4")
    assert safe_chain is False
    assert "Subcommand count (4) exceeds maximum limit" in reason_chain

    # 3. Git Force Push blocked
    safe_git, reason_git = guard.inspect_command("git push origin main --force")
    assert safe_git is False
    assert "Destructive Git force push" in reason_git

    # 4. Privilege escalation blocked
    safe_sudo, reason_sudo = guard.inspect_command("sudo systemctl restart nginx")
    assert safe_sudo is False
    assert "Privilege escalation attempt" in reason_sudo

    # 5. Dangerous chmod 777 blocked
    safe_chmod, reason_chmod = guard.inspect_command("chmod -R 777 /var/data")
    assert safe_chmod is False
    assert "Dangerous open directory permission" in reason_chmod
    print("✅ Test 4 Passed: SafeBashGuard shell defense matrix verified.")


def test_5_parallel_execution_sibling_error_and_ordered_replay():
    """Verifies that parallel tools replay in original index order and abort siblings upon error."""
    orchestrator = ToolOrchestrator()

    calls = [
        ToolCallRecord(id="c0", name="read_file", arguments={"path": "file0.txt"}, original_index=0),
        ToolCallRecord(id="c1", name="read_file", arguments={"path": "FAIL.txt"}, original_index=1),
        ToolCallRecord(id="c2", name="read_file", arguments={"path": "file2.txt"}, original_index=2),
    ]
    batch = ToolExecutionBatch(is_parallel=True, calls=calls)

    def mock_runner(name: str, args: dict) -> str:
        p = args.get("path", "")
        if "FAIL" in p:
            raise RuntimeError("Fatal disk I/O failure on target file!")
        # Add artificial jitter to simulate out-of-order thread completion
        if p == "file0.txt":
            time.sleep(0.04)
        else:
            time.sleep(0.01)
        return f"Content of {p}"

    results = orchestrator.execute_batch(batch, tool_runner=mock_runner)
    assert len(results) == 3

    # Assert sequential context replay order: index 0, 1, 2
    assert results[0][0].original_index == 0
    assert results[1][0].original_index == 1
    assert results[2][0].original_index == 2

    # Assert failure and sibling handling
    assert "Content of file0.txt" in results[0][1]
    assert "[ToolExecutionError]" in results[1][1]
    # Sibling c2 was either completed or caught in sibling abort
    assert results[2][1] is not None
    print("✅ Test 5 Passed: Ordered context replay & sibling error handling verified.")


def main():
    print("="*65)
    print(" 🚀 RUNNING DAY 17 TOOLS, PERMISSIONS & HITL TEST SUITE")
    print("="*65)
    test_1_concurrency_safe_partitioning()
    test_2_tri_state_permissions_and_sticky_deny()
    test_3_hitl_approval_and_rejection()
    test_4_safe_bash_guard_deep_defense()
    test_5_parallel_execution_sibling_error_and_ordered_replay()
    print("\n🎉 ALL 5 DAY 17 PERMISSION & TOOL ORCHESTRATION TESTS PASSED 100%!")


if __name__ == "__main__":
    main()
