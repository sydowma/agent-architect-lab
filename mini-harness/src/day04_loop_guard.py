"""
mini-harness Day 4: 具备自省自愈能力的鲁棒性智能体 (Self-Healing ReAct Agent)

核心能力:
1. 工业级错误分类体系集成 (ValidationError, LLMCallError, ToolExecutionError, WorkflowError)
2. LLM 指数退避重试 (Exponential Backoff with Jitter) 防御网络与服务过载
3. 工具异常语义诊断回灌 (Diagnostic Hints) 启发模型自省自愈 (Self-Correction)
4. 严格协议状态机校验 (区分 finish_reason='stop' 与 'length' 截断)
5. 结构化自愈追踪器 (Self-Correction Tracker)
"""

import hashlib
import json
import os
import random
import sys
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

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
    """动作死循环与振荡检测器 (Loop Guard)"""
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


class Day4Agent:
    """
    Day 4: 具备自省自愈机制与工业级防御的 ReAct 智能体
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

        # 强化了自愈指导规范的 System Prompt
        base_prompt = (
            "You are an expert autonomous AI agent driven by ReAct and equipped with Self-Healing capabilities.\n"
            "Your objective is to solve multi-step problems through disciplined iteration and error resilience.\n\n"
            "CORE OPERATING PRINCIPLES:\n"
            "1. THOUGHT FIRST: Before invoking tools, clearly explain your reasoning in text. "
            "State what you have learned and why a specific tool is needed next.\n"
            "2. ACTION: Call only the tools necessary for the current subtask. Never guess information when a tool is available.\n"
            "3. OBSERVATION & DIAGNOSIS: Carefully inspect the tool output. If you encounter a [ToolExecutionError], "
            "do NOT panic and do NOT repeat the identical failing command. Read the 'DIAGNOSTIC HINT' thoroughly, "
            "analyze the root cause, and formulate a corrective action in your next Thought (e.g. check directory first, fix path).\n"
            "4. TERMINATION: When the overall goal is fully satisfied, formulate a comprehensive final answer "
            "WITHOUT calling any more tools.\n"
            "5. NO BLIND RETRIES: If a tool call fails, pivot to an exploratory or alternative approach.\n"
            "6. LANGUAGE: Respond in Chinese unless requested otherwise."
        )

        memory_addon = memory_manager.render_system_prompt_addon()
        full_system_prompt = base_prompt + ("\n" + memory_addon if memory_addon else "")

        self.history = ConversationHistory(system_prompt=full_system_prompt)
        if session_file and os.path.exists(session_file):
            self.history.load(session_file)

    def _call_llm_with_retry(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        带指数退避 (Exponential Backoff with Jitter) 的原生 LLM HTTP 调用
        抵御网络瞬间抖动与本地模型加载排队
        """
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": self.registry.get_schemas(),
            "tool_choice": "auto",
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
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=90) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_error = e
                if attempt < self.max_llm_retries:
                    # 指数退避 + 抖动: base 1s -> 2s -> 4s
                    backoff = (2 ** (attempt - 1)) + random.uniform(0.1, 0.5)
                    print(f"⚠️ [LLMCall Warning]: 尝试 {attempt}/{self.max_llm_retries} 失败 ({str(e)}). {backoff:.2f}s 后重试...")
                    time.sleep(backoff)
                else:
                    raise LLMCallError(
                        f"LLM call failed after {self.max_llm_retries} attempts: {str(last_error)}",
                        details={"attempts": self.max_llm_retries, "error": str(last_error)}
                    )
            except Exception as e:
                raise LLMCallError(f"Unexpected error calling LLM: {str(e)}", details={"error": str(e)})

        raise LLMCallError(f"Exhausted retries: {str(last_error)}")

    def run(self, goal: str, max_steps: Optional[int] = None) -> Dict[str, Any]:
        """
        执行带自愈感知的 ReAct 自主求解循环
        """
        effective_max_steps = max_steps or self.max_steps
        self.loop_detector.reset()
        self.history.add_user_message(goal)

        trace: List[Dict[str, Any]] = []
        step = 0
        final_answer = ""
        termination_reason = "completed"
        self_correction_events: List[Dict[str, Any]] = []
        last_step_had_error = False
        last_failed_tool = ""

        print(f"\n🎯 [Goal]: {goal}")
        print(f"🛡️ [Harness Guard]: Max Steps = {effective_max_steps} | Self-Healing = ON | Protocol Verify = ON")
        print("-" * 65)

        while step < effective_max_steps:
            step += 1
            step_start = time.time()
            print(f"\n🌀 [Step {step}/{effective_max_steps}] Thinking...")

            # 1. 组装上下文
            messages_payload = self.history.get_messages(max_turns=16)

            # 2. LLM 推理 (含指数重试防御)
            try:
                resp = self._call_llm_with_retry(messages_payload)
            except LLMCallError as e:
                print(f"❌ [LLMCallError]: {e.message}")
                termination_reason = f"llm_call_error: {e.message}"
                break

            choice = resp["choices"][0]
            finish_reason = choice.get("finish_reason")
            msg = choice["message"]
            thought = msg.get("content", "").strip()
            tool_calls = msg.get("tool_calls") or []

            # 3. 严格协议状态机校验 (Protocol Guard)
            if finish_reason == "length":
                print(f"⛔ [Protocol Alert]: 模型生成被 Context/Max Tokens 截断 (finish_reason='length')！")
                termination_reason = "context_length_exceeded"
                # 记录截断消息并退出
                self.history.add_assistant_message(msg)
                break

            # 记录 Thought 消息入栈
            self.history.add_assistant_message(msg)

            if thought:
                print(f"💭 [Thought]: {thought}")

            # 4. 检查自愈标记：如果上一步工具报错了，而本步模型做出了修正并给出了新动作/结论
            if last_step_had_error:
                print(f"🌱 [Self-Correction Event Detected]: 模型识别到上一步 '{last_failed_tool}' 的失败，正在自主纠偏推演！")
                self_correction_events.append({
                    "step": step,
                    "previous_failed_tool": last_failed_tool,
                    "new_action_count": len(tool_calls),
                    "correction_thought": thought
                })
                last_step_had_error = False

            # 5. 终止条件判定：无工具调用且 finish_reason 为 stop
            if not tool_calls:
                final_answer = thought
                termination_reason = "goal_reached"
                print(f"✨ [Final Answer Achieved in Step {step}]")
                break

            # 6. 执行 Action 并捕获 Observation
            step_actions = []
            for call in tool_calls:
                call_id = call.get("id", f"call_{step}_{int(time.time()*1000)%10000}")
                fn_name = call["function"]["name"]
                raw_args = call["function"]["arguments"]

                # 6.1 振荡与死循环检测 (Loop Guard)
                is_stuck, repeats = self.loop_detector.record_and_check(fn_name, raw_args)
                if is_stuck:
                    print(f"⚠️ [Loop Guard Alert]: 检测到动作振荡！工具 '{fn_name}' 连续相同参数调用 {repeats} 次。")
                    tool_output = (
                        f"[WorkflowError: ActionOscillation] SYSTEM WARNING: You have repeatedly called '{fn_name}' "
                        f"with the identical arguments {repeats} times without making progress. Stop repeating! "
                        f"Rethink your strategy, use alternative inspection tools, or formulate your final answer."
                    )
                    last_step_had_error = True
                    last_failed_tool = fn_name
                else:
                    # 6.2 派发执行工具 (具备底层异常自动捕获与诊断注入)
                    print(f"🛠️ [Action]: {fn_name}(args={raw_args})")
                    tool_output = self.registry.dispatch(fn_name, raw_args)

                    # 判断工具输出中是否包含错误标识
                    if "[ToolExecutionError" in tool_output or "Error:" in tool_output:
                        last_step_had_error = True
                        last_failed_tool = fn_name
                        print(f"🚨 [Tool Exception Caught & Diagnosed]")

                # 6.3 观测展示与记录
                preview = tool_output[:140].replace("\n", " ")
                print(f"👀 [Observation]: {preview}{'...' if len(tool_output) > 140 else ''}")

                # 6.4 消息回灌
                self.history.add_tool_result(call_id, fn_name, tool_output)

                step_actions.append({
                    "tool": fn_name,
                    "arguments": raw_args,
                    "has_error": last_step_had_error,
                    "output_preview": preview
                })

            step_latency = time.time() - step_start
            trace.append({
                "step": step,
                "thought": thought,
                "actions": step_actions,
                "latency_sec": round(step_latency, 2)
            })

        # 若达到最大步数硬截断
        if step >= effective_max_steps and not final_answer:
            termination_reason = "max_steps_exceeded"
            print(f"\n⛔ [Harness Guard Triggered]: 达到最大步数限制 ({effective_max_steps} steps)，强制自愈总结！")
            self.history.add_user_message(
                "System: You have reached the maximum allowed execution steps. "
                "Do NOT call any more tools. Provide the best possible final summary based on what you have discovered so far."
            )
            try:
                resp = self._call_llm_with_retry(self.history.get_messages(max_turns=16))
                final_msg = resp["choices"][0]["message"]
                self.history.add_assistant_message(final_msg)
                final_answer = final_msg.get("content", "未能得出最终结论。")
            except Exception as e:
                final_answer = f"强制总结发生异常: {str(e)}"

        # 保存会话状态
        if self.session_file:
            self.history.save(self.session_file)

        print("-" * 65)
        return {
            "goal": goal,
            "final_answer": final_answer,
            "total_steps": step,
            "termination_reason": termination_reason,
            "self_correction_count": len(self_correction_events),
            "self_correction_events": self_correction_events,
            "trace": trace
        }

    def interactive_loop(self):
        """交互式终端 REPL"""
        print("\n" + "=" * 65)
        print(" 🧠 mini-harness Day 4: 具备自省自愈能力的 ReAct 终端")
        print(f" 🔌 端点: {self.base_url} (Model: {self.model})")
        print(" 🛡️ 防御: 指数退避重试 | 异常语义诊断 | 状态机强校验 | 动作自愈追踪")
        print(" 💡 随时输入任务，输入 'q' 退出")
        print("=" * 65)

        while True:
            try:
                goal = input("\n🎯 Goal: ").strip()
                if not goal:
                    continue
                if goal.lower() in ("exit", "quit", "q"):
                    print("\n👋 智能体退出。")
                    break

                res = self.run(goal)
                print(f"\n🏁 [Result ({res['termination_reason']}, {res['total_steps']} steps, {res['self_correction_count']} 自愈)]:\n{res['final_answer']}\n")

            except KeyboardInterrupt:
                print("\n\n👋 会话已中断。")
                break
            except Exception as e:
                print(f"\n❌ 运行时异常: {str(e)}")


if __name__ == "__main__":
    session_path = os.path.join(os.path.dirname(__file__), "..", "data", "day4_session.json")
    agent = Day4Agent(session_file=session_path, max_steps=8)

    if len(sys.argv) > 1:
        user_goal = " ".join(sys.argv[1:])
        agent.run(user_goal)
    else:
        agent.interactive_loop()
