"""Day 24: Skills Mounting, Hook Lifecycle & Toolized Delegation (Refactored).

Faithfully reproduces the local-governance and delegation principles from Harness Books
Book 2 Ch 5 & 6 and Codex crate implementations (`skills`, `hooks`, `agent_tool`):

1. Skill & SkillRegistry:
   - Versioned, fingerprinted assets; matching fingerprint skips reinstall.
   - Proximity-based scope precedence: project > user > system.
   - SkillDirectoryLoader: parses SKILL.md files from disk directories.
   - SkillRuntime: Claude Code-style on-demand <invoked_skill> context injection.
2. HookEngine:
   - Explicit lifecycle events: session_start, user_prompt_submit, pre_tool_use, post_tool_use, stop.
   - Strict separation of preview_* (side-effect free) vs run_* execution paths.
   - Blocking pre-tool hooks that intercept dangerous commands before execution.
   - Platform capability explainable gating (graceful disable on unsupported OS).
3. DelegationEngine & DelegationLedger:
   - Toolized agent delegation primitives (spawn, send_input, wait_agent, close_agent).
   - Parent abort cascades to all descendant subagents (No-Orphan Invariant).
   - Strict timeout clamping and timeout-as-status (not false crash).
   - DelegationLedger: immutable audit evidence for skeptical verifiers.
4. GovernedAgentRuntime:
   - Unifies skills, lifecycle hooks, and delegation into a single cohesive harness runner.
"""

from __future__ import annotations

import copy
import hashlib
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ============================================================================
# 0. 异常体系 (Exceptions)
# ============================================================================

class GovernanceError(Exception):
    """Base class for local-governance violations."""


class SkillNotFoundError(GovernanceError):
    """Raised when a turn tries to activate an uninstalled skill."""


class HookConfigError(GovernanceError):
    """Raised when a hook handler lacks required lifecycle metadata."""


class HookInvariantViolation(GovernanceError):
    """Raised when hook event ordering or preview/run invariants break."""


class HookExecutionBlockedError(GovernanceError):
    """Raised when a blocking pre-tool hook short-circuits tool execution."""


class DelegationProtocolError(GovernanceError):
    """Raised on invalid delegation transitions or parameters."""


class DanglingHandleError(GovernanceError):
    """Raised when in-flight delegation handles leak past finalization."""


# ============================================================================
# 1. 技能资产与指纹安装 (Skill, SkillRegistry & SkillDirectoryLoader)
# ============================================================================

class SkillSource(str, Enum):
    """Local governance follows directory proximity: closer scope wins."""
    SYSTEM = "system"    # ~/.mini-harness/skills/.system
    USER = "user"        # ~/.mini-harness/skills
    PROJECT = "project"  # .agent/skills


_SOURCE_PRIORITY: Dict[SkillSource, int] = {
    SkillSource.SYSTEM: 1,
    SkillSource.USER: 2,
    SkillSource.PROJECT: 3,
}


@dataclass
class Skill:
    """A packable workflow/discipline asset, not a transient string in the prompt."""
    name: str
    content: str
    version: str = "1.0.0"
    source: SkillSource = SkillSource.PROJECT
    description: str = ""

    def fingerprint(self) -> str:
        raw = f"{self.name}::{self.version}::{self.content}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class InstallAction(str, Enum):
    INSTALLED = "installed"
    REINSTALLED = "reinstalled"
    SKIPPED_FINGERPRINT_MATCH = "skipped_fingerprint_match"


@dataclass
class InstallRecord:
    name: str
    action: InstallAction
    fingerprint: str
    source: SkillSource


