"""
mini-harness Day 3: 原生 ReAct 驱动引擎与工业级死循环防御 (ReAct Agent with Loop Guard)

核心能力:
1. ReAct 自主求解循环 (Thought -> Action -> Observation)
2. 死循环熔断机制 (Max Steps 硬上限 + Action 连续重复调用振荡检测)
3. 结构化 Execution Trace 追踪 (步骤耗时、工具执行状态与截断观测)
4. 深度复用双层记忆与基础工具链 (支持 read/write/list/calc/shell)
"""

import hashlib
import json
import os
import sys
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

from tools import default_registry
from memory import ConversationHistory, memory_manager


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
    """
    动作死循环与振荡检测器 (Loop Guard)
    通过哈希指纹追踪模型最近发起的工具调用，防止模型陷入参数停滞或无效重试。
    """
    def __init__(self, max_consecutive_repeats: int = 2):
        self.max_consecutive_repeats = max_consecutive_repeats
        self.action_history: List[str] = []

    def compute_signature(self, fn_name: str, raw_args: Any) -> str:
        """为 (函数名 + 规范化参数) 计算唯一指纹"""
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
        """
        记录当前动作并检测是否触发死循环。
        返回: (is_loop_detected, repeat_count)
        """
        sig = self.compute_signature(fn_name, raw_args)
        self.action_history.append(sig)

        # 检查尾部连续相同签名的数量
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


