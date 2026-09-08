"""
mini-harness Day 2: 具备双层记忆的多轮智能体 (Multi-Turn Stateful Agent)

核心能力:
1. 短期工作记忆 (Working Memory): 维护严谨的消息时序拓扑，支持多轮连续追问与上下文引用
2. 长期事实记忆 (Long-Term Memory): 自主调用 save_fact 写入磁盘，并在下次开机时自动注入 System Prompt
"""

import json
import os
import sys
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional
from tools import default_registry
from memory import ConversationHistory, memory_manager


def load_env_file():
    """原生 .env 读取器"""
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


class Day2Agent:
    def __init__(self, session_file: Optional[str] = None):
        self.api_key = os.getenv("OPENAI_API_KEY", "lm-studio")
        self.base_url = os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:12340/v1")
        self.model = os.getenv("OPENAI_MODEL", "qwen/qwen3.8-27b")
        self.registry = default_registry
        self.session_file = session_file

        # 装配基础系统人设 + 长期记忆事实
        base_prompt = (
            "You are a sophisticated AI coding assistant with long-term memory capabilities.\n"
            "You have access to tools for filesystem access, shell execution, and persistent memory.\n"
            "RULE 1: When the user shares important personal facts, preferences, or technical configurations, "
            "proactively call the 'save_fact' tool to remember them permanently.\n"
            "RULE 2: Use Chinese when replying to the user unless asked otherwise."
        )
        memory_addon = memory_manager.render_system_prompt_addon()
        full_system_prompt = base_prompt + ("\n" + memory_addon if memory_addon else "")

        self.history = ConversationHistory(system_prompt=full_system_prompt)
        if session_file and os.path.exists(session_file):
            self.history.load(session_file)

    def _call_llm(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """向 LLM 端点发送请求"""
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": self.registry.get_schemas(),
            "tool_choice": "auto",
            "temperature": 0.3
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def step(self, user_input: str) -> str:
        """执行一轮交互，可能包含多步工具调用"""
        self.history.add_user_message(user_input)

        # 单轮最大循环步数，防止死循环
        max_substeps = 5
        substep = 0

        while substep < max_substeps:
            substep += 1
            messages_payload = self.history.get_messages(max_turns=12)
            resp = self._call_llm(messages_payload)

            choice = resp["choices"][0]
            msg = choice["message"]
            tool_calls = msg.get("tool_calls")

            # 无论模型是说话还是调工具，先将 assistant 消息严格入栈
            self.history.add_assistant_message(msg)

            if not tool_calls:
                # 模型完成本次回答，终止内部循环
                reply = msg.get("content", "")
                if self.session_file:
                    self.history.save(self.session_file)
                return reply

            # 遍历并执行所有工具调用
            for call in tool_calls:
                call_id = call.get("id", f"call_{substep}")
                fn_name = call["function"]["name"]
                raw_args = call["function"]["arguments"]

                print(f"   ⚙️ [Tool Action]: {fn_name}(args={raw_args})")
                tool_output = self.registry.dispatch(fn_name, raw_args)
                preview = tool_output[:100].replace("\n", " ")
                print(f"   📥 [Observation]: {preview}{'...' if len(tool_output) > 100 else ''}")

                # 必须将每一个 tool 响应以严格的 tool_call_id 装入历史
                self.history.add_tool_result(call_id, fn_name, tool_output)

        if self.session_file:
            self.history.save(self.session_file)
        return "执行达到单轮最大步数限制。"

    def interactive_loop(self):
        """命令行交互式 REPL 循环"""
        print("\n" + "=" * 62)
        print(" 🧠 mini-harness Day 2: 多轮记忆智能体交互终端")
        print(f" 🔌 端点: {self.base_url} (Model: {self.model})")
        print(" 💡 提示: 随意闲聊、告诉它你的偏好、或者让它查文件。输入 'exit' 或 'q' 退出。")
        print("=" * 62)

        # 展示当前已有的长期记忆
        known = memory_manager.query_facts()
        if known and "No persistent" not in known:
            print(f"📂 [已加载的长期记忆]:\n{known}\n" + "-" * 62)

        while True:
            try:
                user_input = input("\n👤 You: ").strip()
                if not user_input:
                    continue
                if user_input.lower() in ("exit", "quit", "q"):
                    print("\n👋 会话结束。长期记忆已持久化。再见！\n")
                    break

                reply = self.step(user_input)
                print(f"\n🤖 Agent: {reply}")

            except KeyboardInterrupt:
                print("\n\n👋 会话中断退出。")
                break
            except Exception as e:
                print(f"\n❌ 执行出错: {str(e)}")


if __name__ == "__main__":
    session_cache = os.path.join(os.path.dirname(__file__), "..", "data", "current_session.json")
    agent = Day2Agent(session_file=session_cache)

    if len(sys.argv) > 1:
        single_query = " ".join(sys.argv[1:])
        ans = agent.step(single_query)
        print(f"\n🤖 Agent: {ans}\n")
    else:
        agent.interactive_loop()
