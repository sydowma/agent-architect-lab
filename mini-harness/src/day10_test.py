"""Integration test and live benchmark suite for Day 10 Multi-Agent & Blackboard.

Verifies:
1. Unit tests: Safety clamp, mechanical arbiter, tie-breaking, domination decay, convergence.
2. Live integration tests with local LM Studio (Qwen 3.8 27B):
   - SupervisorMultiAgent end-to-end execution
   - BlackboardSystem end-to-end execution
   - Comparative evaluation report generation
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Ensure src is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

from day10_multi_agent import (
    AgentRole,
    BlackboardBid,
    BlackboardEntry,
    BlackboardSystem,
    SupervisorMultiAgent,
    SupervisorResult,
    parse_json_from_response,
)


# ===========================================================================
# 1. Deterministic Mock Unit Tests
# ===========================================================================
def test_supervisor_safety_clamp_on_loop():
    print("\n--- [Unit Test] Supervisor Safety Clamp on Agent Loop ---")

    call_seq = [
        # Step 1: Supervisor calls 'coder'
        json.dumps({"next_agent": "coder", "reason": "Write initial code"}),
        "Coder: func Add(a, b int) int { return a + b }",
        # Step 2: Supervisor erroneously attempts to call 'coder' AGAIN (looping)
        json.dumps({"next_agent": "coder", "reason": "Coder should check again"}),
        # Safety clamp should redirect to 'tester'!
        "Tester: TestAdd passed with 100% coverage",
        # Step 3: Supervisor calls 'writer'
        json.dumps({"next_agent": "writer", "reason": "Synthesize summary"}),
        "Writer: Synthesis of Coder and Tester delivered.",
        # Step 4: Supervisor calls FINISH
        json.dumps({"next_agent": "FINISH", "reason": "Completed"}),
    ]

    def mock_llm(messages, **kwargs):
        return call_seq.pop(0)

    roles = {
        "coder": AgentRole("coder", "Writes code", "Code implementation"),
        "tester": AgentRole("tester", "Writes tests", "Verification and QA"),
    }
    agent = SupervisorMultiAgent(roles=roles, llm_fn=mock_llm, max_rounds=5)
    result = agent.run("Implement and test Add function")

    assert len(result.specialist_outputs) == 2, f"Expected 2 specialist outputs, got {len(result.specialist_outputs)}"
    assert result.specialist_outputs[0].role == "coder"
    assert result.specialist_outputs[1].role == "tester"

    # Verify clamp occurred
    clamped_steps = [s for s in result.trace if s.decision_type == "safety_clamp"]
    assert len(clamped_steps) >= 1, "Expected at least one safety clamp to catch the loop"
    print(f"PASS: Safety Clamp caught loop and redirected to '{clamped_steps[0].selected_agent}'.")


def test_supervisor_safety_clamp_premature_finish():
    print("\n--- [Unit Test] Supervisor Safety Clamp on Premature FINISH ---")

    call_seq = [
        # Step 1: Supervisor calls 'researcher'
        json.dumps({"next_agent": "researcher", "reason": "Research topic"}),
        "Researcher findings here.",
        # Step 2: Supervisor attempts to FINISH without writer
        json.dumps({"next_agent": "FINISH", "reason": "Looks good enough"}),
        # Safety clamp should force writer!
        "Writer: Synthesized final output.",
        # Step 3: Supervisor calls FINISH
        json.dumps({"next_agent": "FINISH", "reason": "Done"}),
    ]

    def mock_llm(messages, **kwargs):
        return call_seq.pop(0)

    roles = {
        "researcher": AgentRole("researcher", "Research", "Research specialist"),
    }
    agent = SupervisorMultiAgent(roles=roles, llm_fn=mock_llm, max_rounds=4)
    result = agent.run("Research topic X")

    assert bool(result.final_report), "Final report must be populated by writer"
    clamped_steps = [s for s in result.trace if s.decision_type == "safety_clamp"]
    assert any(s.selected_agent == "writer" for s in clamped_steps), "Must clamp premature FINISH to writer"
    print("PASS: Premature FINISH intercepted and redirected to 'writer'.")


def test_blackboard_mechanical_arbiter_and_domination_decay():
    print("\n--- [Unit Test] Blackboard Mechanical Arbiter & Domination Decay ---")

    # Round 1:
    # KS 'optimist' bids 5
    # KS 'skeptic' bids 4
    # Winner should be 'optimist' (eff_conf = 5 - 0 = 5)
    #
    # Round 2:
    # KS 'optimist' bids 5 again (penalty = 1 * 1.5 = 1.5, eff_conf = 3.5)
    # KS 'skeptic' bids 4 (penalty = 0, eff_conf = 4.0)
    # Winner should now be 'skeptic' due to domination decay!
    #
    # Round 3:
    # Both bid will_contribute=False -> Natural convergence!

    call_seq = [
        # Round 1 bids
        json.dumps({"will_contribute": True, "confidence": 5, "preview": "Opt 1", "reason": "r"}),
        json.dumps({"will_contribute": True, "confidence": 4, "preview": "Skep 1", "reason": "r"}),
        # Round 1 winner acts (optimist)
        "Optimist content for round 1",
        # Round 2 bids
        json.dumps({"will_contribute": True, "confidence": 5, "preview": "Opt 2", "reason": "r"}),
        json.dumps({"will_contribute": True, "confidence": 4, "preview": "Skep 2", "reason": "r"}),
        # Round 2 winner acts (skeptic wins because 4.0 > 3.5)
        "Skeptic content for round 2",
        # Round 3 bids (convergence)
        json.dumps({"will_contribute": False, "confidence": 1, "preview": "", "reason": "done"}),
        json.dumps({"will_contribute": False, "confidence": 1, "preview": "", "reason": "done"}),
        # Final synthesis
        "Final balanced synthesis of optimist and skeptic.",
    ]

    def mock_llm(messages, **kwargs):
        return call_seq.pop(0)

    ks = {
        "optimist": AgentRole("optimist", "Optimist prompt", "Optimist role"),
        "skeptic": AgentRole("skeptic", "Skeptic prompt", "Skeptic role"),
    }
    bb_system = BlackboardSystem(
        knowledge_sources=ks,
        llm_fn=mock_llm,
        max_rounds=5,
        min_confidence=3,
        domination_decay=1.5,
    )
    result = bb_system.run("Analyze project viability")

    assert len(result.blackboard) == 2, f"Expected 2 entries, got {len(result.blackboard)}"
    assert result.blackboard[0].role == "optimist", "Round 1 winner should be optimist"
    assert result.blackboard[1].role == "skeptic", "Round 2 winner should be skeptic due to domination decay"
    print(f"PASS: Round 1 winner='{result.blackboard[0].role}', Round 2 winner='{result.blackboard[1].role}' (decay worked).")


# ===========================================================================
# 2. Live Integration Benchmark (against LM Studio Qwen 3.8 27B)
# ===========================================================================
def run_live_benchmarks():
    print("\n" + "=" * 70)
    print("🚀 Running Live Multi-Agent Benchmark on Local Qwen 3.8 27B")
    print("=" * 70)

    task_description = (
        "在超高并发（100k QPS）金融行情推送与微服务网关架构中，团队正在权衡选择 Go 语言还是 Rust 语言。"
        "请从：1) 峰值吞吐量与 P99 尾部延迟（含 GC 停顿与内存安全）；2) 团队研发效能、工程可维护性与招聘成本两个核心维度进行深入对比，并给出明确的选型与落地决策。"
    )

    # -----------------------------------------------------------------------
    # Benchmark 1: Supervisor Multi-Agent System
    # -----------------------------------------------------------------------
    print("\n>>> [Benchmark 1/2] Executing SupervisorMultiAgent (Star Topology)...")
    supervisor_roles = {
        "performance_architect": AgentRole(
            name="performance_architect",
            system_prompt=(
                "你是资深底层系统架构师与性能调优专家。专注于探讨高并发网络 I/O、GC 停顿对 P99 尾部延迟的影响、"
                "内存占用（Footprint）与 Tokio 异步运行时与 Go 原生 Goroutine/Netpoller 的吞吐表现。用数据和底层机制说话。"
            ),
            description="分析 Go 与 Rust 在 100k QPS 场景下的 P99 延迟、内存消耗、GC 与并发吞吐底层机制。",
        ),
        "engineering_lead": AgentRole(
            name="engineering_lead",
            system_prompt=(
                "你是研发效能负责人与工程技术总监。专注于团队学习曲线、生命周期与所有权机制导致的研发心智负担、"
                "代码重构难度、开源生态丰富度、测试/CI 构建速度以及国内人才招聘与梯队建设成本。"
            ),
            description="分析 Go 与 Rust 在团队交付速度、招聘成本、维护门槛与长期工程治理上的差异。",
        ),
    }

    sup_agent = SupervisorMultiAgent(
        roles=supervisor_roles,
        max_rounds=5,
    )
    sup_result = sup_agent.run(task_description)

    print(f"Supervisor finished in {sup_result.elapsed_seconds:.2f}s with {sup_result.total_llm_calls} LLM calls.")
    print("Supervisor Execution Trace:")
    for step in sup_result.trace:
        print(f"  Step {step.step_index}: [{step.decision_type}] -> {step.selected_agent} ({step.reason})")

    # -----------------------------------------------------------------------
    # Benchmark 2: Blackboard System (HEARSAY-II Style)
    # -----------------------------------------------------------------------
    print("\n>>> [Benchmark 2/2] Executing BlackboardSystem (Decentralized Shared Bus)...")
    blackboard_sources = {
        "performance_expert": AgentRole(
            name="performance_expert",
            system_prompt=(
                "你是专注于极致性能与硬件利用率的系统工程师。评估 Go 与 Rust 在网络 I/O、CPU Cache 利用率、"
                "零拷贝、GC STW 暂停与 P99 延迟方面的物理硬核极限。"
            ),
            description="系统性能与底层硬件 I/O 专家",
        ),
        "reliability_sre": AgentRole(
            name="reliability_sre",
            system_prompt=(
                "你是资深 SRE 与高可用架构师。从高并发故障隔离、并发竞态、Goroutine 泄漏风险、编译期内存安全保证、"
                "线上 Panic 恢复、可观测性排查难度等角度进行批判性审视。"
            ),
            description="SRE 稳定性与高并发线上风险审计专家",
        ),
        "engineering_manager": AgentRole(
            name="engineering_manager",
            system_prompt=(
                "你是工程研发管理者。重点权衡团队交付效率、开发敏捷度、Rust 借用检查器心智负担 vs Go 极简主义、"
                "团队工程素养与人才市场招聘门槛。"
            ),
            description="研发效能与团队交付管理专家",
        ),
    }

    bb_system = BlackboardSystem(
        knowledge_sources=blackboard_sources,
        max_rounds=2,
        min_confidence=3,
        domination_decay=1.0,
    )
    bb_result = bb_system.run(task_description)

    print(f"Blackboard finished in {bb_result.elapsed_seconds:.2f}s with {bb_result.total_llm_calls} LLM calls.")
    print(f"Blackboard generated {len(bb_result.blackboard)} shared entries.")
    for entry in bb_result.blackboard:
        print(f"  [Round {entry.round_index} | Winner: {entry.role.upper()}]: {entry.content[:100]}...")

    # -----------------------------------------------------------------------
    # Save Benchmark Report
    # -----------------------------------------------------------------------
    report_path = Path(__file__).resolve().parents[2] / "docs" / "02-benchmark-reports" / "02_supervisor_vs_blackboard.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    report_content = f"""# 基准评测报告：Supervisor 星型中心化架构 vs. Blackboard 黑板去中心化架构

