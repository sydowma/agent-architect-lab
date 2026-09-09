"""
mini-harness v0.1 全量验收与基准评测套件 (Phase 1 Final Integration Suite)

测试范围:
1. 动态工具掩码 (Dynamic Tool Masking) 阻断算法测试
2. 上下文检查点与原子回滚 (Checkpoint & Rollback) 测试
3. 真实模型端到端综合任务 (Time + Calc + Write + Stats) 验收
"""

import os
import sys
from core import HarnessEngine, ActionLoopDetector


def test_tool_masking_mechanism():
    print("\n" + "=" * 65)
    print("🧪 1. 测试动态工具掩码 (Dynamic Tool Masking) 物理权限剥夺")
    print("=" * 65)

    engine = HarnessEngine(max_steps=5)
    all_schemas = engine.registry.get_schemas()
    initial_count = len(all_schemas)
    assert initial_count >= 5, "基础工具库应包含至少 5 个工具"

    # 模拟工具 'read_file' 触发振荡
    engine.masked_tools.add("read_file")
    active_schemas = engine._get_active_schemas()
    active_names = [s["function"]["name"] for s in active_schemas]

    assert "read_file" not in active_names, "被掩码的 read_file 不应出现在活跃 schemas 中！"
    assert len(active_schemas) == initial_count - 1
    print(f"  ✅ 动态掩码成功从工具菜单中剔除 'read_file' (剩余工具: {len(active_schemas)}/{initial_count})")

    # 解除掩码
    engine.masked_tools.clear()
    restored_schemas = engine._get_active_schemas()
    assert len(restored_schemas) == initial_count
    print("  ✅ 掩码解除后工具权限即时恢复")
    print("🎉 动态工具掩码测试通过！\n")


def test_checkpoint_and_rollback():
    print("=" * 65)
    print("🧪 2. 测试上下文检查点快照与原子回滚 (Checkpoint & Rollback)")
    print("=" * 65)

    engine = HarnessEngine()
    initial_len = len(engine.history.messages)

    # 1. 建立快照
    engine.create_checkpoint()

    # 2. 模拟对话导致上下文增长与污染
    engine.history.add_user_message("一些错误的用户输入")
    engine.history.add_assistant_message({"content": "有害的中毒输出", "tool_calls": []})
    assert len(engine.history.messages) == initial_len + 2

    # 3. 执行回滚
    success = engine.rollback()
    assert success is True, "回滚操作应返回成功"
    assert len(engine.history.messages) == initial_len, "回滚后历史消息数必须严格还原"
    print("  ✅ 成功回滚至检查点快照，消除中毒上下文")
    print("🎉 快照回滚测试通过！\n")


def test_e2e_v01_release():
    print("=" * 65)
    print("🚀 3. mini-harness v0.1 端到端全功能生产级综合验收")
    print("=" * 65)

    release_file = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "data", "v01_release_check.txt")
    )
    if os.path.exists(release_file):
        os.remove(release_file)

    engine = HarnessEngine(max_steps=6, temperature=0.1)

    complex_task = (
        "请执行以下发布验收任务：\n"
        "1. 调用 get_system_time 获取当前系统时间。\n"
        "2. 调用 calculate 计算 1024 * 768 的屏幕像素点总数。\n"
        "3. 将包含系统时间和计算结果的验收文本写入 mini-harness/data/v01_release_check.txt。\n"
        "4. 输出最终成果报告。"
    )

    res = engine.run(complex_task)

    print("\n" + "=" * 65)
    print("📊 [发布验收指标复盘]")
    print("=" * 65)
    print(f" • 任务达成状态:  {res['termination_reason']}")
    print(f" • 执行总步数:    {res['total_steps']}")
    print(f" • 引擎总目标数:  {engine.stats['total_goals']}")
    print(f" • 工具调用次数:  {engine.stats['total_tool_calls']}")
    print(f" • 累计 LLM 耗时: {engine.stats['total_llm_time_sec']:.2f}s")

    assert res["termination_reason"] == "goal_reached", "发布验收任务必须顺利达成！"
    assert os.path.exists(release_file), "验收报告文件必须被创建！"

    with open(release_file, "r", encoding="utf-8") as f:
        content = f.read()

    print(f"\n📄 验收文件内容 [{release_file}]:\n{'-'*40}\n{content}\n{'-'*40}")
    assert "786432" in content or "786,432" in content, "报告中必须包含正确计算结果 786432"
    print("\n🏆 mini-harness v0.1 全功能验收测试圆满通过！")


if __name__ == "__main__":
    test_tool_masking_mechanism()
    test_checkpoint_and_rollback()
    test_e2e_v01_release()
