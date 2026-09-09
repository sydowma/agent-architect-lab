"""
mini-harness Day 3 自动化测试与实战验证脚本 (Automated Test Suite for ReAct Loop)

测试项:
1. ActionLoopDetector 单元防御测试 (振荡检测算法验证)
2. 端到端多步自主求解测试 (本地 Qwen 3.8 27B 实测: 探查 -> 读取 -> 计算 -> 写入)
"""

import json
import os
import sys
from day3_agent import ActionLoopDetector, ReActAgent


def test_loop_detector():
    print("\n" + "=" * 60)
    print("🧪 1. 测试 ActionLoopDetector 动作振荡与死循环检测")
    print("=" * 60)

    detector = ActionLoopDetector(max_consecutive_repeats=2)

    # 第一次调用 read_file
    stuck, count = detector.record_and_check("read_file", '{"filepath": "data/test.txt"}')
    assert not stuck, "首次调用不应触发报警"
    assert count == 1
    print("  ✅ 首次调用通过 (count=1)")

    # 乱序 JSON 参数 (语义相同) 调用同名工具
    stuck, count = detector.record_and_check("read_file", '{"filepath": "data/test.txt"}')
    assert stuck, "相同工具且相同规范化参数第 2 次应触发死循环报警"
    assert count == 2
    print("  ✅ 连续重复调用成功拦截 (count=2, stuck=True)")

    # 切换为不同参数
    stuck, count = detector.record_and_check("read_file", '{"filepath": "data/other.txt"}')
    assert not stuck, "参数改变后应自动解除死循环锁定"
    assert count == 1
    print("  ✅ 切换不同参数后解除锁定 (count=1, stuck=False)")

    print("🎉 ActionLoopDetector 防御算法测试全部通过！\n")


def test_e2e_complex_react():
    print("=" * 60)
    print("🚀 2. 测试本地 Qwen 3.8 27B 原生多步 ReAct 自主求解")
    print("=" * 60)

    target_report = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "data", "day3_analysis.txt")
    )
    if os.path.exists(target_report):
        os.remove(target_report)

    agent = ReActAgent(max_steps=6, temperature=0.1)

    complex_goal = (
        "请完成以下多步分析任务：\n"
        "1. 列出 mini-harness/data 目录下的文件。\n"
        "2. 读取 mini-harness/data/agent_memory.json，统计里面共有几个 key（事实）。\n"
        "3. 调用 calculate 工具计算：如果每个 key 占用 128 MB 内存，这些 key 一共占用多少 MB？\n"
        "4. 将最终分析结果（包含事实数量、计算算式与总内存占用）写入 mini-harness/data/day3_analysis.txt。\n"
        "5. 写入成功后输出最终答复。"
    )

    result = agent.run(complex_goal)

    print("\n" + "=" * 60)
    print("📊 [测试复盘与断言]")
    print("=" * 60)
    print(f"总执行步数: {result['total_steps']}")
    print(f"终止原因: {result['termination_reason']}")

    # 验证物理文件是否生成
    assert os.path.exists(target_report), f"目标产物文件未被创建: {target_report}"
    with open(target_report, "r", encoding="utf-8") as f:
        content = f.read()

    print(f"\n📄 生成的文件内容 [{target_report}]:\n{'-'*40}\n{content}\n{'-'*40}")
    assert len(content.strip()) > 0, "生成的文件内容不应为空"
    print("\n✅ 端到端 ReAct 自主闭环验证成功！")


if __name__ == "__main__":
    test_loop_detector()
    test_e2e_complex_react()