class ReActAgent:
    """
    原生 ReAct 智能体
    自主运行 Thought -> Action -> Observation 驱动循环，直至任务达成或触发安全熔断。
    """
    def __init__(
        self,
        session_file: Optional[str] = None,
        max_steps: int = 8,
        temperature: float = 0.2
    ):
        self.api_key = os.getenv("OPENAI_API_KEY", "lm-studio")
        self.base_url = os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:12340/v1")
        self.model = os.getenv("OPENAI_MODEL", "qwen/qwen3.8-27b")
        self.registry = default_registry
        self.session_file = session_file
        self.max_steps = max_steps
        self.temperature = temperature
        self.loop_detector = ActionLoopDetector(max_consecutive_repeats=2)

        # 组装 ReAct 控制面 System Prompt
        base_prompt = (
            "You are an expert autonomous AI agent driven by the ReAct (Reasoning + Acting) methodology.\n"
            "Your objective is to solve multi-step problems through disciplined iteration.\n\n"
            "OPERATING PRINCIPLES:\n"
            "1. THOUGHT FIRST: Before invoking tools, explicitly explain your reasoning in text. "
            "State what you have learned and why a specific tool is needed next.\n"
            "2. ACTION: Call only the tools necessary for the current subtask. Never guess information when a tool is available.\n"
            "3. OBSERVATION: Carefully inspect the tool output returned by the host. Adapt your hypothesis if errors occur.\n"
            "4. TERMINATION: When the overall goal is fully satisfied, formulate a clear, comprehensive final answer WITHOUT calling any more tools.\n"
            "5. NO ENDLESS RETRIES: If a tool fails twice with the same inputs, change your approach instead of repeating.\n"
            "6. LANGUAGE: Respond in Chinese unless requested otherwise."
        )

        memory_addon = memory_manager.render_system_prompt_addon()
        full_system_prompt = base_prompt + ("\n" + memory_addon if memory_addon else "")

        self.history = ConversationHistory(system_prompt=full_system_prompt)
        if session_file and os.path.exists(session_file):
            self.history.load(session_file)

    def _call_llm(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """原生 HTTP 请求 LLM 端点"""
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

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def run(self, goal: str, max_steps: Optional[int] = None) -> Dict[str, Any]:
        """
        执行 ReAct 自主求解主循环
        返回结构化执行结果字典与执行轨迹 (Trace)
        """
        effective_max_steps = max_steps or self.max_steps
        self.loop_detector.reset()
        self.history.add_user_message(goal)

        trace: List[Dict[str, Any]] = []
        step = 0
        final_answer = ""
        termination_reason = "completed"

        print(f"\n🎯 [Goal]: {goal}")
        print(f"🛡️ [Harness Guard]: Max Steps = {effective_max_steps}, Loop Detection = ON")
        print("-" * 65)

        while step < effective_max_steps:
            step += 1
            step_start = time.time()
            print(f"\n🌀 [Step {step}/{effective_max_steps}] Thinking...")

            # 1. 获取包含历史上下文的消息体
            messages_payload = self.history.get_messages(max_turns=16)

            # 2. 模型推理 (Thought + 可能的 Action)
            try:
                resp = self._call_llm(messages_payload)
            except Exception as e:
                print(f"❌ [LLM Call Error]: {str(e)}")
                termination_reason = f"llm_error: {str(e)}"
                break

            choice = resp["choices"][0]
            msg = choice["message"]
            thought = msg.get("content", "").strip()
            tool_calls = msg.get("tool_calls") or []

            # 记录 Thought 消息入栈
            self.history.add_assistant_message(msg)

            if thought:
                print(f"💭 [Thought]: {thought}")

            # 3. 检查终止条件：若模型未发起 tool_calls，则代表达成最终解答
            if not tool_calls:
                final_answer = thought
                termination_reason = "goal_reached"
                print(f"✨ [Final Answer Achieved in Step {step}]")
                break

            # 4. 执行 Action 并捕获 Observation
            step_actions = []
            for call in tool_calls:
                call_id = call.get("id", f"call_{step}_{int(time.time()*1000)%10000}")
                fn_name = call["function"]["name"]
                raw_args = call["function"]["arguments"]

                # 4.1 振荡检测 (Loop Guard)
                is_stuck, repeats = self.loop_detector.record_and_check(fn_name, raw_args)
                if is_stuck:
                    print(f"⚠️ [Loop Guard Alert]: 检测到动作振荡！工具 '{fn_name}' 连续相同参数调用 {repeats} 次。")
                    tool_output = (
                        f"SYSTEM WARNING: You have repeatedly called '{fn_name}' with the identical arguments "
                        f"{repeats} times without progress. Stop repeating! Analyze why previous attempts did not "
                        f"satisfy your need, rethink your approach, or provide your final answer."
                    )
                else:
                    # 4.2 派发执行工具
                    print(f"🛠️ [Action]: {fn_name}(args={raw_args})")
                    t_tool_start = time.time()
                    tool_output = self.registry.dispatch(fn_name, raw_args)
                    t_tool_cost = time.time() - t_tool_start

                # 4.3 观测展示与记录
                preview = tool_output[:120].replace("\n", " ")
                print(f"👀 [Observation]: {preview}{'...' if len(tool_output) > 120 else ''}")

                # 4.4 消息回灌
                self.history.add_tool_result(call_id, fn_name, tool_output)

                step_actions.append({
                    "tool": fn_name,
                    "arguments": raw_args,
                    "output_length": len(tool_output),
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
            print(f"\n⛔ [Harness Guard Triggered]: 达到单次任务最大步数限制 ({effective_max_steps} steps)，强制收敛！")
            # 强制要求模型依据当前上下文输出结论
            self.history.add_user_message(
                "System: You have reached the maximum allowed execution steps. "
                "Do NOT call any more tools. Provide the best possible final summary based on what you have discovered so far."
            )
            try:
                resp = self._call_llm(self.history.get_messages(max_turns=16))
                final_msg = resp["choices"][0]["message"]
                self.history.add_assistant_message(final_msg)
                final_answer = final_msg.get("content", "未能得出最终结论。")
            except Exception as e:
                final_answer = f"强制收敛阶段发生错误: {str(e)}"

        # 保存会话状态
        if self.session_file:
            self.history.save(self.session_file)

        print("-" * 65)
        return {
            "goal": goal,
            "final_answer": final_answer,
            "total_steps": step,
            "termination_reason": termination_reason,
            "trace": trace
        }

    def interactive_loop(self):
        """交互式 ReAct REPL"""
        print("\n" + "=" * 65)
        print(" 🧠 mini-harness Day 3: ReAct 自主智能体终端 (Autonomous ReAct Loop)")
        print(f" 🔌 端点: {self.base_url} (Model: {self.model})")
        print(" 🛡️ 防御: Max Steps = 8 | 连续动作振荡阻断 | 自动记忆沉淀")
        print(" 💡 输入任务目标（如让它调查目录、计算并写总结文件），输入 'q' 退出")
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
                print(f"\n🏁 [Result ({res['termination_reason']}, {res['total_steps']} steps)]:\n{res['final_answer']}\n")

            except KeyboardInterrupt:
                print("\n\n👋 会话已中断。")
                break
            except Exception as e:
                print(f"\n❌ 运行时异常: {str(e)}")


if __name__ == "__main__":
    session_path = os.path.join(os.path.dirname(__file__), "..", "data", "day3_session.json")
    agent = ReActAgent(session_file=session_path, max_steps=8)

    if len(sys.argv) > 1:
        user_goal = " ".join(sys.argv[1:])
        agent.run(user_goal)
    else:
        agent.interactive_loop()
