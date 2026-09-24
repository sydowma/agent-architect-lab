"""Day 24 Unit & Integration Test Suite (Refactored).

Verifies:
1. Skill fingerprinted idempotency, scope precedence, and SkillDirectoryLoader disk parsing.
2. SkillRuntime on-demand <invoked_skill> context injection.
3. HookEngine lifecycle events, preview vs run separation, and blocking Pre-Tool Hooks.
4. DelegationEngine spawn/send/wait/close, cascade cancellation, and DelegationLedger.
5. GovernedAgentRuntime end-to-end governed turn execution.
"""

import os
import tempfile
import unittest

from day24_skills_and_delegation import (
    AgentStatus,
    DanglingHandleError,
    DelegationEngine,
    DelegationProtocolError,
    GovernedAgentRuntime,
    HookConfigError,
    HookContext,
    HookEngine,
    HookEvent,
    HookExecutionBlockedError,
    HookHandler,
    HookSkipReason,
    InstallAction,
    Skill,
    SkillDirectoryLoader,
    SkillNotFoundError,
    SkillRegistry,
    SkillRuntime,
    SkillSource,
)


class TestDay24SkillsAndDelegation(unittest.TestCase):

    # ========================================================================
    # 1. 技能管理与指纹测试 (Skills & Directory Loader)
    # ========================================================================

    def test_01_skill_fingerprint_install_and_skip(self):
        """Unchanged skills skip reinstall; changed content triggers reinstall."""
        registry = SkillRegistry()
        s1 = Skill("lint-rule", "content v1", version="1.0.0", source=SkillSource.PROJECT)
        rec1 = registry.install(s1)
        self.assertEqual(rec1.action, InstallAction.INSTALLED)

        # Same skill again: identical fingerprint skips
        rec2 = registry.install(s1)
        self.assertEqual(rec2.action, InstallAction.SKIPPED_FINGERPRINT_MATCH)

        # Content changed: fingerprint mismatch triggers reinstallation
        s1_mod = Skill("lint-rule", "content v2 modified", version="1.0.1", source=SkillSource.PROJECT)
        rec3 = registry.install(s1_mod)
        self.assertEqual(rec3.action, InstallAction.REINSTALLED)

    def test_02_skill_source_precedence(self):
        """Closer governance scope wins: project > user > system."""
        registry = SkillRegistry()
        sys_skill = Skill("test-runner", "system runner", source=SkillSource.SYSTEM)
        user_skill = Skill("test-runner", "user runner", source=SkillSource.USER)
        proj_skill = Skill("test-runner", "project runner", source=SkillSource.PROJECT)

        registry.install(sys_skill)
        self.assertEqual(registry.get("test-runner").content, "system runner")

        # User overrides system
        registry.install(user_skill)
        self.assertEqual(registry.get("test-runner").content, "user runner")

        # Project overrides user
        registry.install(proj_skill)
        self.assertEqual(registry.get("test-runner").content, "project runner")

        # Weaker system cannot shadow existing project
        rec = registry.install(sys_skill)
        self.assertEqual(rec.action, InstallAction.SKIPPED_FINGERPRINT_MATCH)
        self.assertEqual(registry.get("test-runner").content, "project runner")

    def test_03_skill_directory_loader(self):
        """SkillDirectoryLoader parses SKILL.md files and YAML frontmatter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_path = os.path.join(tmpdir, "SKILL.md")
            with open(skill_path, "w", encoding="utf-8") as f:
                f.write("""---
