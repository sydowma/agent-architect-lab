"""Unit tests for Day 22: Dual Control Planes - Claude Code vs Codex."""

import unittest
from typing import Any, Dict, List

from day22_dual_control_planes import (
    ClaudeCodeLayers,
    ContextualUserFragment,
    ControlPlaneInvariantEnforcer,
    DirectoryScopedHierarchyManager,
    DualControlPlaneAnalyzer,
    DynamicAssemblyControlPlane,
    FragmentType,
    InvariantViolationError,
    MarkerConstants,
)


class TestDay22DualControlPlanes(unittest.TestCase):
    """Deterministic test suite validating Book 2 Ch 1 & 2 principles."""

    def test_01_claude_code_dynamic_assembly_and_precedence(self):
        """Verify Claude Code layered assembly and immutable system prompt prefix."""
        layers = ClaudeCodeLayers(
            default_prompt="DEFAULT: Model is unstable.",
            team_claudemd="TEAM: Run lint before commit.",
            personal_claudemd="USER: Preferred language Python 3.12.",
            project_claudemd="PROJECT: Strictly preserve concurrency safety.",
        )
        plane = DynamicAssemblyControlPlane(layers)
        system_prompt = plane.assemble_static_system_prompt()

        # Check all layers present
        self.assertIn("DEFAULT: Model is unstable.", system_prompt)
        self.assertIn("[TEAM RULES]\nTEAM: Run lint before commit.", system_prompt)
        self.assertIn("[USER PREFERENCES]\nUSER: Preferred language Python 3.12.", system_prompt)
        self.assertIn("[PROJECT INVARIANTS]\nPROJECT: Strictly preserve concurrency safety.", system_prompt)

        # Dynamic reminder injected at tip
        reminder = plane.inject_stream_tip_reminder("dirty", "src/cache.py")
        self.assertEqual(reminder["type"], "system_reminder")
        self.assertIn("src/cache.py", reminder["content"])
        self.assertIn("<system-reminder>", reminder["content"])

        # Invariant: Static prompt remains completely immutable
        self.assertEqual(system_prompt, plane.assemble_static_system_prompt())

    def test_02_codex_typed_fragment_wrapping_and_markers(self):
        """Verify Codex contextual user fragments with explicit START/END markers and metadata."""
        frag = ContextualUserFragment(
            fragment_type=FragmentType.AGENTS_MD,
            content="Strictly avoid editing auth token config.",
            source_dir="/workspace/backend",
            path="/workspace/backend/AGENTS.md",
        )

        wrapped = frag.wrap()
        # Markers check
        self.assertTrue(wrapped.startswith(MarkerConstants.AGENTS_MD_START))
        self.assertTrue(wrapped.endswith(MarkerConstants.AGENTS_MD_END))
        self.assertIn("# AGENTS_MD instructions for /workspace/backend", wrapped)
        self.assertIn("Strictly avoid editing auth token config.", wrapped)

        # into_message check
        msg = frag.into_message()
        self.assertEqual(msg["type"], "contextual_fragment")
        self.assertEqual(msg["fragment_type"], "AGENTS_MD")
        self.assertEqual(msg["source_dir"], "/workspace/backend")
        self.assertEqual(msg["path"], "/workspace/backend/AGENTS.md")

    def test_03_codex_directory_scoped_hierarchy_inheritance(self):
        """Verify Codex AGENTS.md directory hierarchy resolution and depth precedence."""
        manager = DirectoryScopedHierarchyManager(enable_child_agents_md=True)
        manager.register_agents_md("/repo", "Repo-wide team standards.")
        manager.register_agents_md("/repo/backend", "Backend microservice standards.")

        resolved = manager.resolve_hierarchy_fragments("/repo/backend")
        
        # Expected: root fragment, backend fragment, and inheritance notice
        self.assertEqual(len(resolved), 3)

        root_frag = resolved[0]
        backend_frag = resolved[1]
        notice_frag = resolved[2]

        self.assertEqual(root_frag.source_dir, "/repo")
        self.assertEqual(backend_frag.source_dir, "/repo/backend")
        self.assertLess(root_frag.precedence_level, backend_frag.precedence_level)
        self.assertLess(backend_frag.precedence_level, notice_frag.precedence_level)

        self.assertIn("[INHERITANCE NOTICE]", notice_frag.content)

    def test_04_invariant_enforcer_marker_asymmetry_and_monotonicity(self):
        """Invariant checks: marker symmetry and precedence monotonicity."""
        # 1. Asymmetric marker failure
        bad_wrapped_text = f"{MarkerConstants.AGENTS_MD_START}\nRules without ending marker"
        with self.assertRaises(InvariantViolationError) as ctx:
            ControlPlaneInvariantEnforcer.verify_fragment_symmetry(bad_wrapped_text, FragmentType.AGENTS_MD)
        self.assertIn("Marker Asymmetry Violation", str(ctx.exception))

        # 2. Symmetric marker pass
        good_wrapped = f"{MarkerConstants.AGENTS_MD_START}\nRules\n{MarkerConstants.AGENTS_MD_END}"
        ControlPlaneInvariantEnforcer.verify_fragment_symmetry(good_wrapped, FragmentType.AGENTS_MD)

        # 3. Monotonicity violation
        f1 = ContextualUserFragment(FragmentType.AGENTS_MD, "A", "/r", precedence_level=50)
        f2 = ContextualUserFragment(FragmentType.AGENTS_MD, "B", "/r/sub", precedence_level=20)  # Dropped!
        with self.assertRaises(InvariantViolationError) as ctx_mono:
            ControlPlaneInvariantEnforcer.verify_precedence_monotonicity([f1, f2])
        self.assertIn("Precedence Monotonicity Violation", str(ctx_mono.exception))

    def test_05_dual_control_plane_side_by_side_comparison(self):
        """Compare both control planes on identical input rules."""
        result = DualControlPlaneAnalyzer.compare(
            default_rule="Base principle: maintain test suite.",
            team_rule="Team standard: git branch naming feature/*",
            project_rule="Backend rule: wrap db query in transactions.",
            workdir="/repo/backend",
        )

        # Claude Code side
        claude_res = result["claude_code"]
        self.assertEqual(claude_res["paradigm"], "Dynamic Assembly Line")
        self.assertGreater(claude_res["system_prompt_length"], 50)
        self.assertEqual(claude_res["stream_tip_reminder"]["type"], "system_reminder")

        # Codex side
        codex_res = result["codex"]
        self.assertEqual(codex_res["paradigm"], "Structured Document Office")
        self.assertGreaterEqual(codex_res["fragment_count"], 2)
        self.assertTrue(codex_res["has_scope_declaration"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