class SkillDirectoryLoader:
    """Discovers and parses SKILL.md files from directories with frontmatter metadata."""

    @staticmethod
    def load_skill_file(filepath: str, source: SkillSource = SkillSource.PROJECT) -> Skill:
        with open(filepath, "r", encoding="utf-8") as f:
            raw_text = f.read()

        name = os.path.basename(os.path.dirname(filepath)) or os.path.splitext(os.path.basename(filepath))[0]
        version = "1.0.0"
        description = ""
        content = raw_text

        # Parse YAML-like frontmatter if present (between --- markers)
        if raw_text.startswith("---"):
            parts = raw_text.split("---", 2)
            if len(parts) >= 3:
                frontmatter = parts[1]
                content = parts[2].strip()
                for line in frontmatter.splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        k = k.strip().lower()
                        v = v.strip().strip('"').strip("'")
                        if k == "name":
                            name = v
                        elif k == "version":
                            version = v
                        elif k == "description":
                            description = v

        return Skill(name=name, content=content, version=version, source=source, description=description)

    @classmethod
    def scan_directory(cls, dir_path: str, source: SkillSource = SkillSource.PROJECT) -> List[Skill]:
        skills = []
        if not os.path.exists(dir_path):
            return skills
        for root, _, files in os.walk(dir_path):
            for file in files:
                if file.lower() in ("skill.md", "skill.txt") or file.endswith(".skill.md"):
                    full_path = os.path.join(root, file)
                    skills.append(cls.load_skill_file(full_path, source=source))
        return skills


class SkillRegistry:
    """Manages installed skills with fingerprint idempotency and scope precedence."""

    def __init__(self, home: str = "~/.mini-harness"):
        self.home = home
        self._installed: Dict[str, Skill] = {}
        self._fingerprints: Dict[str, str] = {}

    def install(self, skill: Skill) -> InstallRecord:
        existing = self._installed.get(skill.name)
        incoming_fp = skill.fingerprint()

        # Invariant: weaker scope never shadows stronger scope
        if existing is not None and _SOURCE_PRIORITY[skill.source] < _SOURCE_PRIORITY[existing.source]:
            return InstallRecord(skill.name, InstallAction.SKIPPED_FINGERPRINT_MATCH, skill.fingerprint(), skill.source)

        # Invariant: identical fingerprint skips reinstall (idempotent)
        if self._fingerprints.get(skill.name) == incoming_fp and existing is not None:
            return InstallRecord(skill.name, InstallAction.SKIPPED_FINGERPRINT_MATCH, incoming_fp, existing.source)

        action = InstallAction.REINSTALLED if existing is not None else InstallAction.INSTALLED
        self._installed[skill.name] = skill
        self._fingerprints[skill.name] = incoming_fp
        return InstallRecord(skill.name, action, incoming_fp, skill.source)

    def install_system_skills(self, skills: List[Skill]) -> List[InstallRecord]:
        return [self.install(s) for s in skills]

    def get(self, name: str) -> Optional[Skill]:
        return self._installed.get(name)

    def installed_names(self) -> List[str]:
        return sorted(self._installed.keys())

    def fingerprints(self) -> Dict[str, str]:
        return dict(self._fingerprints)


class SkillRuntime:
    """Claude Code-style on-demand injection into the live prompt context."""

    def __init__(self, registry: SkillRegistry):
        self.registry = registry

    def activate(self, name: str, reason: str = "") -> Dict[str, Any]:
        skill = self.registry.get(name)
        if skill is None:
            raise SkillNotFoundError(f"Skill '{name}' is not installed in {self.registry.home}.")
        header = f'<invoked_skill name="{skill.name}" version="{skill.version}" source="{skill.source.value}"'
        if reason:
            header += f' reason="{reason}"'
        header += ">"
        return {
            "role": "user",
            "type": "invoked_skill_attachment",
            "skill_name": skill.name,
            "content": f"{header}\n{skill.content.strip()}\n</invoked_skill>",
        }


# ============================================================================
# 2. Hook 生命周期事件引擎 (HookEngine)
# ============================================================================

class HookEvent(str, Enum):
    SESSION_START = "session_start"
    USER_PROMPT_SUBMIT = "user_prompt_submit"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    STOP = "stop"


@dataclass
class HookContext:
    thread_id: str
    event: HookEvent
    tool_name: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HookHandler:
    event_name: HookEvent
    command: Callable[[HookContext], Optional[str]]
    matcher: str = "*"
    timeout_s: float = 5.0
    status_message: str = ""
    source_path: str = ""
    display_order: int = 100
    latency_ms: float = 0.0
    name: str = ""
    can_block: bool = False

    def matches(self, ctx: HookContext) -> bool:
        if self.matcher in ("*", ""):
            return True
        return re.search(self.matcher, ctx.tool_name) is not None


class HookSkipReason(str, Enum):
    NONE = "none"
    MATCHER_MISS = "matcher_miss"
    TIMED_OUT = "timed_out"
    HOOKS_DISABLED = "hooks_disabled"