name: "git-hygiene"
version: "2.1.0"
description: "Commit discipline rules"
---
# Rules
Always write Conventional Commits.
""")
            skill = SkillDirectoryLoader.load_skill_file(skill_path, source=SkillSource.PROJECT)
            self.assertEqual(skill.name, "git-hygiene")
            self.assertEqual(skill.version, "2.1.0")
            self.assertEqual(skill.description, "Commit discipline rules")
            self.assertIn("Conventional Commits", skill.content)

    def test_04_skill_runtime_on_demand_injection(self):
        """Skills mount into the live context without mutating the installed registry."""
        registry = SkillRegistry()
        registry.install(Skill("security-gate", "Never log plain tokens", source=SkillSource.PROJECT))
        runtime = SkillRuntime(registry)

        attachment = runtime.activate("security-gate", reason="Database query detected")
        self.assertEqual(attachment["role"], "user")
        self.assertIn('<invoked_skill name="security-gate"', attachment["content"])
        self.assertIn("Never log plain tokens", attachment["content"])

        with self.assertRaises(SkillNotFoundError):
            runtime.activate("unknown-skill")

    # ========================================================================
    # 2. Hook 生命周期与阻断门禁测试 (HookEngine)
    # ========================================================================

    def test_05_hook_preview_never_executes(self):
        """preview_* lists candidates with zero side-effects; only run_* executes."""
        engine = HookEngine()
        executed = []

        handler = HookHandler(
            name="audit-logger",
            event_name=HookEvent.PRE_TOOL_USE,
            command=lambda ctx: executed.append(ctx.tool_name),
            source_path="hooks/audit.py",
        )
        engine.register(handler)

        ctx = HookContext(thread_id="t1", event=HookEvent.PRE_TOOL_USE, tool_name="bash")
        previews = engine.preview_event(ctx)
        self.assertEqual(len(previews), 1)
        self.assertTrue(previews[0].preview_only)
        self.assertEqual(len(executed), 0)  # zero side effect!

        # Now run
        engine.run_event(ctx)
        self.assertEqual(len(executed), 1)
        self.assertEqual(executed[0], "bash")

    def test_06_hook_blocking_pre_tool_hook(self):
        """A blocking pre_tool_use hook stops dangerous tool calls before execution."""
        engine = HookEngine()

        def block_dangerous_drop(ctx: HookContext):
            if "drop" in ctx.payload.get("cmd", "").lower():
                raise HookExecutionBlockedError("Forbidden SQL: DROP TABLE is banned in this repo")
            return "OK"

        blocker = HookHandler(
            name="sql-guard",
            event_name=HookEvent.PRE_TOOL_USE,
            command=block_dangerous_drop,
            can_block=True,
            source_path="rules/sql.py",
        )
        engine.register(blocker)

        # Dangerous command triggers block
        ctx_danger = HookContext(
            thread_id="t1",
            event=HookEvent.PRE_TOOL_USE,
            tool_name="sql_exec",
            payload={"cmd": "DROP TABLE users;"},
        )
        records = engine.run_event(ctx_danger)
        self.assertTrue(records[0].blocked)
        self.assertIn("DROP TABLE is banned", records[0].block_reason)

    def test_07_hook_platform_gating_is_explainable(self):
        """Hooks disable loudly with explanation on Windows."""
        win_engine = HookEngine(platform="windows")
        self.assertFalse(win_engine.enabled)
        self.assertTrue(any("disabled on windows" in w for w in win_engine.warnings))

    # ========================================================================
    # 3. 委派协议与账本留痕测试 (DelegationEngine & DelegationLedger)
    # ========================================================================

    def test_08_delegation_spawn_send_wait(self):
        """Delegation primitives: spawn, send_input (interrupt & queue), and wait."""
        engine = DelegationEngine()
        handle_id = engine.spawn_agent(role="researcher", prompt="Scan auth module")
        self.assertEqual(engine.active_handles_count(), 1)

        # Send input
        engine.send_input(handle_id, "Check JWT verification logic", interrupt=False)
        # Wait
        status, out = engine.wait_agent(handle_id)
        self.assertEqual(status, AgentStatus.COMPLETED)
        self.assertIn("Check JWT verification logic", out)

        # Close
        engine.close_agent(handle_id)
        self.assertEqual(engine.active_handles_count(), 0)

    def test_09_delegation_cascade_abort_and_ledger(self):
        """Parent abort cascades to all descendants and records immutable ledger events."""
        engine = DelegationEngine()
        parent_id = engine.spawn_agent(role="lead", prompt="Lead task")
        child_id = engine.spawn_agent(role="worker", prompt="Worker task", parent_handle_id=parent_id)

        self.assertEqual(engine.active_handles_count(), 2)

        # Parent abort cascades
        engine.abort_parent(parent_id)
        self.assertEqual(engine.active_handles_count(), 0)

        # Check ledger evidence
        self.assertTrue(engine.ledger.is_action_logged(parent_id, "ABORT"))
        self.assertTrue(engine.ledger.is_action_logged(child_id, "ABORT"))

    def test_10_delegation_dangling_handle_error(self):
        """assert_no_dangling_handles raises DanglingHandleError if active handles leak."""
        engine = DelegationEngine()
        engine.spawn_agent(role="leaked_worker", prompt="Unclosed task")

        with self.assertRaises(DanglingHandleError):
            engine.assert_no_dangling_handles()

    # ========================================================================
    # 4. 统一治理运行时测试 (GovernedAgentRuntime)
    # ========================================================================

    def test_11_governed_agent_runtime_full_turn(self):
        """GovernedAgentRuntime connects skills, blocking hooks, and tool dispatch."""
        registry = SkillRegistry()
        registry.install(Skill("clean-code", "Follow PEP 8", source=SkillSource.PROJECT))

        hook_engine = HookEngine()

        def rm_guard(ctx: HookContext):
            if "rm -rf" in ctx.payload.get("cmd", ""):
                raise HookExecutionBlockedError("Forbidden destructive deletion")
            return "OK"

        hook_engine.register(
            HookHandler(
                name="rm-blocker",
                event_name=HookEvent.PRE_TOOL_USE,
                command=rm_guard,
                can_block=True,
                source_path="guards/rm.py",
            )
        )

        runtime = GovernedAgentRuntime(registry=registry, hook_engine=hook_engine)

        # Turn 1: Normal execution with skill attachment
        res1 = runtime.run_governed_turn(
            thread_id="th_1",
            user_prompt="Format codebase",
            tool_name="bash",
            tool_args={"cmd": "black ."},
            tool_callable=lambda: "All done! 5 files reformatted.",
            activate_skill_name="clean-code",
        )
        self.assertFalse(res1["tool_blocked"])
        self.assertEqual(res1["tool_output"], "All done! 5 files reformatted.")
        self.assertIn("Follow PEP 8", res1["skill_attachment"]["content"])

        # Turn 2: Dangerous command blocked by hook
        res2 = runtime.run_governed_turn(
            thread_id="th_1",
            user_prompt="Delete all files",
            tool_name="bash",
            tool_args={"cmd": "rm -rf /"},
            tool_callable=lambda: "DELETED",  # Should never be called!
        )
        self.assertTrue(res2["tool_blocked"])
        self.assertIn("Forbidden destructive deletion", res2["tool_output"])

        # Session close verifies invariants cleanly
        runtime.close_session("th_1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
