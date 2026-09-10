"""Day 18: Context Compaction, Memory Index Governance & Controlled Reboot Engine.

Faithfully reproduces Claude Code's architectural principles from Harness Books Ch 5:
1. Context as Working Memory Budget (Information saturation & attention dilution defense).
2. Three-Tier Memory Decoupling (CLAUDE.md rules vs MEMORY.md index vs SessionMemory blueprint).
3. Memory Index Governance (MAX_ENTRYPOINT_LINES = 200, MAX_ENTRYPOINT_BYTES = 25,000 hard truncation).
4. AutoCompact Budget & Buffer System (MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20,000, AUTOCOMPACT_BUFFER_TOKENS = 13,000).
5. Circuit Breaker for Repeated Failures (MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3).
6. compactConversation() as Controlled Reboot (Pre-cleanse, Session Memory synthesis, Post-reconstruction with CompactBoundary).
"""

from __future__ import annotations

import copy
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ============================================================================
# 1. 结构化会话工作说明书 (Session Memory)
# ============================================================================

@dataclass
class SessionMemory:
    """Structured session continuity blueprint (src/services/SessionMemory/prompts.ts).
    
    Acts as an actionable operating manual for continuing work across turns,
    rather than a casual chat transcript.
    """
    current_state: str = ""
    task_specification: str = ""
    files_and_functions: str = ""
    workflow: str = ""
    errors_and_corrections: str = ""
    codebase_docs: str = ""
    learnings: str = ""
    key_results: str = ""
    worklog: str = ""

    MAX_SECTION_LENGTH: int = 2_000  # Character/token budget per section
    MAX_TOTAL_SESSION_MEMORY_TOKENS: int = 12_000  # Total session memory budget

    def render_markdown(self) -> str:
        """Render the structured memory according to Claude Code template."""
        sections = [
            ("# Current State", self.current_state),
            ("# Task specification", self.task_specification),
            ("# Files and Functions", self.files_and_functions),
            ("# Workflow", self.workflow),
            ("# Errors & Corrections", self.errors_and_corrections),
            ("# Codebase and System Documentation", self.codebase_docs),
            ("# Learnings", self.learnings),
            ("# Key results", self.key_results),
            ("# Worklog", self.worklog),
        ]
        rendered = []
        for header, content in sections:
            if content.strip():
                # Enforce section length ceiling
                trimmed = content.strip()
                if len(trimmed) > self.MAX_SECTION_LENGTH:
                    trimmed = trimmed[:self.MAX_SECTION_LENGTH] + "\n... [Section Truncated to 2000 chars]"
                rendered.append(f"{header}\n{trimmed}\n")
        return "\n".join(rendered).strip()

    def aggressive_condense(self, max_total_chars: int = 4_000) -> SessionMemory:
        """Aggressively condense when budget is exceeded.
        
        Principle: Prioritize retaining Current State and Errors & Corrections;
        aggressively truncate or drop transient worklog and secondary docs.
        """
        condensed = copy.deepcopy(self)
        current_len = len(condensed.render_markdown())
        if current_len <= max_total_chars:
            return condensed

        # Truncate worklog first
        if len(condensed.worklog) > 200:
            condensed.worklog = condensed.worklog[:200] + " ... [Worklog condensed]"
        
        # Truncate learnings & codebase docs
        if len(condensed.learnings) > 200:
            condensed.learnings = condensed.learnings[:200] + " ... [Learnings condensed]"
        if len(condensed.codebase_docs) > 200:
            condensed.codebase_docs = condensed.codebase_docs[:200] + " ... [Docs condensed]"

        # If still over budget, truncate workflow
        if len(condensed.render_markdown()) > max_total_chars and len(condensed.workflow) > 200:
            condensed.workflow = condensed.workflow[:200] + " ... [Workflow condensed]"

        # Current State and Errors & Corrections remain protected as top operational assets!
        return condensed


# ============================================================================
# 2. 长期记忆索引守卫 (MemoryIndexManager)
# ============================================================================

