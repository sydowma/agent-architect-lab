"""
mini-harness Day 4 自省自愈与异常防护实战测试 (Self-Healing & Fault-Tolerance Test)

测试目标:
1. 验证错误分类与诊断提示生成 (Taxonomy & Diagnostic Hint)
2. 端到端诱导踩坑测试: 故意给错路径，验证本地 Qwen 3.8 27B 能否根据 DIAGNOSTIC HINT
   自主反思、修正策略 (list_dir 探查) 并最终成功获取正确数据！
"""

import os
import sys
from day4_agent import Day4Agent
from errors import (
    ValidationError,
    LLMCallError,
    ToolExecutionError,
    WorkflowError,
    classify_and_format_error,
)


def test_error_taxonomy():
    print("\n" + "=" * 62)
    print("🧪 1. 测试错误分类体系 (Error Taxonomy & Diagnostic Hints)")
    print("=" * 62)

    # 1. 验证 FileNotFoundError 诊断
    fnf_obs = classify_and_format_error(FileNotFoundError("bad_file.txt"), "read_file")
    assert "[ToolExecutionError: FileNotFoundError]" in fnf_obs
    assert "list_dir" in fnf_obs, "诊断指引应推荐使用 list_dir 探查"
    print("  ✅ FileNotFoundError 诊断生成正常 (含 list_dir 引导)")

    # 2. 验证 ZeroDivisionError 诊断
    zero_obs = classify_and_format_error(ZeroDivisionError("division by zero"), "calculate")
    assert "[ToolExecutionError: ZeroDivisionError]" in zero_obs
    assert "Division by zero" in zero_obs
    print("  ✅ ZeroDivisionError 诊断生成正常 (含非零除数引导)")

    # 3. 验证结构化异常类属性
    llm_err = LLMCallError("Timeout 90s", details={"attempts": 3})
    assert llm_err.retryable is True, "LLMCallError 应当被标记为可重试"
    assert llm_err.code == "LLM_CALL_ERROR"
    print("  ✅ 结构化异常类属性与 retryable 标定验证通过")

    print("🎉 错误分类与诊断生成测试全部通过！\n")


def test_e2e_self_healing():
    print("=" * 62)
    print("🚀 2. 测试本地 Qwen 3.8 27B 真实诱导踩坑与自主纠偏 (Self-Healing)")
    print("=" * 62)

    agent = Day4Agent(max_steps=7, temperature=0.1)

    trap_goal = (
        "请帮我完成以下任务：\n"
        "1. 首先尝试读取 'mini-harness/data/user_profile.json' 文件。\n"
        "2. 如果该文件不存在或报错，请仔细查看报错中的 DIAGNOSTIC HINT，自主检查 mini-harness/data 目录，"
        "找到真正存储用户记忆的文件并读取它。\n"
        "3. 最终告诉我：用户的姓名（user_name）和主力编程语言分别是什么？"
    )

    result = agent.run(trap_goal)

    print("\n" + "=" * 62)
    print("📊 [自省自愈复盘与断言]")
    print("=" * 62)
    print(f"总执行步数: {result['total_steps']}")
    print(f"自愈纠偏触发次数: {result['self_correction_count']}")
    print(f"终止状态: {result['termination_reason']}")

    # 严格断言
    assert result["termination_reason"] == "goal_reached", "任务应成功达成"
    assert result["self_correction_count"] >= 1, "必须至少触发 1 次自主纠偏事件！"

    answer = result["final_answer"]
    assert "Mark" in answer, "最终答复应准确识别出用户姓名 Mark"
    assert "Python" in answer and "C++" in answer, "最终答复应识别出主力语言 Python 和 C++"

    print("\n✅ 端到端模型自愈闭环验证圆满成功！模型在碰壁后成功自愈并产出精确答案！")


if __name__ == "__main__":
    test_error_taxonomy()
    test_e2e_self_healing()