@dataclass
class HookRunRecord:
    handler_name: str
    event: HookEvent
    skipped: HookSkipReason
    output: Optional[str]
    display_order: int
    preview_only: bool = False
    blocked: bool = False
    block_reason: Optional[str] = None


class HookEngine:
    """Explicit lifecycle event system with preview/run separation."""

    def __init__(self, platform: str = "darwin", enabled: bool = True):
        self.platform = platform
        self.warnings: List[str] = []
        self.enabled = enabled
        if self.platform == "windows":
            self.enabled = False
            self.warnings.append(
                "codex_hooks disabled on windows: hook support is incomplete and would fail silently."
            )
        self.handlers: List[HookHandler] = []
        self.trace: List[HookRunRecord] = []
        self.event_order: List[Tuple[str, HookEvent]] = []
        self.preview_calls = 0
        self.run_calls = 0

    def register(self, handler: HookHandler) -> None:
        if not isinstance(handler.event_name, HookEvent):
            raise HookConfigError("Handler must declare an explicit event_name.")
        if handler.timeout_s <= 0:
            raise HookConfigError(f"Handler '{handler.name}' must declare a positive timeout.")
        if not handler.source_path:
            raise HookConfigError(f"Handler '{handler.name}' must record its source_path for auditability.")
        if not isinstance(handler.display_order, int):
            raise HookConfigError(f"Handler '{handler.name}' must declare an integer display_order.")
        if handler.can_block and handler.event_name != HookEvent.PRE_TOOL_USE:
            raise HookConfigError(
                f"Handler '{handler.name}': can_block is only permitted on PRE_TOOL_USE hooks."
            )
        self.handlers.append(handler)
        self.handlers.sort(key=lambda h: h.display_order)

    def preview_event(self, ctx: HookContext) -> List[HookRunRecord]:
        """Preview candidates that would trigger for this event. ZERO side effects."""
        self.preview_calls += 1
        records: List[HookRunRecord] = []
        if not self.enabled:
            return records
        for handler in self.handlers:
            if handler.event_name != ctx.event:
                continue
            if not handler.matches(ctx):
                continue
            records.append(
                HookRunRecord(
                    handler_name=handler.name,
                    event=ctx.event,
                    skipped=HookSkipReason.NONE,
                    output=None,
                    display_order=handler.display_order,
                    preview_only=True,
                )
            )
        return records

    def run_event(self, ctx: HookContext) -> List[HookRunRecord]:
        """Runs matched handlers in display_order. May block on PRE_TOOL_USE."""
        self.run_calls += 1
        self.event_order.append((ctx.thread_id, ctx.event))
        records: List[HookRunRecord] = []

        if not self.enabled:
            record = HookRunRecord(
                handler_name="*",
                event=ctx.event,
                skipped=HookSkipReason.HOOKS_DISABLED,
                output=None,
                display_order=0,
            )
            self.trace.append(record)
            return [record]

        for handler in self.handlers:
            if handler.event_name != ctx.event:
                continue
            if not handler.matches(ctx):
                record = HookRunRecord(
                    handler_name=handler.name,
                    event=ctx.event,
                    skipped=HookSkipReason.MATCHER_MISS,
                    output=None,
                    display_order=handler.display_order,
                )
                self.trace.append(record)
                records.append(record)
                continue

            # Execute handler
            t0 = time.monotonic()
            out: Optional[str] = None
            blocked = False
            block_reason: Optional[str] = None
            try:
                out = handler.command(ctx)
            except HookExecutionBlockedError as exc:
                if handler.can_block:
                    blocked = True
                    block_reason = str(exc)
                else:
                    raise
            dur = (time.monotonic() - t0) * 1000.0

            if dur > handler.timeout_s * 1000.0:
                record = HookRunRecord(
                    handler_name=handler.name,
                    event=ctx.event,
                    skipped=HookSkipReason.TIMED_OUT,
                    output=None,
                    display_order=handler.display_order,
                )
            else:
                record = HookRunRecord(
                    handler_name=handler.name,
                    event=ctx.event,
                    skipped=HookSkipReason.NONE,
                    output=out,
                    display_order=handler.display_order,
                    blocked=blocked,
                    block_reason=block_reason,
                )

            self.trace.append(record)
            records.append(record)

            # If blocked, abort further handlers for this event immediately
            if blocked:
                break

        return records


