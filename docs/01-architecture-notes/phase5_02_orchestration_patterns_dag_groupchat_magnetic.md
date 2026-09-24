# Phase 5 专题 2: 多智能体协作与编排范式 (Static DAG, Dynamic GroupChat & Magentic-One)

> **学习模块**：Phase 5 复杂多智能体协同与生产编排专项 (Post-90h Advanced Multi-Agent Systems)  
> **核心参考**：Victor Dibia《Designing Multi-Agent Systems》配套代码库 [designing-multiagent-systems](https://github.com/victordibia/designing-multiagent-systems)；`picoagents/workflow/`；微软 Magentic-One 论文与多智能体编排实践  
> **源码对应**：[phase5_orchestration.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/phase5_orchestration.py) | [phase5_orchestration_test.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/phase5_orchestration_test.py)  

---

## 1. 核心挑战：从“能通信”到“有秩序协同”

在专题 1 中，我们通过 `PicoAgents` 和 `MultiAgentBus` 打通了智能体之间的点对点（P2P）与广播通讯。
然而，仅仅拥有消息总线，多智能体系统依然会陷入混乱：
- 谁先发言？谁后发言？
- 遇到死锁或分支依赖怎么办？
- 怎么知道任务已经圆满完成，而不是无休止地互相客套？
- 当某个下游子任务因报错崩溃时，整个系统是直接宕机，还是有能力动态重新规划？

这就是**编排范式（Orchestration Patterns）**要解决的核心命题。
在现代工业界，存在三大主流编排拓扑：

```text
                        多智能体编排三大范式全景对比
  
  ┌───────────────────────┬────────────────────────┬────────────────────────┐
  │ 1. 静态有向无环流 (DAG)│ 2. 动态圆桌会议 (Chat) │ 3. 规划驱动编排 (Magentic)│
  ├───────────────────────┼────────────────────────┼────────────────────────┤
  │ • 确定性拓扑依赖图    │ • 轮询或 AI 动态仲裁发言│ • 双循环架构 (Outer/Inner) │
  │ • 编译期拓扑排序      │ • 会议纪要广播全员     │ • 全局任务板 (Task Ledger)│
  │ • 零幻觉、零死循环    │ • 适合观点交锋与发散研讨│ • 支持失败动态重规划      │
  │ • 适合固化研发流水线  │ • 需防范死循环与发散   │ • 适合开放式长链路任务    │
  └───────────────────────┴────────────────────────┴────────────────────────┘
```

---

## 2. 范式一：静态工作流（Static DAG Workflow）

对于确定性极强的生产工序（如“需求解析 ➔ 架构设计 ➔ 代码生成 ➔ 单测编写 ➔ 安全审查”），让智能体自由发挥通常是灾难性的。
**静态有向无环图（DAG, Directed Acyclic Graph）** 提供了绝对的确定性：

```text
               [Node 1: Requirement Analyst (需求分析)]
                                  │
                                  ▼
               [Node 2: System Architect (架构设计)]
                                 / \
                                /   \
                               ▼     ▼
     [Node 3A: Backend Coder]       [Node 3B: Database Admin]
     (实现后端接口)                  (设计数据库表结构)
                               \     /
                                ▼   ▼
               [Node 4: Integration Verifier (集成测试与验收)]
```

### 核心工程不变式
1. **拓扑排序（Topological Sort）**：所有前置依赖（`depends_on`）必须执行完毕且状态为 `COMPLETED`，后续节点才具备调度资格；
2. **环路死锁阻断（Cycle Detection）**：系统在加载工作流时必须执行有向图环路检测，一旦发现循环依赖（$A \to B \to C \to A$），直接抛出 `WorkflowCycleError`，拒绝在不可判定的图上开跑；
3. **状态汇聚（Context Aggregation）**：当一个节点依赖多个前置分支时（如 Node 4 依赖 3A 和 3B），Harness 自动将各分支的输出字典聚合成联合输入上下文注入给当前执行体。

---

## 3. 范式二：动态圆桌协作（Dynamic GroupChat）

在方案评审、代码攻防或头脑风暴场景中，发言顺序无法在事前静态敲定，需要像真实的“会议室圆桌讨论”一样动态推进：

```text
                  GroupChat (多智能体圆桌会议室)
                                │
        ┌───────────────────────┴───────────────────────┐
        ▼                                               ▼
 [模式 A: Round-Robin (公平轮替)]      [模式 B: AI-Driven (主持人仲裁)]
 架构师 ➔ 开发 ➔ 测试 ➔ 架构师...       GroupManager 实时理解当前上下文:
 (简单、公平、无决策开销)              "刚才开发提到了鉴权疑问，请安全官发言！"
```

### ① 轮询策略接口：`SpeakerSelector`
- **`RoundRobinSelector`**：维护固定的发言列表游标，按顺序轮流点名，适合固定流程的多方审计；
- **`AIDrivenSelector`**：由一个专职的 `GroupManager` 智能体作为“主持人”，每次分析最新发言内容与待解决问题，从候选人池中挑出最适合回答的下一个 Agent（如检测到代码报错则点名修复者）。

### ② 终止条件与死循环熔断（Termination Conditions）
GroupChat 最危险的反模式是“商业互吹”或“无限复读”：
- **语义收敛判断**：检测到特定收敛信号（如 `[CONSENSUS_REACHED]`、`APPROVED`、`TASK_DONE`）时立即终止；
- **硬步数熔断（Max Turns Breaker）**：即使讨论未达成一致，到达最大轮数上限（如 10 轮）时强制刹车，输出当前争议点并移交人工审查。

---

## 4. 范式三：规划驱动编排（Magentic-One 双循环架构）

微软研究院提出的 **Magentic-One** 是目前解决复杂长任务最前沿的多智能体架构。
它彻底推翻了“一次性规划全盘执行”的脆弱假设，引入了**双循环（Outer Loop & Inner Loop）自愈机制**：

```text
 ┌────────────────────────────────────────────────────────────────────────┐
 │                    Magentic-One 双循环协同架构                         │
 │                                                                        │
 │  [外循环: Outer Loop (全局指挥部)]                                      │
 │    • Orchestrator 维护全局任务板 (Task Ledger: PENDING/RUNNING/DONE)   │
 │    • 决定当前推进哪一步                                                │
 │    • 发现子任务连续失败 ──► 触发【动态重规划 (Dynamic Replanning)】     │
 │                 │                               ▲                      │
 │                 ▼ 派发具体子任务                │ 回传执行证据/报错    │
 │  [内循环: Inner Loop (战术执行单元)]            │                      │
 │    • 针对单步任务，调度专职 Agent (如 Coder / WebBrowser)              │
 │    • 进行局部的 ReAct 尝试与工具调用                                   │
 │    • 成功则标记 COMPLETED；多次尝试失败则上报 FAILED                   │
 └────────────────────────────────────────────────────────────────────────┘
```

### 动态重规划（Dynamic Replanning）的救火逻辑
当代码实现者在执行步骤 3 时发现：“由于所依赖的开源库已被废弃，该方案物理不可行”：
- **普通工作流**：整条流水线崩溃报错退出；
- **Magentic-One**：内循环向外循环上报 `FAILED` 状态及原因；Orchestrator 介入，**重写后续步骤**（将“基于旧库实现”替换为“基于替代方案重写”），任务板无缝更新，流程继续向前推演！

---

## 5. 三大编排模式权衡决策矩阵

| 维度 | 静态 DAG 工作流 | 动态 GroupChat | Magentic-One 规划驱动 |
|---|---|---|---|
| **确定性与可复现性** | ⭐⭐⭐⭐⭐ (绝对确定) | ⭐⭐ (概率性发言) | ⭐⭐⭐⭐ (全局任务板锚定) |
| **应对不可预见问题的弹性** | ⭐ (遇到意外只能中断) | ⭐⭐⭐⭐ (可动态辩论) | ⭐⭐⭐⭐⭐ (支持实时重规划) |
| **Token 与时间开销** | ⭐ (极低, 零协调消耗) | ⭐⭐⭐⭐ (较高, 每轮全员知晓) | ⭐⭐⭐ (适中, 仅按需协调) |
| **适用场景** | CI/CD、数据清洗、标准代码生成 | 方案评审、红蓝对抗、头脑风暴 | 复杂长任务、全自动 Repo 重构 |

> **一句话落地法则**：  
> **确定性步骤走 DAG；方案争议走 GroupChat；复杂长链路走 Magentic-One。**  
> 这三大编排引擎的有机结合，构成了现代高阶 Multi-Agent 系统的终极秩序。
