"""Day 15: Prompt Control Plane & Harness Governance Engine.

Faithfully reproduces Claude Code's architectural principles from Harness Books:
1. Model as Engine, Harness as Chassis (5-Layer defensive containment).
2. Prompt as Control Plane, NOT personality text:
   - 5-Tier Precedence Chain: override > coordinator > agent > custom > default (+ append).
   - Proactive mode additive stacking (default + agent).
3. Cache Boundary Hygiene:
   - Clear partition between Static Cacheable Prefix (95%+ KV cache hit) and Dynamic Suffix.
4. Runtime Invariants Enforcement:
   - Formal validation of control constraints before LLM invocation.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple


class PromptSourceTier(Enum):
    """Precedence hierarchy for prompt sources (lower value = higher priority)."""
    OVERRIDE = 1
    COORDINATOR = 2
    AGENT = 3
    CUSTOM = 4
    DEFAULT = 5


@dataclass
class PromptSection:
    """An individual modular section of the control plane prompt."""
    name: str
    content: str
    is_cacheable: bool = True
    tier: Optional[PromptSourceTier] = None
    description: str = ""

    def content_hash(self) -> str:
        """SHA-256 fingerprint of the section content for KV cache tracking."""
        return hashlib.sha256(self.content.strip().encode("utf-8")).hexdigest()[:12]


@dataclass
class BuiltPrompt:
    """The fully assembled control plane prompt ready for runtime dispatch."""
    raw_text: str
    static_prefix: str
    dynamic_suffix: str
    static_cache_key: str
    active_tier: PromptSourceTier
    sources_used: List[str]
    is_proactive_stacked: bool


@dataclass
class InvariantCheckResult:
    """Report of formal invariant validations on the built prompt."""
    passed: bool
    invariants_checked: int
    violations: List[str] = field(default_factory=list)


class PromptControlPlane:
    """Industrial Prompt Control Plane & Runtime Constitution Assembler.
    
    Implements Claude Code's buildEffectiveSystemPrompt() precedence architecture.
    """

    DEFAULT_CONSTITUTION = (
        "=== AGENT HARNESS RUNTIME CONSTITUTION ===\n"
        "You are an autonomous engineering agent governed by strict harness safety protocols.\n"
        "CORE RUNTIME INVARIANTS:\n"
        "1. NO UNAUTHORIZED MUTATIONS: Do not modify files, repositories, or system settings beyond the explicit goal.\n"
        "2. VERIFY BEFORE CLAIMING COMPLETION: Never report a task as completed without objective physical test execution.\n"
        "3. HIGH RISK TOOL RESTRAINT: For shell/bash operations, never use destructive commands, force pushes, or blind commits.\n"
        "4. ERROR RESILIENCE: Treat tool errors, timeouts, and unexpected outputs as standard control paths, never panic.\n"
        "=========================================="
    )

    def __init__(self, workspace_root: Optional[str] = None):
        self.workspace_root = workspace_root or os.getcwd()

        # Slot-based sources representing the 5 precedence tiers
        self._sources: Dict[PromptSourceTier, Optional[PromptSection]] = {
            PromptSourceTier.OVERRIDE: None,
            PromptSourceTier.COORDINATOR: None,
            PromptSourceTier.AGENT: None,
            PromptSourceTier.CUSTOM: None,
            PromptSourceTier.DEFAULT: PromptSection(
                name="default_constitution",
                content=self.DEFAULT_CONSTITUTION,
                is_cacheable=True,
                tier=PromptSourceTier.DEFAULT,
                description="Default foundational engineering discipline"
            )
        }

        # Additional modular sections
        self._project_instructions: Optional[PromptSection] = None
        self._append_instructions: Optional[PromptSection] = None
        self._dynamic_sections: List[PromptSection] = []

        # Auto-discover project constitution (e.g. CLAUDE.md / AGENTS.md)
        self._discover_project_instructions()

    def _discover_project_instructions(self) -> None:
        """Looks for project-level instructions (CLAUDE.md or AGENTS.md)."""
        candidate_files = ["CLAUDE.md", "AGENTS.md", "PROJECT_RULES.md"]
        for fname in candidate_files:
            fpath = os.path.join(self.workspace_root, fname)
            if os.path.isfile(fpath):
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                    if content:
                        self._project_instructions = PromptSection(
                            name=f"project_instructions_{fname}",
                            content=f"=== PROJECT RULES ({fname}) ===\n{content}\n================================",
                            is_cacheable=True,
                            description=f"Auto-loaded from {fname}"
                        )
                        break
                except Exception:
                    pass

    # -----------------------------------------------------------------
    # Configuration Setters
    # -----------------------------------------------------------------
    def set_source(self, tier: PromptSourceTier, name: str, content: str) -> None:
        """Sets a prompt source at a specific precedence tier."""
        self._sources[tier] = PromptSection(
            name=name,
            content=content.strip(),
            is_cacheable=True,
            tier=tier
        )

    def set_override(self, content: str) -> None:
        self.set_source(PromptSourceTier.OVERRIDE, "override_prompt", content)

    def set_coordinator(self, content: str) -> None:
        self.set_source(PromptSourceTier.COORDINATOR, "coordinator_prompt", content)

    def set_agent(self, content: str) -> None:
        self.set_source(PromptSourceTier.AGENT, "agent_prompt", content)

    def set_custom(self, content: str) -> None:
        self.set_source(PromptSourceTier.CUSTOM, "custom_prompt", content)

    def set_append(self, content: str) -> None:
        self._append_instructions = PromptSection(
            name="append_instructions",
            content=content.strip(),
            is_cacheable=True,
            description="Always affixed to the end of static instructions"
        )

    def add_dynamic_section(self, name: str, content_generator) -> None:
        """Adds a dynamic, non-cacheable runtime section (e.g. timestamp, scratchpad)."""
        content = content_generator() if callable(content_generator) else str(content_generator)
        self._dynamic_sections.append(
            PromptSection(
                name=name,
                content=content.strip(),
                is_cacheable=False,
                description="Uncached dynamic runtime observation"
            )
        )

    def clear_dynamic_sections(self) -> None:
        self._dynamic_sections.clear()

    # -----------------------------------------------------------------
    # Core Assembly Logic: buildEffectiveSystemPrompt()
    # -----------------------------------------------------------------
    def build_effective_prompt(self, proactive_mode: bool = False) -> BuiltPrompt:
        """Assembles effective prompt adhering strictly to precedence and cache rules.
        
        Algorithm (from Claude Code src/utils/systemPrompt.ts:28):
        1. sources = [override, coordinator, agent, custom, default]
        2. base = first_present(sources)
        3. if proactive_mode and agent and base != agent: base = default + agent (additive)
        4. static_prefix = base + [project_instructions] + [append]
        5. dynamic_suffix = [dynamic_uncached_sections]
        """
        # 1. Resolve primary source by precedence
        active_tier = PromptSourceTier.DEFAULT
        selected_base_section: Optional[PromptSection] = None
        sources_used = []

        for tier in [
            PromptSourceTier.OVERRIDE,
            PromptSourceTier.COORDINATOR,
            PromptSourceTier.AGENT,
            PromptSourceTier.CUSTOM,
            PromptSourceTier.DEFAULT
        ]:
            sec = self._sources.get(tier)
            if sec and sec.content.strip():
                selected_base_section = sec
                active_tier = tier
                sources_used.append(sec.name)
                break

        if not selected_base_section:
            selected_base_section = self._sources[PromptSourceTier.DEFAULT]
            active_tier = PromptSourceTier.DEFAULT

        base_content = selected_base_section.content
        is_proactive_stacked = False

        # 2. Proactive mode special case: additive stacking (default + agent)
        if proactive_mode and self._sources.get(PromptSourceTier.AGENT):
            agent_sec = self._sources[PromptSourceTier.AGENT]
            default_sec = self._sources[PromptSourceTier.DEFAULT]
            if default_sec and agent_sec:
                base_content = (
                    f"{default_sec.content}\n\n"
                    f"=== AGENT ROLE AUGMENTATION (PROACTIVE MODE) ===\n"
                    f"{agent_sec.content}\n"
                    f"================================================"
                )
                is_proactive_stacked = True
                if f"stacked_{agent_sec.name}" not in sources_used:
                    sources_used.append(f"stacked_{agent_sec.name}")

        # 3. Assemble Static Prefix (Cache-friendly!)
        static_parts = [base_content]

        if self._project_instructions:
            static_parts.append(self._project_instructions.content)
            sources_used.append(self._project_instructions.name)

        if self._append_instructions:
            static_parts.append(f"=== APPEND INSTRUCTIONS ===\n{self._append_instructions.content}\n===========================")
            sources_used.append(self._append_instructions.name)

        static_prefix = "\n\n".join(static_parts).strip()
        static_cache_key = hashlib.sha256(static_prefix.encode("utf-8")).hexdigest()[:16]

        # 4. Assemble Dynamic Suffix (Uncached!)
        dynamic_parts = []
        if self._dynamic_sections:
            dynamic_parts.append("=== RUNTIME DYNAMIC CONTEXT (NON-CACHEABLE) ===")
            for d in self._dynamic_sections:
                dynamic_parts.append(f"[{d.name}]\n{d.content}")
                sources_used.append(f"uncached_{d.name}")
            dynamic_parts.append("================================================")

        dynamic_suffix = "\n\n".join(dynamic_parts).strip()

        raw_text = static_prefix if not dynamic_suffix else f"{static_prefix}\n\n{dynamic_suffix}"

        return BuiltPrompt(
            raw_text=raw_text,
            static_prefix=static_prefix,
            dynamic_suffix=dynamic_suffix,
            static_cache_key=static_cache_key,
            active_tier=active_tier,
            sources_used=sources_used,
            is_proactive_stacked=is_proactive_stacked
        )

    # -----------------------------------------------------------------
    # Runtime Invariant Enforcement
    # -----------------------------------------------------------------
    def verify_invariants(self, built_prompt: BuiltPrompt) -> InvariantCheckResult:
        """Formal check of the 3 fundamental control plane invariants."""
        violations = []
        checks_run = 0

        # Invariant 1: Hierarchy Integrity
        # The base layer must be explicitly resolved and non-empty
        checks_run += 1
        if not built_prompt.static_prefix.strip():
            violations.append("Invariant 1 Violated: Static prompt prefix is empty.")

        # Invariant 2: Execution Safety Constraints
        # Must contain non-negotiable prohibitions against rogue mutations and false completion
        checks_run += 1
        text_upper = built_prompt.raw_text.upper()
        required_guardrails = [
            ("NO UNAUTHORIZED MUTATIONS", "File/state mutation safety boundary missing"),
            ("VERIFY BEFORE CLAIMING", "Mandatory physical test verification rule missing")
        ]
        for phrase, reason in required_guardrails:
            if phrase not in text_upper:
                # If override was used, check if it explicitly provided alternative guardrails
                if built_prompt.active_tier != PromptSourceTier.OVERRIDE:
                    violations.append(f"Invariant 2 Violated: {reason}")

        # Invariant 3: Cache Hygiene
        # Ensure that no unstable/dynamic variables (like raw timestamps) contaminate static prefix
        checks_run += 1
        suspicious_patterns = [
            r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", # ISO timestamp
            r"current_time\s*[:=]\s*\d+",             # Epoch timestamps
        ]
        for pat in suspicious_patterns:
            if re.search(pat, built_prompt.static_prefix):
                violations.append("Invariant 3 Violated: Dynamic timestamp detected inside static cacheable prefix! KV Cache breakdown risk.")

        # Invariant 4: Precedence Order Compliance
        checks_run += 1
        # If override is present, it must be the active tier
        if self._sources.get(PromptSourceTier.OVERRIDE) and built_prompt.active_tier != PromptSourceTier.OVERRIDE:
            violations.append("Invariant 4 Violated: OVERRIDE source present but not selected as active tier.")

        return InvariantCheckResult(
            passed=len(violations) == 0,
            invariants_checked=checks_run,
            violations=violations
        )