class MemoryIndexManager:
    """Governs MEMORY.md entrypoint and topic files (src/memdir/memdir.ts).
    
    Enforces the Two-Step Pattern:
    1. Detailed memory lives in dedicated topic files.
    2. MEMORY.md is strictly a lightweight index of one-line pointers.
    Physical limits:
    - MAX_ENTRYPOINT_LINES = 200
    - MAX_ENTRYPOINT_BYTES = 25_000
    Automatic truncation with standard system warning if breached.
    """

    ENTRYPOINT_NAME = "MEMORY.md"
    MAX_ENTRYPOINT_LINES = 200
    MAX_ENTRYPOINT_BYTES = 25_000
    TRUNCATION_WARNING = "\n[WARNING: Memory entrypoint truncated to 200 lines / 25KB. Please move detailed content into dedicated topic files.]"

    def __init__(self):
        self.entrypoint_lines: List[str] = [
            "# Project Persistent Memory Index",
            "This file is strictly an index of memory pointers. Detailed content lives in topic files.",
            "",
        ]
        self.topic_files: Dict[str, str] = {}

    def add_memory(self, topic: str, pointer_summary: str, detailed_content: Optional[str] = None):
        """Add a memory following the two-step index convention."""
        topic_filename = f"{topic.lower().replace(' ', '_')}.md"
        if detailed_content:
            self.topic_files[topic_filename] = detailed_content
            entry_line = f"- [{topic}]({topic_filename}): {pointer_summary}"
        else:
            entry_line = f"- {topic}: {pointer_summary}"

        self.entrypoint_lines.append(entry_line)

    def read_entrypoint(self) -> Tuple[str, bool]:
        """Read MEMORY.md entrypoint, enforcing line and byte physical bounds.
        
        Returns (content, was_truncated).
        """
        raw_content = "\n".join(self.entrypoint_lines)
        lines = raw_content.splitlines()
        raw_bytes = raw_content.encode("utf-8")

        needs_truncation = (
            len(lines) > self.MAX_ENTRYPOINT_LINES or len(raw_bytes) > self.MAX_ENTRYPOINT_BYTES
        )

        if not needs_truncation:
            return raw_content, False

        # Execute physical truncation
        truncated_lines = lines[:self.MAX_ENTRYPOINT_LINES]
        candidate = "\n".join(truncated_lines)
        candidate_bytes = candidate.encode("utf-8")

        if len(candidate_bytes) > self.MAX_ENTRYPOINT_BYTES:
            # Cut at byte boundary
            candidate = candidate_bytes[:self.MAX_ENTRYPOINT_BYTES].decode("utf-8", errors="ignore")

        final_content = candidate.rstrip() + self.TRUNCATION_WARNING
        return final_content, True


# ============================================================================
# 3. 自动压缩追踪与熔断器 (AutoCompactTracker)
# ============================================================================

@dataclass
class AutoCompactTracker:
    """Tracks compaction state and prevents runaway API loops (src/services/compact/autoCompact.ts)."""

    compacted: bool = False
    turn_counter: int = 0
    turn_id: Optional[str] = None
    consecutive_failures: int = 0
    circuit_broken: bool = False
    max_consecutive_failures: int = 3

    def record_attempt(self, turn_id: str):
        self.turn_id = turn_id
        self.turn_counter += 1

    def record_success(self):
        self.compacted = True
        self.consecutive_failures = 0

    def record_failure(self):
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_consecutive_failures:
            self.circuit_broken = True

    def can_attempt_compact(self) -> bool:
        return not self.circuit_broken


# ============================================================================
# 4. 上下文压紧与受控重启控制器 (ContextCompactor)
# ============================================================================

@dataclass
class CompactBoundary:
    """Records the reboot boundary anchoring the new KV cache baseline."""
    pre_compact_tokens: int
    compacted_turns: int
    timestamp: float = field(default_factory=time.time)
    retained_active_files: List[str] = field(default_factory=list)
    retained_skills: List[str] = field(default_factory=list)


