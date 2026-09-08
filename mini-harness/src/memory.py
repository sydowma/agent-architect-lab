"""
mini-harness 状态与分层记忆模块 (Memory & State Management)

包含:
1. ConversationHistory: 短期工作记忆管理 (原子级滑动窗口截断、会话保存与恢复)
2. FactMemoryManager: 长期持久化知识库 (本地 JSON 存储、主动记忆工具、System Prompt 自动装配)
"""

import datetime
import json
import os
from typing import Any, Dict, List, Optional
from tools import default_registry


class ConversationHistory:
    """短期工作记忆管理器：严格遵守 OpenAI Message 时序拓扑规范"""
    def __init__(self, system_prompt: str = ""):
        self.system_prompt = system_prompt
        self.messages: List[Dict[str, Any]] = []
        if system_prompt:
            self.messages.append({"role": "system", "content": system_prompt})

    def add_user_message(self, content: str):
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, assistant_msg: Dict[str, Any]):
        """追加模型输出 (可能包含 content 或 tool_calls)"""
        clean_msg = {"role": "assistant"}
        if assistant_msg.get("content"):
            clean_msg["content"] = assistant_msg["content"]
        if assistant_msg.get("tool_calls"):
            clean_msg["tool_calls"] = assistant_msg["tool_calls"]
        self.messages.append(clean_msg)

    def add_tool_result(self, tool_call_id: str, tool_name: str, content: str):
        """严格对应 tool_calls 的输出消息"""
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": content
        })

    def get_messages(self, max_turns: int = 15) -> List[Dict[str, Any]]:
        """
        获取传给 LLM 的上下文序列。
        智能窗口裁剪规则：
        1. 必须永久锁定开头的 role="system"
        2. 历史超长时向前滑动，但裁剪起点必须是 role="user"，防止将 tool_calls 和 tool 响应截断成残片
        """
        if len(self.messages) <= 1:
            return list(self.messages)

        system_msg = self.messages[0] if self.messages[0].get("role") == "system" else None
        non_system = self.messages[1:] if system_msg else self.messages

        # 如果未超出限制，直接返回
        if len(non_system) <= max_turns * 2:
            return list(self.messages)

        # 从后往前寻找安全的截断点 (必须是 role="user")
        safe_start_idx = max(0, len(non_system) - max_turns * 2)
        while safe_start_idx < len(non_system) and non_system[safe_start_idx].get("role") != "user":
            safe_start_idx += 1

        windowed = non_system[safe_start_idx:]
        if system_msg:
            return [system_msg] + windowed
        return windowed

    def save(self, filepath: str):
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.messages, f, ensure_ascii=False, indent=2)

    def load(self, filepath: str):
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                self.messages = json.load(f)


class FactMemoryManager:
    """长期持久化事实记忆：将关键偏好与事实存储在本地文件，跨会话保留"""
    def __init__(self, storage_path: Optional[str] = None):
        if not storage_path:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            storage_path = os.path.join(base_dir, "data", "agent_memory.json")
        self.storage_path = storage_path
        self.memories: Dict[str, Dict[str, Any]] = self._load()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.storage_path)), exist_ok=True)
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(self.memories, f, ensure_ascii=False, indent=2)

    def save_fact(self, key: str, value: str, category: str = "general") -> str:
        """存储或更新一条长期事实"""
        self.memories[key] = {
            "key": key,
            "value": value,
            "category": category,
            "updated_at": datetime.datetime.now().isoformat()
        }
        self._save()
        return f"Successfully saved fact '{key}': {value}"

    def query_facts(self, keyword: str = "") -> str:
        """根据关键词检索长期记忆"""
        if not self.memories:
            return "No persistent memories found."
        
        matches = []
        kw = keyword.lower()
        for k, item in self.memories.items():
            if not kw or kw in k.lower() or kw in item["value"].lower():
                matches.append(f"[{item['category']}] {k}: {item['value']}")

        return "\n".join(matches) if matches else f"No memories matching '{keyword}'."

    def render_system_prompt_addon(self) -> str:
        """将所有长期事实渲染为 System Prompt 补充块"""
        if not self.memories:
            return ""
        lines = ["\n=== KNOWN LONG-TERM FACTS (REMEMBERED ACROSS SESSIONS) ==="]
        for k, item in self.memories.items():
            lines.append(f"- {k}: {item['value']} ({item['category']})")
        lines.append("===========================================================\n")
        return "\n".join(lines)


# 实例化全局单例记忆管理器
memory_manager = FactMemoryManager()


# 注册为 Agent 的原生工具能力
@default_registry.tool(description="保存一条长期事实、用户偏好或关键知识到永久存储中。当用户透露关键个人信息、环境配置或特定偏好时主动调用。")
def save_fact(key: str, value: str, category: str = "general") -> str:
    """持久化保存一条长期事实"""
    return memory_manager.save_fact(key, value, category)


@default_registry.tool(description="检索已保存的长期事实或用户偏好。可传入关键词过滤。")
def query_facts(keyword: str = "") -> str:
    """检索持久化记忆"""
    return memory_manager.query_facts(keyword)