# ============================================================================
# 3. 工具化委派协议 (DelegationEngine & DelegationLedger)
# ============================================================================

class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ABORTED = "aborted"
    TIMEOUT = "timeout"


@dataclass
class AgentHandle:
    handle_id: str
    role: str
    prompt: str
    parent_handle_id: Optional[str]
    timeout_s: float
    status: AgentStatus = AgentStatus.PENDING
    pending_inputs: List[str] = field(default_factory=list)
    output: Optional[str] = None
    children: List[str] = field(default_factory=list)


@dataclass
class LedgerEvent:
    timestamp: float
    action: str  # "SPAWN", "SEND_INPUT", "WAIT", "CLOSE", "ABORT"
    handle_id: str
    details: Dict[str, Any]


class DelegationLedger:
    """Immutable audit ledger recording all delegation activities."""

    def __init__(self):
        self.events: List[LedgerEvent] = []

    def log(self, action: str, handle_id: str, details: Dict[str, Any]) -> LedgerEvent:
        evt = LedgerEvent(timestamp=time.time(), action=action, handle_id=handle_id, details=details)
        self.events.append(evt)
        return evt

    def is_action_logged(self, handle_id: str, action: str) -> bool:
        return any(e.handle_id == handle_id and e.action == action for e in self.events)

    def events_for_handle(self, handle_id: str) -> List[LedgerEvent]:
        return [e for e in self.events if e.handle_id == handle_id]


