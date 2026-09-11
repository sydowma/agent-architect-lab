"""Day 22: Dual Control Planes - Claude Code Dynamic Assembly vs Codex Structured Fragments.

Faithfully reproduces architectural comparisons from Harness Books Book 2 Ch 1 & 2:
1. Dynamic Assembly Line (Claude Code: layered prompts, system prompt recomputation, KV cache tip reminder).
2. Structured Contextual Fragments (Codex: typed fragments, explicit START/END markers, directory metadata).
3. Directory-Scoped Hierarchy (AGENTS.md inheritance, closest directory overrides, child_agents_md scope).
4. Control Plane Invariant Enforcer (Marker symmetry, type verification, precedence monotonicity).
5. Dual Control Plane Comparative Analyzer.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple


# ============================================================================
# 1. Claude Code 风格：动态装配线 (Dynamic Assembly Control Plane)
# ============================================================================

@dataclass
class ClaudeCodeLayers:
    default_prompt: str = "DEFAULT CONSTITUTION: You are a principal software engineer."
    append_prompt: str = ""
    agent_prompt: str = ""
    team_claudemd: str = ""
    personal_claudemd: str = ""
    project_claudemd: str = ""
    memory_sections: str = ""
    output_style: str = "STYLE: Direct, concise, no conversational filler."


class DynamicAssemblyControlPlane:
    """Claude Code archetype: Prompt as an assembled, recomputed control plane.
    
    Layers are assembled in strict precedence:
    Default -> Team -> Personal -> Project -> Agent -> Append -> Memory -> OutputStyle.
    """

    def __init__(self, layers: Optional[ClaudeCodeLayers] = None):
        self.layers = layers or ClaudeCodeLayers()

    def assemble_static_system_prompt(self) -> str:
        """Assembles immutable prompt prefix preserving KV Cache."""
        parts = [self.layers.default_prompt]

        # Claudemd layers in order of increasing proximity/precedence
        if self.layers.team_claudemd.strip():
            parts.append(f"[TEAM RULES]\n{self.layers.team_claudemd.strip()}")
        if self.layers.personal_claudemd.strip():
            parts.append(f"[USER PREFERENCES]\n{self.layers.personal_claudemd.strip()}")
        if self.layers.project_claudemd.strip():
            parts.append(f"[PROJECT INVARIANTS]\n{self.layers.project_claudemd.strip()}")

        if self.layers.agent_prompt.strip():
            parts.append(f"[ROLE DEFINITION]\n{self.layers.agent_prompt.strip()}")
        if self.layers.append_prompt.strip():
            parts.append(f"[APPENDED RULES]\n{self.layers.append_prompt.strip()}")
        if self.layers.output_style.strip():
            parts.append(f"[OUTPUT STYLE]\n{self.layers.output_style.strip()}")

        return "\n\n".join(parts)

    def inject_stream_tip_reminder(self, git_status: str, active_file: str) -> Dict[str, Any]:
        """Dynamic working memory injected strictly at stream tip (<system-reminder>)."""
        content = (
            f"<system-reminder>\n"
            f"Active File: {active_file}\n"
            f"Working Tree: {git_status}\n"
            f"</system-reminder>"
        )
        return {"role": "user", "type": "system_reminder", "content": content}


# ============================================================================
# 2. Codex 风格：结构化公文片段 (Typed Contextual Fragments)
# ============================================================================

class FragmentType(Enum):
    AGENTS_MD = "AGENTS_MD"
    SKILL = "SKILL"
    USER_INSTRUCTION = "USER_INSTRUCTION"


class MarkerConstants:
    AGENTS_MD_START = "<AGENTS_MD_START_MARKER>"
    AGENTS_MD_END = "<AGENTS_MD_END_MARKER>"
    SKILL_START = "<SKILL_OPEN_TAG>"
    SKILL_END = "<SKILL_CLOSE_TAG>"
    USER_START = "<USER_INSTRUCTIONS_START_MARKER>"
    USER_END = "<USER_INSTRUCTIONS_END_MARKER>"


@dataclass
class ContextualUserFragment:
    """Represents a structured, typed, traceable instruction fragment (instructions/src/fragment.rs)."""
    fragment_type: FragmentType
    content: str
    source_dir: str
    name: str = "default"
    path: str = ""
    precedence_level: int = 10  # Higher means higher precedence

    def get_markers(self) -> Tuple[str, str]:
        if self.fragment_type == FragmentType.AGENTS_MD:
            return MarkerConstants.AGENTS_MD_START, MarkerConstants.AGENTS_MD_END
        elif self.fragment_type == FragmentType.SKILL:
            return MarkerConstants.SKILL_START, MarkerConstants.SKILL_END
        elif self.fragment_type == FragmentType.USER_INSTRUCTION:
            return MarkerConstants.USER_START, MarkerConstants.USER_END
        raise ValueError(f"Unknown fragment type: {self.fragment_type}")

    def wrap(self) -> str:
        """Wrap with explicit markers and header including directory and name metadata."""
        start_m, end_m = self.get_markers()
        header = f"# {self.fragment_type.value} instructions for {self.source_dir}"
        if self.name and self.name != "default":
            header += f" (name: {self.name})"
        return f"{start_m}\n{header}\n{self.content.strip()}\n{end_m}"

    def into_message(self) -> Dict[str, Any]:
        """Convert into a typed ResponseItem::Message dictionary."""
        return {
            "role": "user",
            "type": "contextual_fragment",
            "fragment_type": self.fragment_type.value,
            "source_dir": self.source_dir,
            "name": self.name,
            "path": self.path,
            "content": self.wrap(),
        }


class DirectoryScopedHierarchyManager:
    """Manages AGENTS.md inheritance across directory hierarchy (docs/agents_md.md)."""

    def __init__(self, enable_child_agents_md: bool = True):
        self.enable_child_agents_md = enable_child_agents_md
        # directory_path -> list of (filename, content)
        self.registered_dirs: Dict[str, str] = {}

    def register_agents_md(self, directory: str, content: str):
        normalized = os.path.normpath(directory)
        self.registered_dirs[normalized] = content

    def resolve_hierarchy_fragments(self, current_workdir: str) -> List[ContextualUserFragment]:
        """Resolve all applicable AGENTS.md from root to current workdir.
        
        Principle: Closer directory has higher precedence.
        """
        normalized_target = os.path.normpath(current_workdir)
        fragments: List[ContextualUserFragment] = []

        # Find matching directories along the path
        applicable_dirs = []
        for d in self.registered_dirs.keys():
            if normalized_target == d or normalized_target.startswith(d + os.sep) or d == "/":
                applicable_dirs.append(d)

        # Sort by depth (root first, deepest last)
        applicable_dirs.sort(key=lambda p: len(p.split(os.sep)))

        for depth_idx, d in enumerate(applicable_dirs):
            content = self.registered_dirs[d]
            # Higher depth = higher precedence
            precedence = 10 + depth_idx * 10
            frag = ContextualUserFragment(
                fragment_type=FragmentType.AGENTS_MD,
                content=content,
                source_dir=d,
                path=os.path.join(d, "AGENTS.md"),
                precedence_level=precedence,
            )
            fragments.append(frag)

        # If child_agents_md is enabled, inject scope and precedence declaration
        if self.enable_child_agents_md and fragments:
            declaration = (
                "[INHERITANCE NOTICE]: Deeper subdirectories override rules from parent directories. "
                "Current active scope: " + normalized_target
            )
            notice_frag = ContextualUserFragment(
                fragment_type=FragmentType.AGENTS_MD,
                content=declaration,
                source_dir=normalized_target,
                name="scope_declaration",
                precedence_level=999,
            )
            fragments.append(notice_frag)

        return fragments


# ============================================================================
# 3. 不变式强制检查器 (ControlPlaneInvariantEnforcer)
# ============================================================================

class InvariantViolationError(Exception):
    pass


class ControlPlaneInvariantEnforcer:
    """Enforces design invariants from Book 2 Ch 2."""

    @staticmethod
    def verify_fragment_symmetry(wrapped_text: str, frag_type: FragmentType):
        """Invariant: assert every fragment has matching (START_MARKER, END_MARKER)."""
        if frag_type == FragmentType.AGENTS_MD:
            start_m, end_m = MarkerConstants.AGENTS_MD_START, MarkerConstants.AGENTS_MD_END
        elif frag_type == FragmentType.SKILL:
            start_m, end_m = MarkerConstants.SKILL_START, MarkerConstants.SKILL_END
        elif frag_type == FragmentType.USER_INSTRUCTION:
            start_m, end_m = MarkerConstants.USER_START, MarkerConstants.USER_END
        else:
            raise InvariantViolationError(f"Unknown fragment type: {frag_type}")

        start_count = wrapped_text.count(start_m)
        end_count = wrapped_text.count(end_m)

        if start_count != end_count or start_count == 0:
            raise InvariantViolationError(
                f"Marker Asymmetry Violation: {start_m} count ({start_count}) != {end_m} count ({end_count})"
            )

    @staticmethod
    def verify_precedence_monotonicity(fragments: List[ContextualUserFragment]):
        """Invariant: assert precedence(project) > precedence(team) > precedence(default)."""
        current_precedence = -1
        for f in fragments:
            if f.precedence_level < current_precedence:
                raise InvariantViolationError(
                    f"Precedence Monotonicity Violation: Fragment {f.name} ({f.precedence_level}) < {current_precedence}"
                )
            current_precedence = f.precedence_level


# ============================================================================
# 4. 双控制面横向对比分析器 (DualControlPlaneAnalyzer)
# ============================================================================

class DualControlPlaneAnalyzer:
    """Compares the structural characteristics of both control planes on identical rules."""

    @classmethod
    def compare(
        cls,
        default_rule: str,
        team_rule: str,
        project_rule: str,
        workdir: str = "/repo/backend",
    ) -> Dict[str, Any]:
        # 1. Claude Code Dynamic Assembly
        claude_plane = DynamicAssemblyControlPlane(
            ClaudeCodeLayers(
                default_prompt=default_rule,
                team_claudemd=team_rule,
                project_claudemd=project_rule,
            )
        )
        claude_system_prompt = claude_plane.assemble_static_system_prompt()
        claude_reminder = claude_plane.inject_stream_tip_reminder("clean", "backend/server.py")

        # 2. Codex Structured Fragments
        codex_hierarchy = DirectoryScopedHierarchyManager(enable_child_agents_md=True)
        codex_hierarchy.register_agents_md("/repo", team_rule)
        codex_hierarchy.register_agents_md("/repo/backend", project_rule)
        codex_fragments = codex_hierarchy.resolve_hierarchy_fragments(workdir)

        # Invariant checks
        for frag in codex_fragments:
            ControlPlaneInvariantEnforcer.verify_fragment_symmetry(frag.wrap(), frag.fragment_type)
        ControlPlaneInvariantEnforcer.verify_precedence_monotonicity(codex_fragments)

        codex_messages = [f.into_message() for f in codex_fragments]

        return {
            "claude_code": {
                "system_prompt_length": len(claude_system_prompt),
                "system_prompt_preview": claude_system_prompt[:150] + "...",
                "stream_tip_reminder": claude_reminder,
                "paradigm": "Dynamic Assembly Line",
            },
            "codex": {
                "fragment_count": len(codex_fragments),
                "messages": codex_messages,
                "has_scope_declaration": any(f.name == "scope_declaration" for f in codex_fragments),
                "paradigm": "Structured Document Office",
            },
        }
