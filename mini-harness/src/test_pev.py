"""
mini-harness Phase 2: PEV (Plan-Execute-Verify) 架构自动化测试套件

测试项:
1. Verifier 质量门禁单元断言 (验证 PASS/FAIL 判断准确性)
2. 端到端多阶段任务确定性推进与全链路验收实测 (Local Qwen 3.8 27B)
"""

import os
import sys
from pev import PEVAgent, PlanStep


def test_verifier_gate():
    print("\n" + "=" * 65)
    print("🧪 1. 测试 Verifier 质量门禁客观判定能力")
    print("=" * 65)

    agent = PEVAgent()
    step = PlanStep(
        step_id=1,
        title="获取文件内容",
        instruction="读取 data.csv 文件",
        expected_outcome="成功返回非空的 CSV 数据表格行"
    )

    # 1. 负向测试 (工具失败)
    bad_output = "[ToolExecutionError: FileNotFoundError] File 'data.csv' does not exist."
    passed, critique = agent.verify(step, bad_output)
    assert passed is False, "当输出为报错且缺少预期数据时，Verifier 必须判定 FAIL！"
    print(f"  ✅ 失败输出被准确拦截 (passed=False): {critique[:60]}...")

    # 2. 正向测试 (符合预期)
    good_output = "[read_file Output]: date,price,volume\n2026-09-01,100.5,15000\n2026-09-02,102.1,18000"
    passed, critique = agent.verify(step, good_output)
    assert passed is True, "当输出包含有效数据时，Verifier 必须判定 PASS！"
    print(f"  ✅ 达标输出被准确验收 (passed=True): {critique[:60]}...")

    print("🎉 Verifier 门禁算法单元测试全部通过！\n")


def test_e2e_pev_execution():
    print("=" * 65)
    print("🚀 2. 本地 Qwen 3.8 27B 端到端 PEV 确定性全链路实测")
    print("=" * 65)

    target_file = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "data", "pev_verified_result.txt")
    )
    if os.path.exists(target_file):
        os.remove(target_file)

    agent = PEVAgent(max_retries_per_step=1)

    task = (
        "执行两阶段确定性系统任务：\n"
        "1. 第一阶段：调用 get_system_time 工具获取当前精确系统时间。\n"
        "2. 第二阶段：调用 write_file 工具，将获得的时间戳以及 'PEV_PIPELINE_VERIFIED' 写入 mini-harness/data/pev_verified_result.txt。"
    )

    res = agent.run(task)

    print("\n" + "=" * 65)
    print("📊 [PEV 全流程执行复盘与断言]")
    print("=" * 65)
    print(f" • 任务整体达成: {res['success']}")
    print(f" • 计划总步骤数: {len(res['plan'])}")
    for s in res['plan']:
        print(f"    - [{s['step_id']}] {s['title']} -> 状态: {s['status']}")

    assert res["success"] is True, "全流程必须全部验证通过"
    assert os.path.exists(target_file), f"目标产物文件未被生成: {target_file}"

    with open(target_file, "r", encoding="utf-8") as f:
        c = f.read()

    print(f"\n📄 最终落盘产物内容 [{target_file}]:\n{'-'*40}\n{c}\n{'-'*40}")
    assert "PEV_PIPELINE_VERIFIED" in c, "目标文件必须包含验收标记 PEV_PIPELINE_VERIFIED"
    print("\n🏆 PEV 确定性规划与单步验收全流程验证圆满通过！")


if __name__ == "__main__":
    test_verifier_gate()
    test_e2e_pev_execution()
