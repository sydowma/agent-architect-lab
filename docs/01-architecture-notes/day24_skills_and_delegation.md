# Day 24: 技能挂载、Hook 生命周期与工具化委派 (Skills, Hooks & Delegation Protocol)

> **学习模块**：Week 4 工业体系与方法论 (Harness Books Book 2: Local Governance & Delegation)  
> **核心参考**：`harness-books/book2-comparing` 第 5 章（技能、Hook 与本地规则：系统如何学会守乡约）与第 6 章（委派、验证与持久状态：谁来防止系统自己给自己打高分）；Codex `skills/src/lib.rs`、`hooks/src/engine/mod.rs`、`agent_tool.rs`  
> **源码对应**：[day24_skills_and_delegation.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/day24_skills_and_delegation.py) | [day24_test.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/day24_test.py)  

---

## 1. 核心哲学：能落地的 Agent，一定会"地方化"

任何通用 Coding Agent 一旦进入真实团队，就会立刻撞上同一堵墙：
- 公司有公司的编码规范与安全基线；
- 仓库有仓库的构建流程、Git 工作流与 CI 门禁；
- 目录有目录的局部约定（如 `frontend/` 禁用全局样式，`db/` 严禁直接执行 Drop Table）；
- 人还有怪脾气与特定的工作偏好。

**系统如果不能吸收这些“局部制度”，就永远只能停留在 Toy Project 和 Demo 演示里。**

现代两大顶尖系统（Claude Code 与 Codex）给出了截然不同的解法：

```text
        ┌──────────────────────────────────────────────────────────────┐
        │              两条本地治理路线：现场记忆 vs 结构化制度          │
        │                                                              │
        │  [Claude Code: 现场经验收编]         [Codex: 结构化资产挂载]   │
        │  CLAUDE.md / skill / hook          skills/ + fingerprint     │
        │  session memory (动态折叠)          hooks/engine (事件状态机) │
        │        │                                  │                  │
        │        ▼                                  ▼                  │
        │  靠近主循环、实时组装上下文           分类 / 排序 / 安装 / 审计  │
        │  适应新仓库极快，极客体验佳           组织规范性极强，防线严密  │
        │  代价：现场补丁可能失控膨胀           代价：显式制度编写门槛偏高│
        └──────────────────────────────────────────────────────────────┘
```

> **一句话精义**：  
> Claude Code 问的是：“怎样让 Agent 在这个目录里干活更像个老练的本地员工？”  
> Codex 问的是：“怎样把分散在各处的经验与规则，收编进一套受控、防窜改、可审计的制度体系中？”

---

## 2. 技能体系：从“临时提示词”到“版本化指纹资产”

很多朴素框架把 Skill 当成简单的“字符串拼接”——每轮对话把一个 `skill.txt` 读进来塞给模型。这种做法在复杂工程中极度脆弱：不仅浪费 Token，而且当多层级规则冲突时，模型根本分不清主次。

Codex 的 `skills/src/lib.rs` 确立了工业级 Skill 管理规范：

```text
               Skill 资产发现与指纹安装生命周期 (Fingerprint Discipline)
  
  [磁盘扫描] ──► 提取 YAML Frontmatter + Markdown Content
                        │
                        ▼
               [计算 SHA256 资产指纹]
                        │
         ┌──────────────┴──────────────┐
         ▼                             ▼
   [指纹与 Registry 一致]       [指纹不一致 / 首次发现]
         │                             │
         ▼                             ▼
  [SKIPPED_FINGERPRINT_MATCH]   [INSTALLED / REINSTALLED]
  (零IO开销, 保持幂等)           (原子覆写更新, 刷新作用域缓存)
```

### ① 指纹纪律（Fingerprint Discipline）
- 每个技能资产通过 `name::version::content` 计算 SHA256 指纹；
- 系统启动或会话初始化时扫描技能目录，**仅在指纹不匹配时执行覆写**；
- 指纹未变更时直接返回 `SKIPPED_FINGERPRINT_MATCH`，杜绝无谓的重复 IO 与内存抖动。

### ② 作用域优先级原则（Source Precedence Monotonicity）
本地治理遵循**“空间越近，约束越强”**的不变式：

$$\text{PROJECT (项目级)} > \text{USER (用户级)} > \text{SYSTEM (系统级)}$$

