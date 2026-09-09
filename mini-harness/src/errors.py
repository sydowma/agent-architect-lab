"""
mini-harness 错误分类分级与异常自愈诊断模块 (Error Taxonomy & Diagnostic Hints)

提供:
1. 标准化 Agent 异常继承层次 (HarnessError, ValidationError, LLMCallError, ToolExecutionError, WorkflowError)
2. 语义诊断生成器: 将底层系统异常转化为带有修复指引 (Diagnostic Hint) 的 Observation 文本
"""

from typing import Any, Dict, Optional


class HarnessError(Exception):
    """mini-harness 基础异常基类"""
    def __init__(
        self,
        message: str,
        code: str = "HARNESS_ERROR",
        retryable: bool = False,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details
        }


class ValidationError(HarnessError):
    """输入验证或参数校验错误 (不可直接重试，需纠偏输入)"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code="VALIDATION_ERROR", retryable=False, details=details)


class LLMCallError(HarnessError):
    """LLM 提供方通信/网络/超时错误 (瞬时故障，可指数退避重试)"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code="LLM_CALL_ERROR", retryable=True, details=details)


class ToolExecutionError(HarnessError):
    """工具物理执行阶段错误 (附带语义诊断指引)"""
    def __init__(
        self,
        message: str,
        tool_name: str,
        diagnostic_hint: str = "",
        details: Optional[Dict[str, Any]] = None
    ):
        full_details = details or {}
        full_details["tool_name"] = tool_name
        full_details["diagnostic_hint"] = diagnostic_hint
        super().__init__(message, code="TOOL_EXECUTION_ERROR", retryable=False, details=full_details)
        self.tool_name = tool_name
        self.diagnostic_hint = diagnostic_hint


class WorkflowError(HarnessError):
    """编排控制面故障 (如 Token 超限截断、步数耗尽等)"""
    def __init__(self, message: str, code: str = "WORKFLOW_ERROR", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code=code, retryable=False, details=details)


def generate_diagnostic_hint(tool_name: str, error_msg: str, exc_type: str = "") -> str:
    """根据工具名称、异常类型与错误特征，生成启发模型自省纠错的诊断指引"""
    low_err = f"{tool_name} {error_msg} {exc_type}".lower()

    if "not found" in low_err or "not exist" in low_err or "errno 2" in low_err or "filenotfound" in low_err:
        return (
            "The specified file/path was not found. "
            "HINT: Check if there is a typo in the filename, or use 'list_dir' on the parent directory "
            "to inspect the actual files available before retrying."
        )

    if "division by zero" in low_err or "zerodivision" in low_err:
        return (
            "Math error: Division by zero is undefined. "
            "HINT: Check your formula inputs to ensure the divisor is non-zero."
        )

    if "json" in low_err and ("decode" in low_err or "invalid" in low_err):
        return (
            "JSON parsing failure. "
            "HINT: Ensure the tool arguments strictly follow JSON standard format with valid quotes."
        )

    if "timed out" in low_err or "timeout" in low_err:
        return (
            "Execution timed out. "
            "HINT: The command or operation took too long. Simplify the query or reduce the workload."
        )

    if "permission" in low_err or "errno 13" in low_err:
        return (
            "Permission denied. "
            "HINT: The current process does not have sufficient privileges for this path."
        )

    # 默认通用指引
    return (
        f"Tool '{tool_name}' failed to execute. "
        "HINT: Review the error message and adjust your input parameters or consider alternative tools."
    )


def classify_and_format_error(
    exc: Exception,
    tool_name: str,
    raw_args: Any = None
) -> str:
    """
    将任何捕获到的底层异常格式化为高信息量的 Observation 文本回灌给模型。
    格式:
    [ToolExecutionError: ErrorType] Error description
    DIAGNOSTIC HINT: ...
    """
    error_type = type(exc).__name__
    error_str = str(exc)
    hint = generate_diagnostic_hint(tool_name, error_str, error_type)

    observation = (
        f"[ToolExecutionError: {error_type}] Tool '{tool_name}' encountered an error: {error_str}\n"
        f"DIAGNOSTIC HINT: {hint}"
    )
    return observation
