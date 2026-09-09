"""
mini-harness v0.1 核心执行驱动引擎 (Core Engine)

集成:
1. ReAct 原生驱动循环 (Thought -> Action -> Observation)
2. 动态工具掩码 (Dynamic Tool Masking): 物理屏蔽连续振荡工具，强制切断注意力锁死
3. 检查点机制与回滚 (Checkpoint & Rollback): 防御上下文毒化
4. 错误分级分诊与自省自愈 (Error Taxonomy & Self-Correction)
5. 运行期全链路度量与统计 (Execution Metrics & Trace)
"""

import copy
import hashlib
import json
import os
import random
import sys
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Set, Tuple

from tools import default_registry
from memory import ConversationHistory, memory_manager
from errors import (
    HarnessError,
    ValidationError,
    LLMCallError,
    ToolExecutionError,
    WorkflowError,
    classify_and_format_error,
)


def load_env_file():
    """原生 .env 变量加载器"""
    candidates = [
        os.path.join(os.path.dirname(__file__), ".env"),
        os.path.join(os.path.dirname(__file__), "..", ".env"),
        os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
    ]
    for env_path in candidates:
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())
            break


load_env_file()


class ActionLoopDetector:
    """动作死循环与振荡检测器"""
    def __init__(self, max_consecutive_repeats: int = 2):
        self.max_consecutive_repeats = max_consecutive_repeats
        self.action_history: List[str] = []

    def compute_signature(self, fn_name: str, raw_args: Any) -> str:
        normalized_args = ""
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
                normalized_args = json.dumps(parsed, sort_keys=True)
            except Exception:
                normalized_args = raw_args.strip()
        elif isinstance(raw_args, dict):
            normalized_args = json.dumps(raw_args, sort_keys=True)
        else:
            normalized_args = str(raw_args)

        sig_raw = f"{fn_name}:{normalized_args}"
        return hashlib.md5(sig_raw.encode("utf-8")).hexdigest()

    def record_and_check(self, fn_name: str, raw_args: Any) -> Tuple[bool, int]:
        sig = self.compute_signature(fn_name, raw_args)
        self.action_history.append(sig)

        repeats = 0
        for s in reversed(self.action_history):
            if s == sig:
                repeats += 1
            else:
                break

        is_stuck = repeats >= self.max_consecutive_repeats
        return is_stuck, repeats

    def reset(self):
        self.action_history.clear()