- `PROJECT` 技能（位于 `.agent/skills/` 或仓库内）代表当前仓库的具体规则，权重最高；
- 低作用域（如 SYSTEM 默认规则）**永远无法遮蔽（Shadow）高作用域**。

### ③ Claude Code 风格的按需动态注入（On-demand Runtime Attachment）
即使安装了 50 个技能，也**绝不能全量塞进 System Prompt**（否则摧毁前缀缓存并耗尽上下文）。
正确范式是**按需挂载（On-demand Mount）**：
- 仅当 Agent 主动触发或当前任务命中技能匹配器时，才将技能作为特定回合的上下文片段包装注入：

```xml
<invoked_skill name="concurrency-guard" version="1.0.0" source="project" reason="Task touches shared memory">
[技能核心操作指南与约束条款...]
</invoked_skill>
```
用完即走，下一轮压紧（Compaction）时即可回收，不污染长期基座。

---

## 3. Hook 生命周期引擎：把规则挂在显式事件上

Codex 的 `hooks/src/engine/mod.rs` 彻底抛弃了隐晦的拦截中间件，将 Agent 执行周期定义为一组**严格单调推进的生命周期事件状态机**：

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│                            Hook 全链路生命周期事件流                          │
│                                                                              │
│   [session_start] ──► 会话初次启动 (仅触发一次, 环境变量预检, 平台能力探测)   │
│          │                                                                   │
│          ▼                                                                   │
│   [user_prompt_submit] ──► 用户提交指令 (输入审查, 敏感词与意图分类)         │
│          │                                                                   │
│          ▼                                                                   │
│   ┌─────────────── 循环调用工具帧 (Tool Loop) ───────────────┐               │
│   │                                                         │               │
│   │   [preview_pre_tool_use] (只读探测: 预览命中, 绝不执行) │               │
│   │            │                                            │               │
│   │            ▼                                            │               │
│   │   [pre_tool_use] ───────(命中阻断 Hook?)────────┐       │               │
│   │            │ 否 (放行)                           │ 是   │               │
│   │            ▼                                     ▼      │               │
│   │     [实际物理执行工具]                   [阻断短路并回传]│               │
│   │            │                                     │      │               │
│   │            ▼                                     │      │               │
│   │   [post_tool_use] ◄──────────────────────────────┘      │               │
│   │   (输出审计/敏感数据脱敏)                                │               │
│   │                                                         │               │
│   └─────────────────────────────────────────────────────────┘               │
│          │                                                                   │
│          ▼                                                                   │
│   [stop] ──► 会话终结/退出 (仅触发一次, 资源回收, 释放锁与沙箱快照)           │
└──────────────────────────────────────────────────────────────────────────────┘
```

### ① preview_* 与 run_* 双路径分离原则
- **`preview_*` 路径**：在模型决定是否调用工具之前，仅模拟计算会触发哪些 Hook，**绝对不能产生真实副作用（Side-effect Free）**；
- **`run_*` 路径**：在工具真正执行时按 `display_order` 排序串行触发，允许执行日志记录、指标上报或权限拦截；
- **收益**：让 Agent 的下一步行为在执行前即可解释、可可视化、可人工预检。

### ② 阻断型 Pre-Tool Hook（Blocking Interceptors）
只有 `pre_tool_use` 具备阻断（`can_block=True`）权限：
- 当模型试图调用包含危险模式的命令（如扫描私钥、修改生产数据库）时，Pre-Hook 直接抛出阻断异常，**在工具到达物理执行器之前将其短路**，并直接合成报错 Observation 回灌模型，杜绝破坏。

### ③ 平台能力可解释探测（Explainable Gating）
如果在不支持特定特性（如特定 cgroups 或命名管道）的操作系统（如 Windows）上运行，Hook 引擎必须**显式发出警告并降级关闭**，绝不静默假装成功或导致静默死锁。

---

## 4. 委派协议：把“多代理”做成标准工具契约

在工业级架构中，“多智能体（Multi-Agent）”不是由黑魔法或隐式递归完成的，而是被严格规范为**一组受管的工具接口（Codex `agent_tool.rs`）**：

```text
  主代理 (Coordinator)               受管委派调度引擎               子代理 (Subagent Worker)
         │                                   │                                │
         │─── spawn_agent(role, prompt) ────►│─── 创建隔离环境与线程上下文 ──►│
         │◄── 返回 handle="agent_sub_01" ────│                                │
         │                                   │                                │
         │─── send_input(handle, msg) ──────►│─── queue 或 interrupt 打断 ───►│
         │                                   │                                │
         │─── wait_agent(handle, timeout) ──►│─── 轮询执行状态 / 超时收口 ────►│
         │◄── 返回子代理成果与证据 ──────────│                                │
         │                                   │                                │
         │─── close_agent(handle, cascade) ─►│─── 级联终结子代理与其所有后代 ──►│ (销毁)
