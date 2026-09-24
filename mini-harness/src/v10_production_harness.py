"""mini-harness v1.0: Production-Grade Unified AI Coding Agent Harness.

Phase 4 Capstone Masterpiece Engineering Artifact.
Unified culmination of 4 weeks of hands-on harness engineering:

1. Control Plane & Prefix Cache Discipline:
   - PromptControlPlane: 5-tier precedence hierarchy & boundary discipline.
   - PrefixCacheDisciplineValidator: Zero-tolerance for mid-stream drift, preserving GPU KV cache.
2. Governance & Rules Substrate:
   - SkillRegistry & SkillDirectoryLoader: Versioned, fingerprinted local skills with precedence.
   - HookEngine: Explicit lifecycle events with preview_* vs run_* and blocking pre-hooks.
   - DelegationEngine & DelegationLedger: Toolized multi-agent delegation with cascade termination.
3. Resilience & In-Loop Defense:
   - SafeBashGuard & Tri-State Permission Policy (ALLOW, ASK, DENY).
   - SyntheticLedger: Dual-entry tool accounting, guaranteed balance on Ctrl+C / timeouts.
   - ContextCompactor & Living Working Memory: Compaction with semantic anchoring.
4. Execution Isolation & Session Portability:
   - SandboxedToolExecutor: Dual-key privilege separation (no host master key leaks).
   - Dual-mode Execution Sandbox: LocalDockerSandbox (prod) / SimulatedIsolatedSandbox (hermetic).
   - SessionPortabilityEngine: Lossless export/import to Pi-style append-only JSONL event streams.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# Ensure adjacent day modules can be imported
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from day23_sandbox_executor import (
    BaseExecutionSandbox,
    DualKeyPrivilegeBroker,
    ExecutionResult,
    LocalDockerSandbox,
    NetworkMode,
    SandboxSecurityPolicy,
    SandboxedToolExecutor,
    SimulatedIsolatedSandbox,
)
from day24_skills_and_delegation import (
    AgentStatus,
    DelegationEngine,
    DelegationLedger,
    GovernedAgentRuntime,
    HookContext,
    HookEngine,
    HookEvent,
    HookExecutionBlockedError,
    HookHandler,
    InstallAction,
    Skill,
    SkillDirectoryLoader,
    SkillRegistry,
    SkillRuntime,
    SkillSource,
)
from day25_architecture_synthesis import (
    CacheInvalidationReason,
    PiEvent,
    PiSessionStream,
    PrefixCacheDisciplineValidator,
    PromptAssemblySnapshot,
    SessionPortabilityEngine,
    SyntheticLedger,
)


# ============================================================================
# 1. 生产级配置与运行时状态 (Configuration & State)
# ============================================================================

class ExecutionMode(str, Enum):
    SIMULATED = "simulated"  # 纯 Python 仿真，零环境依赖
    DOCKER = "docker"        # 依赖本地 Docker 守护进程


@dataclass
class ProductionHarnessConfig:
    """Enterprise configuration for mini-harness v1.0."""
    session_id: str = field(default_factory=lambda: f"sess_{uuid.uuid4().hex[:8]}")
    execution_mode: ExecutionMode = ExecutionMode.SIMULATED
    workspace_dir: str = "/tmp/mini-harness-workspace"
    skills_dir: Optional[str] = None
    system_prompt: str = (
        "You are an expert AI software architect operating inside mini-harness v1.0. "
        "Strictly obey safety invariants, execute code inside the sandbox, and reason step-by-step."
    )
    max_turns: int = 30
    token_compaction_threshold: int = 6000
    host_secrets: Dict[str, str] = field(default_factory=dict)
    enable_docker_fallback_to_sim: bool = True


# ============================================================================
# 2. 安全与权限守卫 (SafeBashGuard & Permissions)
# ============================================================================

class PermissionLevel(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class SafeBashGuard:
    """Defense-in-depth gatekeeper for shell commands before dispatch."""

    DENY_PATTERNS = [
        (r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+[/~*]", "Destructive recursive deletion of system/root files"),
        (r"\bgit\s+push\s+.*(?:-f|--force)\b", "Destructive force push to remote repository"),
        (r"\bchmod\s+-[a-zA-Z]*R\s+777\b", "Dangerous open directory permission alteration"),
        (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "Fork bomb DoS attack"),
    ]

    ASK_PATTERNS = [
        (r"\bgit\s+commit\b", "Repository commit requires human confirmation"),
        (r"\bpip\s+install\b", "Package installation alters dependencies"),
        (r"\brm\s+", "File deletion operation"),
    ]

    @classmethod
    def evaluate(cls, command: str) -> Tuple[PermissionLevel, str]:
        for pattern, reason in cls.DENY_PATTERNS:
            if re.search(pattern, command):
                return PermissionLevel.DENY, f"[BLOCKED] {reason}"
        for pattern, reason in cls.ASK_PATTERNS:
            if re.search(pattern, command):
                return PermissionLevel.ASK, f"[REQUIRES_CONFIRMATION] {reason}"
        return PermissionLevel.ALLOW, "Command allowed"


# ============================================================================
# 3. 生产级主引擎 (ProductionHarnessV1)
# ============================================================================

class ProductionHarnessV1:
    """The complete industrial Coding Agent Harness integrating Phase 1-4 subsystems."""

    def __init__(self, config: Optional[ProductionHarnessConfig] = None):
        self.config = config or ProductionHarnessConfig()
        self.session_id = self.config.session_id
        os.makedirs(self.config.workspace_dir, exist_ok=True)

        # 1. 提示缓存与控制面 (Prefix Cache & Control Plane)
        self.cache_validator = PrefixCacheDisciplineValidator(base_ttft_ms=25.0, cold_penalty_factor=8.0)
        self.tools_declaration = json.dumps({
            "tools": ["run_bash", "read_file", "write_file", "list_dir", "spawn_subagent"]
        }, sort_keys=True)
        self.history_stream: List[str] = []
        self.turn_counter = 0

        # 2. 安全沙箱与执行面 (Sandbox & Execution Subsystem)
        self.policy = SandboxSecurityPolicy(
            network_mode=NetworkMode.NONE,
            memory_limit_mb=256,
            pids_limit=64,
        )
        self.broker = DualKeyPrivilegeBroker(
            host_secrets=self.config.host_secrets,
            policy=self.policy,
        )
        if self.config.execution_mode == ExecutionMode.DOCKER:
            try:
                self.sandbox: BaseExecutionSandbox = LocalDockerSandbox(
                    workspace_dir=self.config.workspace_dir,
                    policy=self.policy,
                    broker=self.broker,
                    session_id=self.session_id,
                )
            except Exception:
                if self.config.enable_docker_fallback_to_sim:
                    self.sandbox = SimulatedIsolatedSandbox(
                        session_id=self.session_id,
                        policy=self.policy,
                        broker=self.broker,
                    )
                else:
                    raise
        else:
            self.sandbox = SimulatedIsolatedSandbox(
                session_id=self.session_id,
                policy=self.policy,
                broker=self.broker,
            )
        self.tool_executor = SandboxedToolExecutor(sandbox=self.sandbox, timeout=30)

        # 3. 本地规则治理与 Hook 引擎 (Governance, Skills & Hooks)
        self.skill_registry = SkillRegistry()
        self.skill_runtime = SkillRuntime(self.skill_registry)
        if self.config.skills_dir and os.path.exists(self.config.skills_dir):
            discovered = SkillDirectoryLoader.scan_directory(self.config.skills_dir, source=SkillSource.PROJECT)
            for sk in discovered:
                self.skill_registry.install(sk)

        self.hook_engine = HookEngine(platform="darwin" if sys.platform == "darwin" else "linux")
        self._register_default_hooks()

        # 4. 工具化多代理委派 (Delegation Engine)
        self.delegation_engine = DelegationEngine()

        # 5. 协议账本与会话流 (Synthetic Ledger & Pi Event Stream)
        self.ledger = SyntheticLedger()
        self.session_stream = PiSessionStream(session_id=self.session_id)

        # 初始化会话事件
        self.session_stream.append_event(
            kind="system_prompt",
            payload={"text": self.config.system_prompt},
            turn_index=0
        )
        self._session_started = False

    def _register_default_hooks(self) -> None:
        """Installs baseline security & observability hooks."""
        # SafeBashGuard Blocking Pre-Tool Hook
        def bash_security_check(ctx: HookContext):
            if ctx.tool_name == "run_bash":
                cmd = ctx.payload.get("cmd", "")
                decision, reason = SafeBashGuard.evaluate(cmd)
                if decision == PermissionLevel.DENY:
                    raise HookExecutionBlockedError(reason)
            return "OK"

        self.hook_engine.register(
            HookHandler(
                name="safe_bash_guard",
                event_name=HookEvent.PRE_TOOL_USE,
                command=bash_security_check,
                matcher="run_bash",
                can_block=True,
                source_path="mini-harness/guards/bash.py",
                display_order=10,
            )
        )

    def start_session_if_needed(self) -> None:
        if not self._session_started:
            ctx = HookContext(thread_id=self.session_id, event=HookEvent.SESSION_START)
            self.hook_engine.run_event(ctx)
            self._session_started = True

    def execute_turn(
        self,
        user_input: str,
        tool_name: Optional[str] = None,
        tool_args: Optional[Dict[str, Any]] = None,
        activate_skill_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Executes a complete single-turn cycle through all industrial defense layers."""
        self.start_session_if_needed()
        self.turn_counter += 1
        t0 = time.time()

        # 1. 记录用户意图与事件流 (User Prompt Submit)
        prompt_ctx = HookContext(
            thread_id=self.session_id,
            event=HookEvent.USER_PROMPT_SUBMIT,
            payload={"text": user_input},
        )
        self.hook_engine.run_event(prompt_ctx)
        self.session_stream.append_event("user_prompt", {"text": user_input}, self.turn_counter)
        self.history_stream.append(f"User: {user_input}")

        # 2. 验证提示缓存前缀稳定性 (KV Prefix Cache Discipline)
        snapshot = PromptAssemblySnapshot(
            turn_index=self.turn_counter,
            static_prefix=self.config.system_prompt,
            tools_declaration=self.tools_declaration,
            history_stream=list(self.history_stream),
        )
        cache_hit, cache_reason, est_ttft = self.cache_validator.register_turn(snapshot)

        # 3. 按需挂载技能 (On-Demand Skill Attachment)
        skill_attachment = None
        if activate_skill_name:
            skill_attachment = self.skill_runtime.activate(activate_skill_name, reason="Turn activation")

        # 4. 执行受管工具调用 (Managed Tool Execution)
        observation: Optional[str] = None
        tool_blocked = False
        block_reason: Optional[str] = None
        call_id = f"call_{self.turn_counter}_{uuid.uuid4().hex[:6]}"

        if tool_name:
            args = tool_args or {}
            self.ledger.debit_call(call_id, tool_name, args)
            self.session_stream.append_event("tool_call", {"tool_name": tool_name, "args": args}, self.turn_counter)

            # Pre-Tool Hook 拦截检查
            pre_ctx = HookContext(
                thread_id=self.session_id,
                event=HookEvent.PRE_TOOL_USE,
                tool_name=tool_name,
                payload=args,
            )
            self.hook_engine.preview_event(pre_ctx)
            pre_records = self.hook_engine.run_event(pre_ctx)

            for r in pre_records:
                if r.blocked:
                    tool_blocked = True
                    block_reason = r.block_reason
                    observation = f"[SECURITY_GATEWAY_BLOCKED] {r.block_reason}"
                    break

            if not tool_blocked:
                # 物理/仿真沙箱安全执行
                if tool_name == "run_bash":
                    cmd = args.get("cmd", "")
                    observation = self.tool_executor.run(cmd)
                elif tool_name == "read_file":
                    rel_path = args.get("path", "")
                    full_path = os.path.join(self.config.workspace_dir, rel_path)
                    if os.path.exists(full_path):
                        with open(full_path, "r", encoding="utf-8") as f:
                            observation = f.read()
                    else:
                        observation = f"[Error] File '{rel_path}' does not exist."
                elif tool_name == "write_file":
                    rel_path = args.get("path", "")
                    content = args.get("content", "")
                    full_path = os.path.join(self.config.workspace_dir, rel_path)
                    os.makedirs(os.path.dirname(full_path), exist_ok=True)
                    with open(full_path, "w", encoding="utf-8") as f:
                        f.write(content)
                    observation = f"[Success] Wrote {len(content)} bytes to {rel_path}."
                elif tool_name == "spawn_subagent":
                    role = args.get("role", "worker")
                    prompt = args.get("prompt", "")
                    handle_id = self.delegation_engine.spawn_agent(role, prompt)
                    observation = f"[Delegation] Spawned child agent '{handle_id}' with role '{role}'."
                else:
                    observation = f"[Error] Unknown tool '{tool_name}'."

            # 闭合账本 (Credit Ledger)
            self.ledger.credit_result(call_id, observation or "")
            self.session_stream.append_event(
                "tool_result",
                {"tool_name": tool_name, "output": observation},
                self.turn_counter
            )

            # Post-Tool Hook 审计
            post_ctx = HookContext(
                thread_id=self.session_id,
                event=HookEvent.POST_TOOL_USE,
                tool_name=tool_name,
                payload={"output": observation},
            )
            self.hook_engine.run_event(post_ctx)

            self.history_stream.append(f"Assistant Tool Call: {tool_name}({args})")
            self.history_stream.append(f"Observation: {observation}")

        # 5. 上下文压紧监测 (Context Compaction Check)
        est_tokens = snapshot.compute_total_tokens_estimate()
        compacted = False
        if est_tokens > self.config.token_compaction_threshold:
            self._trigger_compaction()
            compacted = True

        duration_ms = (time.time() - t0) * 1000.0

        return {
            "turn_index": self.turn_counter,
            "session_id": self.session_id,
            "cache_hit": cache_hit,
            "cache_reason": cache_reason.value,
            "estimated_ttft_ms": est_ttft,
            "skill_attached": bool(skill_attachment),
            "tool_blocked": tool_blocked,
            "block_reason": block_reason,
            "observation": observation,
            "compacted": compacted,
            "duration_ms": duration_ms,
        }

    def _trigger_compaction(self) -> None:
        """Performs structured context compaction with semantic anchoring."""
        summary = (
            f"[Living Working Memory Compaction at Turn {self.turn_counter}]\n"
            f"- Retained Session: {self.session_id}\n"
            f"- Workspace: {self.config.workspace_dir}\n"
            f"- History Trimmed: Last {len(self.history_stream)} entries folded into anchor."
        )
        self.cache_validator.trigger_compaction_reset(summary, self.tools_declaration)
        # Keep only the last 4 turns for immediate context continuity
        self.history_stream = [f"System Summary: {summary}"] + self.history_stream[-4:]

    def handle_user_interruption(self, reason: str = "User interrupt (Ctrl+C)") -> None:
        """Ensures synthetic tool results balance the ledger on sudden termination."""
        synthetic_closed = self.ledger.abort_and_synthesize(reason)
        for entry in synthetic_closed:
            self.session_stream.append_event(
                "tool_result",
                {"tool_name": entry.tool_name, "output": entry.result_payload, "synthetic": True},
                self.turn_counter,
            )
        self.ledger.assert_ledger_balanced()

    def export_session_jsonl(self) -> str:
        """Exports complete session history as Pi-style append-only JSONL."""
        return self.session_stream.serialize_to_jsonl()

    def close(self) -> None:
        """Gracefully closes session, releases compute, and validates invariants."""
        if self._session_started:
            ctx = HookContext(thread_id=self.session_id, event=HookEvent.STOP)
            self.hook_engine.run_event(ctx)
        self.sandbox.cleanup()
        self.delegation_engine.assert_no_dangling_handles()
        self.ledger.assert_ledger_balanced()