class HarnessEngine:
    """
    mini-harness v0.1 生产级微内核引擎
    """
    def __init__(
        self,
        session_file: Optional[str] = None,
        max_steps: int = 8,
        temperature: float = 0.2,
        max_llm_retries: int = 3
    ):
        self.api_key = os.getenv("OPENAI_API_KEY", "lm-studio")
        self.base_url = os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:12340/v1")
        self.model = os.getenv("OPENAI_MODEL", "qwen/qwen3.8-27b")
        self.registry = default_registry
        self.session_file = session_file
        self.max_steps = max_steps
        self.temperature = temperature
        self.max_llm_retries = max_llm_retries

        self.loop_detector = ActionLoopDetector(max_consecutive_repeats=2)
        self.masked_tools: Set[str] = set()  # 动态工具掩码黑名单
        self.checkpoint_history: Optional[List[Dict[str, Any]]] = None

        # 统计度量
        self.stats = {
            "total_goals": 0,
            "total_steps": 0,
            "total_tool_calls": 0,
            "total_self_corrections": 0,
            "total_masking_events": 0,
            "total_llm_time_sec": 0.0,
        }

        # 编译完整 System Prompt
        self._init_history()

    def _init_history(self):
        base_prompt = (
            "You are mini-harness v0.1, an autonomous AI software engineer driven by ReAct.\n"
            "You operate with high discipline, robust error resilience, and self-healing.\n\n"
            "OPERATING PROTOCOL:\n"
            "1. REASON BEFORE ACTION: Formulate explicit Thought before calling any tool.\n"
            "2. ADAPT TO ERRORS: When receiving a [ToolExecutionError] with DIAGNOSTIC HINT, "
            "never repeat the same call. Change parameters or use inspection tools (like list_dir).\n"
            "3. RESPECT TOOL MASKING: If a tool is unavailable, it has been temporarily disabled "
            "by the host due to oscillation. Pivot to alternative strategies immediately.\n"
            "4. TERMINATE CLEANLY: When the goal is completed, output a clear, structured final answer "
            "WITHOUT any tool calls.\n"
            "5. LANGUAGE: Default to Chinese unless requested otherwise."
        )
        memory_addon = memory_manager.render_system_prompt_addon()
        full_system_prompt = base_prompt + ("\n" + memory_addon if memory_addon else "")

        self.history = ConversationHistory(system_prompt=full_system_prompt)
        if self.session_file and os.path.exists(self.session_file):
            self.history.load(self.session_file)

    def create_checkpoint(self):
        """保存当前会话历史快照"""
        self.checkpoint_history = copy.deepcopy(self.history.messages)

    def rollback(self) -> bool:
        """回滚到上一快照，彻底清除中毒上下文"""
        if self.checkpoint_history is not None:
            self.history.messages = copy.deepcopy(self.checkpoint_history)
            self.masked_tools.clear()
            self.loop_detector.reset()
            if self.session_file:
                self.history.save(self.session_file)
            return True
        return False

    def clear_session(self):
        """清空当前工作记忆，保留长期记忆"""
        self._init_history()
        self.masked_tools.clear()
        self.loop_detector.reset()
        if self.session_file and os.path.exists(self.session_file):
            os.remove(self.session_file)

    def _get_active_schemas(self) -> List[Dict[str, Any]]:
        """获取当前激活状态的工具 Schema (应用动态工具掩码 Tool Masking)"""
        all_schemas = self.registry.get_schemas()
        if not self.masked_tools:
            return all_schemas
        return [
            s for s in all_schemas
            if s["function"]["name"] not in self.masked_tools
        ]

    def _call_llm_with_retry(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """向 LLM 发送请求，包含指数退避与活跃工具过滤"""
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        active_tools = self._get_active_schemas()

        payload = {
            "model": self.model,
            "messages": messages,
            "tools": active_tools if active_tools else None,
            "tool_choice": "auto" if active_tools else "none",
            "temperature": self.temperature,
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        last_error = None
        for attempt in range(1, self.max_llm_retries + 1):
            try:
                t0 = time.time()
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=180) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    self.stats["total_llm_time_sec"] += (time.time() - t0)
                    return res
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_error = e
                if attempt < self.max_llm_retries:
                    backoff = (2 ** (attempt - 1)) + random.uniform(0.1, 0.4)
                    time.sleep(backoff)
                else:
                    raise LLMCallError(f"LLM call failed after {self.max_llm_retries} attempts: {str(last_error)}")
            except Exception as e:
                raise LLMCallError(f"Unexpected LLM error: {str(e)}")

        raise LLMCallError(f"Exhausted retries: {str(last_error)}")

    def run(self, goal: str, max_steps: Optional[int] = None) -> Dict[str, Any]:
        """执行 ReAct 主循环"""
        self.create_checkpoint()  # 任务前保存快照
        effective_max_steps = max_steps or self.max_steps
        self.loop_detector.reset()
        self.masked_tools.clear()  # 新任务清空临时掩码
        self.history.add_user_message(goal)

        trace: List[Dict[str, Any]] = []
        step = 0
        final_answer = ""
        termination_reason = "completed"
        self_corrections = 0
        last_step_had_error = False

        self.stats["total_goals"] += 1

        print(f"\n🎯 [Goal]: {goal}")
        print(f"🛡️ [mini-harness v0.1]: Max Steps = {effective_max_steps} | Dynamic Tool Masking = ON | Self-Healing = ON")
        print("-" * 68)

        while step < effective_max_steps:
            step += 1
            self.stats["total_steps"] += 1
            step_start = time.time()

            active_names = [s["function"]["name"] for s in self._get_active_schemas()]
            mask_status = f" (Masked: {list(self.masked_tools)})" if self.masked_tools else ""
            print(f"\n🌀 [Step {step}/{effective_max_steps}] Thinking...{mask_status}")

            messages_payload = self.history.get_messages(max_turns=16)

            try:
                resp = self._call_llm_with_retry(messages_payload)
            except LLMCallError as e:
                print(f"❌ [Fatal LLMCallError]: {e.message}")
                termination_reason = f"llm_error: {e.message}"
                break

            choice = resp["choices"][0]
            finish_reason = choice.get("finish_reason")
            msg = choice["message"]
            thought = msg.get("content", "").strip()
            tool_calls = msg.get("tool_calls") or []

            # 协议强校验：拦截截断伪完成
            if finish_reason == "length":
                print(f"⛔ [Protocol Guard]: 模型输出被 Token 长度截断 (finish_reason='length')！")
                termination_reason = "context_length_exceeded"
                self.history.add_assistant_message(msg)
                break

            self.history.add_assistant_message(msg)
            if thought:
                print(f"💭 [Thought]: {thought}")

            # 自愈检测
            if last_step_had_error and (tool_calls or not last_step_had_error):
                print(f"🌱 [Self-Correction]: 模型成功吸取上一轮报错经验，推进修正策略！")
                self_corrections += 1
                self.stats["total_self_corrections"] += 1
                last_step_had_error = False

            # 任务达成：无工具调用且为自然 stop
            if not tool_calls:
                final_answer = thought
                termination_reason = "goal_reached"
                print(f"✨ [Final Answer Achieved in Step {step}]")
                break

            # 执行工具
            step_actions = []
            for call in tool_calls:
                self.stats["total_tool_calls"] += 1
                call_id = call.get("id", f"call_{step}_{int(time.time()*1000)%10000}")
                fn_name = call["function"]["name"]
                raw_args = call["function"]["arguments"]

                # 振荡检测与动态工具掩码
                is_stuck, repeats = self.loop_detector.record_and_check(fn_name, raw_args)
                if is_stuck:
                    # 触发 Dynamic Tool Masking：临时将此工具拔掉！
                    self.masked_tools.add(fn_name)
                    self.stats["total_masking_events"] += 1
                    print(f"🚨 [Dynamic Tool Masking]: 工具 '{fn_name}' 触发振荡熔断！已在下一步从 tools 列表物理剥夺权限。")

                    tool_output = (
                        f"[WorkflowError: DynamicToolMasking] Tool '{fn_name}' has been TEMPORARILY DISABLED "
                        f"because you called it {repeats} times with the exact same failing arguments. "
                        f"You CANNOT use '{fn_name}' in your next step. "
                        f"Please examine available alternative tools or synthesize your conclusion."
                    )
                    last_step_had_error = True
                else:
                    # 如果模型成功调用了其他工具，尝试解除先前的掩码
                    if fn_name not in self.masked_tools and self.masked_tools:
                        print(f"🔓 [Mask Released]: 模型成功转向新工具 '{fn_name}'，解冻受限工具。")
                        self.masked_tools.clear()

                    print(f"🛠️ [Action]: {fn_name}(args={raw_args})")
                    tool_output = self.registry.dispatch(fn_name, raw_args)
                    if "[ToolExecutionError" in tool_output or "Error:" in tool_output:
                        last_step_had_error = True

                preview = tool_output[:140].replace("\n", " ")
                print(f"👀 [Observation]: {preview}{'...' if len(tool_output) > 140 else ''}")

                self.history.add_tool_result(call_id, fn_name, tool_output)
                step_actions.append({
                    "tool": fn_name,
                    "arguments": raw_args,
                    "preview": preview
                })

            step_latency = time.time() - step_start
            trace.append({
                "step": step,
                "thought": thought,
                "actions": step_actions,
                "latency_sec": round(step_latency, 2)
            })

        # 步数硬熔断
        if step >= effective_max_steps and not final_answer:
            termination_reason = "max_steps_exceeded"
            print(f"\n⛔ [Harness Guard]: 达到最大步数限制 ({effective_max_steps} steps)，强制总结收敛！")
            self.history.add_user_message(
                "System: Max steps reached. Output your best final summary without calling any more tools."
            )
            try:
                resp = self._call_llm_with_retry(self.history.get_messages(max_turns=16))
                final_answer = resp["choices"][0]["message"].get("content", "执行达到上限。")
            except Exception as e:
                final_answer = f"强制收敛异常: {str(e)}"

        if self.session_file:
            self.history.save(self.session_file)

        print("-" * 68)
        return {
            "goal": goal,
            "final_answer": final_answer,
            "total_steps": step,
            "termination_reason": termination_reason,
            "self_corrections": self_corrections,
            "masked_events": self.stats["total_masking_events"],
            "trace": trace
        }