class DelegationEngine:
    """Codex-style toolized delegation manager (agent_tool.rs)."""

    MIN_TIMEOUT = 1.0
    MAX_TIMEOUT = 300.0
    DEFAULT_TIMEOUT = 30.0

    def __init__(self):
        self._handles: Dict[str, AgentHandle] = {}
        self.ledger = DelegationLedger()

    def spawn_agent(
        self,
        role: str,
        prompt: str,
        parent_handle_id: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> str:
        t = timeout if timeout is not None else self.DEFAULT_TIMEOUT
        t = max(self.MIN_TIMEOUT, min(self.MAX_TIMEOUT, t))
        handle_id = f"agent_{uuid.uuid4().hex[:8]}"

        handle = AgentHandle(
            handle_id=handle_id,
            role=role,
            prompt=prompt,
            parent_handle_id=parent_handle_id,
            timeout_s=t,
            status=AgentStatus.PENDING,
        )
        self._handles[handle_id] = handle

        if parent_handle_id and parent_handle_id in self._handles:
            self._handles[parent_handle_id].children.append(handle_id)

        self.ledger.log("SPAWN", handle_id, {"role": role, "parent": parent_handle_id, "timeout": t})
        return handle_id

    def send_input(self, handle_id: str, message: str, interrupt: bool = False) -> None:
        handle = self._handles.get(handle_id)
        if not handle:
            raise DelegationProtocolError(f"Handle '{handle_id}' not found.")
        if handle.status in (AgentStatus.ABORTED, AgentStatus.COMPLETED):
            raise DelegationProtocolError(f"Cannot send input to agent in status '{handle.status}'.")

        if interrupt:
            # Drop pending queue and preempt
            handle.pending_inputs = [message]
            handle.status = AgentStatus.RUNNING
            self.ledger.log("SEND_INPUT", handle_id, {"interrupt": True, "message": message})
        else:
            handle.pending_inputs.append(message)
            handle.status = AgentStatus.RUNNING
            self.ledger.log("SEND_INPUT", handle_id, {"interrupt": False, "message": message})

    def wait_agent(self, handle_id: str, timeout: Optional[float] = None) -> Tuple[AgentStatus, Optional[str]]:
        handle = self._handles.get(handle_id)
        if not handle:
            raise DelegationProtocolError(f"Handle '{handle_id}' not found.")

        # Simulate task completion if inputs present
        if handle.status == AgentStatus.RUNNING or handle.pending_inputs:
            handle.status = AgentStatus.COMPLETED
            handle.output = f"Completed work for role {handle.role}: {'; '.join(handle.pending_inputs) or handle.prompt}"
            handle.pending_inputs.clear()

        self.ledger.log("WAIT", handle_id, {"status": handle.status.value, "output": handle.output})
        return handle.status, handle.output

    def close_agent(self, handle_id: str, cascade: bool = True) -> None:
        handle = self._handles.get(handle_id)
        if not handle:
            return

        if cascade:
            for child_id in list(handle.children):
                self.close_agent(child_id, cascade=True)

        handle.status = AgentStatus.COMPLETED
        self.ledger.log("CLOSE", handle_id, {"cascade": cascade})
        self._handles.pop(handle_id, None)

    def abort_parent(self, handle_id: str) -> None:
        """Cascades abort to all descendants (No-Orphan invariant)."""
        handle = self._handles.get(handle_id)
        if not handle:
            return

        for child_id in list(handle.children):
            self.abort_parent(child_id)

        handle.status = AgentStatus.ABORTED
        self.ledger.log("ABORT", handle_id, {"cascaded": True})
        self._handles.pop(handle_id, None)

    def active_handles_count(self) -> int:
        return len(self._handles)

    def assert_no_dangling_handles(self) -> None:
        if self._handles:
            raise DanglingHandleError(f"Dangling delegation handles detected at finalization: {list(self._handles.keys())}")


# ============================================================================
# 4. 统一治理运行时 (GovernedAgentRuntime)
# ============================================================================

class GovernedAgentRuntime:
    """Unified runtime enforcing skills, hooks, and tool delegation for an Agent loop."""

    def __init__(
        self,
        registry: Optional[SkillRegistry] = None,
        hook_engine: Optional[HookEngine] = None,
        delegation: Optional[DelegationEngine] = None,
    ):
        self.registry = registry or SkillRegistry()
        self.skill_runtime = SkillRuntime(self.registry)
        self.hook_engine = hook_engine or HookEngine()
        self.delegation_engine = delegation or DelegationEngine()
        self._session_started = False

    def start_session(self, thread_id: str) -> None:
        if not self._session_started:
            ctx = HookContext(thread_id=thread_id, event=HookEvent.SESSION_START)
            self.hook_engine.run_event(ctx)
            self._session_started = True

    def run_governed_turn(
        self,
        thread_id: str,
        user_prompt: str,
        tool_name: Optional[str] = None,
        tool_args: Optional[Dict[str, Any]] = None,
        tool_callable: Optional[Callable[[], str]] = None,
        activate_skill_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Executes a single turn through the full lifecycle guardrails."""
        self.start_session(thread_id)

        # 1. User prompt submit event
        prompt_ctx = HookContext(thread_id=thread_id, event=HookEvent.USER_PROMPT_SUBMIT, payload={"prompt": user_prompt})
        self.hook_engine.run_event(prompt_ctx)

        # 2. On-demand skill mount
        mounted_skill_attachment = None
        if activate_skill_name:
            mounted_skill_attachment = self.skill_runtime.activate(activate_skill_name, reason=f"Triggered by turn")

        # 3. Tool execution lifecycle
        tool_output: Optional[str] = None
        tool_blocked = False
        block_reason: Optional[str] = None

        if tool_name and tool_callable:
            tool_ctx = HookContext(
                thread_id=thread_id,
                event=HookEvent.PRE_TOOL_USE,
                tool_name=tool_name,
                payload=tool_args or {},
            )
            # Preview path
            self.hook_engine.preview_event(tool_ctx)

            # Run pre_tool_use hooks
            pre_records = self.hook_engine.run_event(tool_ctx)
            for r in pre_records:
                if r.blocked:
                    tool_blocked = True
                    block_reason = r.block_reason
                    tool_output = f"[BLOCKED by Pre-Tool Hook: {r.block_reason}]"
                    break

            if not tool_blocked:
                # Actual execution
                tool_output = tool_callable()

            # Run post_tool_use hooks
            post_ctx = HookContext(
                thread_id=thread_id,
                event=HookEvent.POST_TOOL_USE,
                tool_name=tool_name,
                payload={"output": tool_output},
            )
            self.hook_engine.run_event(post_ctx)

        return {
            "thread_id": thread_id,
            "skill_attachment": mounted_skill_attachment,
            "tool_output": tool_output,
            "tool_blocked": tool_blocked,
            "block_reason": block_reason,
        }

    def close_session(self, thread_id: str) -> None:
        """Finishes the session and asserts invariants."""
        ctx = HookContext(thread_id=thread_id, event=HookEvent.STOP)
        self.hook_engine.run_event(ctx)
        self.delegation_engine.assert_no_dangling_handles()