```

### ① 委派四步原语契约

1. **`spawn_agent(role, prompt, timeout, inherit_approval)`**：
   分配唯一的 `agent_handle`，创建专用的子代理上下文与不确定性分区，返回句柄；
2. **`send_input(handle, message, interrupt=True/False)`**：
   - `interrupt=False`：消息排入子代理待办队列；
   - `interrupt=True`：**立即抢占中断**子代理当前执行，丢弃其 pending 队列并注入最新意图；
3. **`wait_agent(handle, timeout)`**：
   在 `[min_timeout, max_timeout]` 阈值区间内同步等待子代理返回；若超时，返回 `[Timed Out]` 状态，**绝不抛出错误假装崩溃**，避免主模型陷入重复调用的死循环；
4. **`close_agent(handle, cascade=True)`**：
   关闭子代理句柄。若 `cascade=True`，则沿着进程树**级联关闭所有派生的孙子代理**，彻底杜绝孤儿进程（No-Orphan Invariant）。

### ② 委派状态转换矩阵

```text
    ┌──────────┐      spawn       ┌──────────┐
    │ PENDING  ├─────────────────►│ RUNNING  │
    └──────────┘                  └────┬─────┘
                                       │
            ┌──────────────────────────┼──────────────────────────┐
            ▼                          ▼                          ▼
     ┌───────────┐              ┌───────────┐              ┌───────────┐
     │ COMPLETED │              │  ABORTED  │              │  TIMEOUT  │
     │ (正常交付) │              │(父死子亡) │              │(受控收口) │
     └───────────┘              └───────────┘              └───────────┘
```

---

## 5. 审计留痕：DelegationLedger 让独立验证不流于形式

在 Day 20 中我们建立了“实现者 ≠ 验证者”的双角色分离纪律。但如果主代理询问子代理：“你做完测试了吗？”，子代理回答：“做完了，100% 通过”，这种验证就只是**缺乏公信力的形式主义**。

真实工业系统要求**凭证在手（Evidence-based Verification）**：
- 委派系统内置 **`DelegationLedger`（委派账本）**；
- 记录每一次 `SPAWN`、`INPUT`、`WAIT`、`ABORT`、`CLOSE` 的精确时间戳、入参哈希与执行结果；
- 独立验证者（Skeptical Verifier）**只审查账本中的物理证据**，而不是听信模型的自我吹嘘。

```python
# 验证者直接检查账本证据链，确保闭环无遗漏
ledger = runtime.delegation_engine.ledger
assert ledger.is_action_logged("agent_sub_01", "SPAWN")
assert ledger.is_action_logged("agent_sub_01", "WAIT")
assert runtime.delegation_engine.active_handles_count() == 0  # 无悬挂句柄
```

---

## 6. 统一治理运行时：GovernedAgentRuntime

在 `day24_skills_and_delegation.py` 中，我们将上述三大支柱融合成了一个开箱即用的工业级运行时：

```text
                       ┌──────────────────────────────────────┐
                       │        GovernedAgentRuntime          │
                       │                                      │
                       │  • SkillRegistry  (指纹与目录加载)   │
                       │  • HookEngine     (显式生命周期流)   │
                       │  • Delegation     (工具化多代理委派) │
                       │  • AuditLedger    (不可篡改证据链)   │
                       └──────────────────┬───────────────────┘
                                          │
                        Agent 统一调用接口: run_governed_turn()
                                          ▼
     [session_start] ──► [注入匹配 Skill] ──► [Pre-Hook 门禁] ──► [工具/委派执行] ──► [Post-Hook]
```

> **终极总结**：  
> **Skill 规定了“在这个团队怎么规范干活”；  
> Hook 规范了“什么时候能动手、什么时候必须停步”；  
> 委派协议与账本保障了“活是谁派的、谁干的、证据何在、绝不烂尾”。  
> 这三者共同构成了让 AI Coding Agent 真正融入工业生产环境的“制度治理之基”。**