> **测试环境**：
> - 模型引擎：LM Studio 本地驱动 `qwen/qwen3.8-27b`
> - 测试任务：100k QPS 金融网关核心选型（Go vs. Rust：性能延迟 vs. 研发效能与工程治理）
> - 框架实现：mini-harness 原生零依赖 MAS 内核
> - 记录日期：2026-09-09

---

## 1. 核心定量基准指标对比

| 指标维度 | Supervisor 模式 (星型中心化) | Blackboard 模式 (去中心化共享黑板) | 架构对比解读 |
| :--- | :--- | :--- | :--- |
| **拓扑形态** | 星型 Hub-and-Spoke | 广播式 Shared State Bus | Supervisor 依赖中心统筹，Blackboard 依赖专家自治 |
| **总 LLM 调用次数** | **{sup_result.total_llm_calls} 次** | **{bb_result.total_llm_calls} 次** | Blackboard 每轮 N 次竞标，LLM 调用量随专家数倍增 |
| **端到端执行耗时** | **{sup_result.elapsed_seconds:.2f} 秒** | **{bb_result.elapsed_seconds:.2f} 秒** | Blackboard 串行竞标耗时更长，若支持异步并发可优化 |
| **参与专家数** | 2 名业务专家 + 1 名 Writer | 3 名全自主知识源 + 1 名 Synthesizer | Blackboard 更易包容跨多维度的冲突视角 |
| **有效内容产出条数** | {len(sup_result.specialist_outputs)} 个专家研报 + 1 份最终综合报告 | {len(bb_result.blackboard)} 个黑板辩论条目 + 1 份决策综述 | Supervisor 报告单块深度更大，Blackboard 互动博弈性更强 |
| **防死循环 / 防垄断机制** | **Host Safety Clamp 物理重定向** | **Mechanical Arbiter + Domination Decay** | 前者强控流转节点，后者数学扣减出价置信度 |