class ContextCompactor:
    """Engine executing controlled reboot compaction (src/services/compact/compact.ts).
    
    Features:
    - Pre-compact cleansing (stripping raw images & reinjected file attachments).
    - Session Memory structured extraction.
    - Post-compact working semantic reconstruction (active files, plan mode, invoked skills).
    - Per-skill token truncation ('per-skill truncation beats dropping').
    - CompactBoundary cache baseline anchor.
    - Continuous failure circuit breaker.
    """

    DEFAULT_CONTEXT_WINDOW = 128_000
    MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000
    AUTOCOMPACT_BUFFER_TOKENS = 13_000
    MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3
    SKILL_TOKEN_CAP_CHARS = 1_000  # Cap per skill to preserve discipline without bloating

    def __init__(self, context_window: int = DEFAULT_CONTEXT_WINDOW):
        self.context_window = context_window
        self.tracker = AutoCompactTracker(max_consecutive_failures=self.MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES)
        self.read_file_state: Set[str] = set()

    def get_effective_context_window(self) -> int:
        """Reserve budget for summary generation."""
        return max(0, self.context_window - self.MAX_OUTPUT_TOKENS_FOR_SUMMARY)

    def get_auto_compact_threshold(self) -> int:
        """Deduct warning buffer to trigger compaction well before window overflow."""
        return max(0, self.get_effective_context_window() - self.AUTOCOMPACT_BUFFER_TOKENS)

    def should_auto_compact(self, current_tokens: int) -> bool:
        return current_tokens >= self.get_auto_compact_threshold()

    def cleanse_messages_for_summary(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Pre-compact cleansing: strip bloated raw data that adds token weight without summary value.
        
        1. stripImagesFromMessages: Replaces inline base64/binary images with [image].
        2. stripReinjectedAttachments: Replaces raw file dumps with [attachment: filename].
        """
        cleansed: List[Dict[str, Any]] = []
        for msg in messages:
            msg_copy = copy.deepcopy(msg)
            content = msg_copy.get("content", "")

            # 1. Strip raw images
            content = re.sub(r"data:image\/[a-zA-Z]+;base64,[A-Za-z0-9+/=]+", "[image]", content)
            content = re.sub(r"!\[.*?\]\(.*?\)", "[image]", content)

            # 2. Strip transient reinjected attachments
            content = re.sub(
                r"<attachment path=\"([^\"]+)\">[\s\S]*?<\/attachment>",
                r"[attachment: \1]",
                content
            )
            # Strip large raw test logs if over 1000 chars
            if "<raw_test_log>" in content:
                content = re.sub(r"<raw_test_log>[\s\S]*?<\/raw_test_log>", "[raw_test_log: truncated]", content)

            msg_copy["content"] = content
            cleansed.append(msg_copy)
        return cleansed

    def truncate_head_for_ptl_retry(
        self, messages: List[Dict[str, Any]], drop_turn_count: int = 2
    ) -> List[Dict[str, Any]]:
        """truncateHeadForPTLRetry: If compacting prompt causes Prompt Too Long error,
        safely trim the oldest conversation turns while preserving system instructions and latest turns.
        """
        if len(messages) <= drop_turn_count + 1:
            return messages
        
        # Keep system prompt (index 0 if system) and drop the oldest turns
        if messages[0].get("role") == "system":
            return [messages[0]] + messages[1 + drop_turn_count :]
        return messages[drop_turn_count:]

    def reconstruct_post_compact_environment(
        self,
        session_summary: SessionMemory,
        active_files: Dict[str, str],
        plan_state: Optional[Dict[str, Any]] = None,
        invoked_skills: Optional[Dict[str, str]] = None,
        pre_compact_tokens: int = 0,
        compacted_turns: int = 0,
    ) -> Tuple[List[Dict[str, Any]], CompactBoundary]:
        """Rebuild operational context after compaction (Controlled Reboot).
        
        Instead of merely leaving a paragraph of text:
        1. Resets stale readFileState.
        2. Re-injects active file attachments into working memory.
        3. Re-injects active plan discipline.
        4. Re-injects invoked skills with per-skill truncation cap.
        5. Emits CompactBoundary anchor.
        """
        # 1. Reset readFileState
        self.read_file_state.clear()

        reconstructed_messages: List[Dict[str, Any]] = []

        # 2. Inject Compact Boundary as anchor
        boundary = CompactBoundary(
            pre_compact_tokens=pre_compact_tokens,
            compacted_turns=compacted_turns,
            retained_active_files=list(active_files.keys()),
            retained_skills=list(invoked_skills.keys()) if invoked_skills else [],
        )

        reconstructed_messages.append({
            "role": "system",
            "type": "compact_boundary",
            "content": (
                f"<compact_boundary>\n"
                f"Context compacted from {pre_compact_tokens} tokens across {compacted_turns} turns.\n"
                f"Controlled reboot completed. Previous raw turns discarded; actionable work semantics reconstructed.\n"
                f"</compact_boundary>"
            )
        })

        # 3. Inject Structured Session Memory
        reconstructed_messages.append({
            "role": "user",
            "type": "session_memory_injection",
            "content": f"<session_memory>\n{session_summary.render_markdown()}\n</session_memory>"
        })

        # 4. Reconstruct Active Files (Operational Working Set)
        for filepath, content in active_files.items():
            self.read_file_state.add(filepath)
            reconstructed_messages.append({
                "role": "system",
                "type": "active_file_attachment",
                "content": f'<active_file path="{filepath}">\n{content}\n</active_file>'
            })

        # 5. Reconstruct Plan Discipline & Status
        if plan_state:
            reconstructed_messages.append({
                "role": "system",
                "type": "plan_mode_attachment",
                "content": (
                    f"<active_plan name=\"{plan_state.get('name', 'Main Plan')}\">\n"
                    f"Current Step: {plan_state.get('current_step', 'Unknown')}\n"
                    f"Remaining Steps: {', '.join(plan_state.get('remaining_steps', []))}\n"
                    f"</active_plan>"
                )
            })

        # 6. Reconstruct Invoked Skills with per-skill token cap
        # Principle: 'Per-skill truncation beats dropping'
        if invoked_skills:
            for skill_name, skill_prompt in invoked_skills.items():
                skill_content = skill_prompt.strip()
                if len(skill_content) > self.SKILL_TOKEN_CAP_CHARS:
                    skill_content = (
                        skill_content[:self.SKILL_TOKEN_CAP_CHARS]
                        + "\n... [Skill instruction truncated to preserve context budget]"
                    )
                reconstructed_messages.append({
                    "role": "system",
                    "type": "invoked_skill_attachment",
                    "content": f'<invoked_skill name="{skill_name}">\n{skill_content}\n</invoked_skill>'
                })

        return reconstructed_messages, boundary

    def compact_conversation(
        self,
        messages: List[Dict[str, Any]],
        active_files: Dict[str, str],
        summarizer_func: Callable[[List[Dict[str, Any]]], SessionMemory],
        plan_state: Optional[Dict[str, Any]] = None,
        invoked_skills: Optional[Dict[str, str]] = None,
        current_tokens: int = 100_000,
        turn_id: str = "turn_default",
    ) -> Tuple[List[Dict[str, Any]], CompactBoundary]:
        """Execute the end-to-end controlled reboot workflow.
        
        Guarded by Circuit Breaker.
        """
        if not self.tracker.can_attempt_compact():
            raise RuntimeError(
                f"AutoCompact Circuit Breaker Tripped! Refusing compaction after "
                f"{self.tracker.consecutive_failures} consecutive failures."
            )

        self.tracker.record_attempt(turn_id)

        try:
            # 1. Pre-cleanse messages
            cleansed = self.cleanse_messages_for_summary(messages)

            # 2. Extract Session Memory
            session_summary = summarizer_func(cleansed)

            # 3. Post-compact environment reconstruction
            compacted_turns = len([m for m in messages if m.get("role") == "user"])
            reconstructed_messages, boundary = self.reconstruct_post_compact_environment(
                session_summary=session_summary,
                active_files=active_files,
                plan_state=plan_state,
                invoked_skills=invoked_skills,
                pre_compact_tokens=current_tokens,
                compacted_turns=compacted_turns,
            )

            self.tracker.record_success()
            return reconstructed_messages, boundary

        except Exception as e:
            self.tracker.record_failure()
            raise e
