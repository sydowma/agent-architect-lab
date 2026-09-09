"""
mini-harness 工具注册与派发器 (Tool Registry & Dispatcher)

负责功能:
1. @tool 装饰器: 从 Python 原生类型标注和 Docstring 中自动提取并生成 OpenAI 规范的 JSON Schema
2. ToolRegistry: 集中注册、Schema 导出与安全派发执行 (含异常捕获)
"""

import inspect
import json
import os
import subprocess
from typing import Any, Callable, Dict, List, Optional, get_type_hints


# Python 原生类型映射到 JSON Schema 类型
TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


class ToolDefinition:
    def __init__(self, fn: Callable, name: Optional[str] = None, description: Optional[str] = None):
        self.fn = fn
        self.name = name or fn.__name__
        self.description = description or (fn.__doc__.strip() if fn.__doc__ else f"Function {self.name}")
        self.schema = self._generate_schema()

    def _generate_schema(self) -> Dict[str, Any]:
        """通过 inspect 分析函数签名与文档，自动生成符合 OpenAI 规范的 schema"""
        sig = inspect.signature(self.fn)
        type_hints = get_type_hints(self.fn)

        properties = {}
        required = []

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue

            param_type = type_hints.get(param_name, str)
            json_type = TYPE_MAP.get(param_type, "string")

            properties[param_name] = {
                "type": json_type,
                "description": f"Parameter: {param_name}"
            }

            # 无默认值的参数视为必填
            if param.default == inspect.Parameter.empty:
                required.append(param_name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            }
        }

    def execute(self, kwargs: Dict[str, Any]) -> str:
        """执行本地函数，捕获任何异常并转化为模型可读的语义诊断文本"""
        try:
            res = self.fn(**kwargs)
            if isinstance(res, (dict, list)):
                return json.dumps(res, ensure_ascii=False)
            return str(res)
        except Exception as e:
            try:
                from errors import classify_and_format_error
                return classify_and_format_error(e, self.name, kwargs)
            except Exception:
                return f"Error executing tool '{self.name}': {type(e).__name__} - {str(e)}"


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}

    def register(self, fn: Callable, name: Optional[str] = None, description: Optional[str] = None):
        """注册工具函数"""
        tool_def = ToolDefinition(fn, name, description)
        self._tools[tool_def.name] = tool_def
        return fn

    def tool(self, name: Optional[str] = None, description: Optional[str] = None):
        """装饰器方式注册: @registry.tool()"""
        def decorator(fn: Callable):
            self.register(fn, name, description)
            return fn
        return decorator

    def get_schemas(self) -> List[Dict[str, Any]]:
        """获取供 LLM API 使用的 tools 数组"""
        return [t.schema for t in self._tools.values()]

    def dispatch(self, tool_name: str, arguments: Any) -> str:
        """派发执行工具，包含防御性校验"""
        if tool_name not in self._tools:
            return f"Error: Tool '{tool_name}' not found. Available tools: {list(self._tools.keys())}"

        # 兼容 arguments 可能是 JSON 字符串或 dict
        if isinstance(arguments, str):
            try:
                parsed_args = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError as e:
                return f"Error: Invalid JSON arguments for tool '{tool_name}': {str(e)}"
        elif isinstance(arguments, dict):
            parsed_args = arguments
        else:
            parsed_args = {}

        return self._tools[tool_name].execute(parsed_args)


# 全局默认注册表与基础工具集
default_registry = ToolRegistry()


@default_registry.tool(description="读取本地文件内容。可指定起始行与读取最大行数。")
def read_file(filepath: str, max_lines: int = 100) -> str:
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File '{filepath}' does not exist.")
    if os.path.isdir(filepath):
        raise IsADirectoryError(f"'{filepath}' is a directory, not a file.")

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        lines = [f.readline() for _ in range(max_lines)]
    return "".join(lines)


@default_registry.tool(description="列出指定目录下的文件和子目录。")
def list_dir(directory: str = ".") -> str:
    if not os.path.exists(directory):
        return f"Error: Directory '{directory}' does not exist."
    try:
        entries = sorted(os.listdir(directory))
        return json.dumps(entries, ensure_ascii=False)
    except Exception as e:
        return f"Error listing directory: {str(e)}"


@default_registry.tool(description="在当前环境中执行只读或安全检查的 Shell 命令。")
def run_command(command: str) -> str:
    # 基础高危拦截 (Day 3 会进一步做 HITL 权限审批)
    for forbidden in ["rm -rf /", ":(){ :|:& };:"]:
        if forbidden in command:
            return f"Error: Command blocked by safety policy."

    try:
        res = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=15
        )
        out = res.stdout.strip()
        err = res.stderr.strip()
        if res.returncode != 0:
            return f"Exit Code {res.returncode}\nStdout: {out}\nStderr: {err}"
        return out if out else "(No output)"
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 15 seconds."
    except Exception as e:
        return f"Error executing shell command: {str(e)}"

@default_registry.tool(description="获取系统当前时间。")
def get_system_time() -> str:
    """获取系统当前时间"""
    from datetime import datetime
    return datetime.now().isoformat()


@default_registry.tool(description="安全数学表达式计算器。支持四则运算、取模、括号与基础数学运算 (如 abs, round, min, max, pow, sqrt)。")
def calculate(expression: str) -> str:
    """安全计算数学表达式并返回数值结果"""
    import math
    safe_dict = {
        "abs": abs, "round": round, "min": min, "max": max, "pow": pow,
        "sqrt": math.sqrt, "ceil": math.ceil, "floor": math.floor,
        "pi": math.pi, "e": math.e
    }
    cleaned = expression.strip().replace("^", "**")
    result = eval(cleaned, {"__builtins__": {}}, safe_dict)
    return str(result)


@default_registry.tool(description="向指定路径写入文本内容。会自动创建父目录。")
def write_file(filepath: str, content: str) -> str:
    """写入文件内容并返回确认消息"""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote {len(content)} characters to '{filepath}'."
    except Exception as e:
        return f"Error writing file '{filepath}': {str(e)}"