---

## 2. 调度时序与轨迹追踪 (Execution Traces)

### 2.1 Supervisor 调度时序
```text
{chr(10).join(f"Step {s.step_index}: [{s.decision_type}] -> {s.selected_agent} | {s.reason}" for s in sup_result.trace)}
```

### 2.2 Blackboard 竞标与中标记录
```text
{chr(10).join(f"Round {e.round_index} Winner: {e.role.upper()} | 内容摘要: {e.content[:120]}..." for e in bb_result.blackboard)}
```

---

## 3. 产出方案质量与认知深度对比

### 3.1 Supervisor 模式最终综合报告节选
```markdown
{sup_result.final_report[:1200]}...
```

### 3.2 Blackboard 模式最终综合报告节选
```markdown
{bb_result.final_synthesis[:1200]}...
```

---

## 4. 架构师选型裁决总结

1. **工单型流水线首选 Supervisor**：
   - 当任务逻辑具有清晰的职责划分（如：调研 -> 编码 -> 测试 -> 发布），Supervisor 具有最高的执行确定性和最低的 Token 浪费（$O(M)$ 复杂度）。
2. **疑难开放式诊断首选 Blackboard**：
   - 当面对根因未知、且不知道哪位专家能够产生关键突破的问题时（如高并发突发性能毛刺排查、分布式系统架构重构论证），Blackboard 允许各专家“看盘自主出价”，能够激发出超越预设工作流的交叉洞察。
"""
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)

    print(f"\n✅ Benchmark report successfully written to: {report_path}")


def main():
    print("==================================================")
    print("RUNNING UNIT TESTS (DETERMINISTIC MOCKS)")
    print("==================================================")
    test_supervisor_safety_clamp_on_loop()
    test_supervisor_safety_clamp_premature_finish()
    test_blackboard_mechanical_arbiter_and_domination_decay()
    print("\nAll unit tests passed successfully!")

    print("\n==================================================")
    print("RUNNING LIVE END-TO-END BENCHMARKS ON LOCAL LLM")
    print("==================================================")
    run_live_benchmarks()


if __name__ == "__main__":
    main()
