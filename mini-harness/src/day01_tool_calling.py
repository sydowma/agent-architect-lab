"""
mini-harness Day 1: 最小工具调用智能体 (Minimal Tool-Calling Agent)

核心教学点:
1. 观察 LLM (如本地 LM Studio 中的 qwen/qwen3.8-27b) 如何输出 tool_calls
2. 宿主如何拦截调用、执行本地函数，并将结果装配为 role="tool" 消息回灌给模型
3. 零三方依赖，支持自动读取 .env 配置
"""

import json
import os
import sys
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional
from tools import default_registry


def load_env_file():
    """轻量级原生 .env 读取器，无需额外安装 python-dotenv"""
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


class Day1Agent:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or "lm-studio"
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL") or "http://127.0.0.1:12340/v1"
        self.model = model or os.getenv("OPENAI_MODEL") or "qwen/qwen3.8-27b"
        self.registry = default_registry

    def _call_real_llm(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        """调用兼容 OpenAI 规范的端点 (纯原生 urllib 实现，支持 LM Studio / OpenAI / DeepSeek)"""
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.2
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"API 请求失败 HTTP {e.code}: {err_body}")
        except Exception as e:
            raise RuntimeError(f"连接模型端点失败 ({url}): {str(e)}")

    def chat_step(self, user_query: str):
        """执行一次完整的工具决策与调用交互"""
        print("\n" + "=" * 60)
        print(f"👤 [User Query]: {user_query}")
        print(f"🔌 [Model Endpoint]: {self.base_url} (Model: {self.model})")
        print("=" * 60)

        # 1. 组装上下文与可用工具契约
        system_prompt = (
            "You are a helpful coding assistant. You have access to local tools.\n"
            "When asked to inspect files or run commands, call the appropriate tools.\n"
            "After receiving tool outputs, answer the user's question clearly in Chinese."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query}
        ]
        tools_schema = self.registry.get_schemas()

        print(f"📦 [Context]: 注入了 {len(tools_schema)} 个工具契约 (JSON Schema):")
        for s in tools_schema:
            fn = s["function"]
            print(f"   - {fn['name']}: {fn['description']}")

        # 2. 发起第一轮模型推理
        print(f"\n🤖 [LLM]: 正在请求本地模型 ({self.model})...")
        resp = self._call_real_llm(messages, tools_schema)

        msg = resp["choices"][0]["message"]
        tool_calls = msg.get("tool_calls")

        # 3. 检查模型决策：是直接回复，还是发起了工具调用？
        if not tool_calls:
            print(f"\n💬 [Assistant Direct Reply]:\n{msg.get('content')}")
            return

        print(f"\n⚡ [LLM Decision]: 模型决定调用 {len(tool_calls)} 个工具！")
        messages.append(msg)

        # 4. 宿主 Dispatcher 逐个执行工具
        for call in tool_calls:
            call_id = call.get("id", "call_1")
            fn_name = call["function"]["name"]
            raw_args = call["function"]["arguments"]

            print(f"   ⚙️ [Host Execute]: {fn_name}(args={raw_args})")
            tool_output = self.registry.dispatch(fn_name, raw_args)
            preview = tool_output[:120].replace("\n", " ")
            print(f"   📥 [Host Observation]: {preview}{'...' if len(tool_output) > 120 else ''}")

            # 5. 将结果按 OpenAI 规范组装为 role="tool"
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "name": fn_name,
                "content": tool_output
            })

        # 6. 带上工具的 Observation，发起第二轮模型推理
        print("\n🔄 [Second Inference]: 将工具结果作为 role='tool' 回传给模型总结...")
        final_resp = self._call_real_llm(messages, tools_schema)

        final_content = final_resp["choices"][0]["message"].get("content", "")
        print(f"\n🎉 [Final Agent Answer]:\n{final_content}")
        print("=" * 60 + "\n")


if __name__ == "__main__":
    agent = Day1Agent()
    query = sys.argv[1] if len(sys.argv) > 1 else "请帮我看看当前目录有哪些文件？"
    agent.chat_step(query)
