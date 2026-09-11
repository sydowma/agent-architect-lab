# Day 21: Harness Engineering 十条黄金法则与 mini-harness v0.2 工业级架构宣言

> **学习模块**：Week 3 工业级蜕变 —— 驾驭工程与系统防御 (Claude Code Harness Engineering)  
> **核心参考**：`harness-books/book1-claude-code` 第 9 章（Harness Engineering 十条原则）与全书统合复盘  
> **源码对应**：[v02_industrial_harness.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/v02_industrial_harness.py) | [day21_test.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/day21_test.py)  

---

## 1. 核心引言：从拟人神话到工程系统

在经历了 Week 1 的基础机制手搓（ReAct 循环、内存与工具抽象）以及 Week 2 的架构范式横向较量（Direct vs ReAct vs Reflection vs PEV）之后，我们在 Week 3 全身心投入了对 **Claude Code（Anthropic 官方首个工业级 Agent 终端产品）** 完整架构的解剖。

在这 7 天的高强度研读与原生手搓中，我们彻底推翻了一个充满浪漫主义却在真实世界中屡屡碰壁的幻想：
> **“模型很聪明，只要把 Prompt 写得像个高级架构师，给它挂上各种工具，它就能自主完成软件工程。”**

真实世界残酷而冷静。在面对复杂长链路任务时，**原生的大语言模型本质上是一个不可靠、易自满、注意力易稀释、输出易截断、极度缺乏物理责任感的概率预测器**。

Claude Code 之所以能够支撑成千上万工程师在真实代码库上稳定运转，其核心秘密绝不在于某一个特定的 Prompt 词条，而在于它围绕这个“不可靠的引擎”，铸造了一整套极其坚固、克制、具备严格物理防线的 **Harness（车身工程）**！

```text
       ┌────────────────────────────────────────────────────────┐
       │             Harness Engineering 工业级防御架构全景     │
       │                                                        │
       │  [控制平面 Control Plane]                              │
       │  5 级优先级分层 ──> 静态宪法 ──> 动态 <system-reminder>│
       │                        │                               │
       │                        ▼ (保全 KV Prefix Cache)        │
       │  [执行心跳 Query Loop]                                 │
       │  单调递增轮次 ──> 严格输入治理 ──> 悬空工具账本平衡    │
       │                        │                               │
       │                        ▼                               │
       │  [受管工具执行接口 Managed Tools]                       │
       │  partitionToolCalls (读并行/写串行) ──> 三态权限判定   │
       │  SafeBashGuard 深度防线 ──> 顺序因果回放               │
       │                        │                               │
       │                        ▼                               │
       │  [上下文预算治理 Context Compactor]                    │
       │  MEMORY.md 200行索引截断 ──> AutoCompact 受控重启      │
       │  SessionMemory 骨架提取 ──> 后置工作语义重建           │
       │                        │                               │
       │                        ▼                               │
       │  [分层自愈与死锁防线 Error Recovery]                   │
       │  Withheld 错误扣留 ──> PTL 分层阶梯 ──> 切断死循环     │
       │  MOT 无道歉续写 ──> 救火逃生舱 ──> Abort 闭环          │
       │                        │                               │
       │                        ▼                               │
       │  [多代理分区与独立怀疑验证 Multi-Agent & Verification]  │
       │  Cache-Safe Forking ──> 默认状态隔离 ──> 强制综合律   │
       │  ★ 实现与独立怀疑验证角色强解耦 ★ ──> 物理跑单测验收   │
       └────────────────────────────────────────────────────────┘
```

---

## 2. 《Harness Engineering 十条黄金法则》权威全景

根据 `harness-books Book 1` 第 9 章，我们将工业级 Agent 系统的工程智慧凝练为十条指导原则：

### 原则一：把模型当不稳定部件，不要当同事
- **哲学内涵**：同事可以被信任地承担责任，模型不能。模型也许能像同事一样说话，但它不会自动具备稳定性与因果责任感。
- **工程落地**：系统的可靠性绝不寄托在模型身上，而由运行时沙箱、权限管理、测试验证和回滚机制在物理上兜底。

### 原则二：Prompt 不是人格，Prompt 是控制平面
- **哲学内涵**：把 Prompt 当拟人化设定，只会得到一个擅长表演却失控的玩具；
- **工程落地**：Prompt 是系统的控制总线。实施 5 级优先级分层（Override > Coord > Agent > Custom > Default），将不变宪法固定在头部保全 KV Prefix Cache，动态上下文严格推至尾部 `<system-reminder>`。

### 原则三：Query Loop 才是代理系统的心跳
- **哲学内涵**：一次 LLM 调用不叫 Agent，持续受控的心跳执行循环才是运行时。
- **工程落地**：执行单调递增轮次自增、严格前置输入治理（治理超额工具输出与微压缩）、实施工具调用账本平衡（无论正常执行还是异常中断，每一个 `tool_use` 必须严格闭合 `tool_result`）。

### 原则四：工具是受管执行接口
- **哲学内涵**：一旦模型开始接触 Shell、文件系统与网络，动作就会产生不可逆的物理后果。
- **工程落地**：严禁模型直接碰世界。实行 `partitionToolCalls`（只读操作并行吞吐、写操作强制单步串行）、因果顺序回放（`contextModifier Replay`）、拒绝布尔偷懒的三态权限系统（ALLOW / ASK / DENY）以及 `SafeBashGuard` 高危 Shell 拦截。

### 原则五：上下文是工作内存预算
- **哲学内涵**：“信息越多越聪明”是低级神话。上下文是昂贵、易膨胀、会自我污染的工作内存。
- **工程落地**：长期规则入 `CLAUDE.md`，持久经验入 `MEMORY.md`（实施 200 行 / 25KB 物理硬截断），短期工作流萃取入结构化 `SessionMemory`（优先保留 `Current State` 与 `Errors & Corrections`）。

### 原则六：错误路径就是主路径
- **哲学内涵**：工程世界最不可信的就是“正常情况下”。`prompt_too_long`、`max_output_tokens` 是长会话的必然生理周期。
- **工程落地**：建立 `withheld` 可恢复错误扣留暂存白名单，实施分层自愈，在 PTL 无法恢复时坚决跳过 Stop Hooks 切断死亡螺旋。

### 原则七：恢复的目标是继续工作
- **哲学内涵**：系统快窒息时，优先级是先恢复呼吸，而不是礼貌客套。
- **工程落地**：输出截断（MOT）后，首提 Cap 重跑，次注入“禁止道歉、禁止 Recap、从断句处无缝拼接续写”指令；救火请求自身超长时，触发 `truncateHeadForPTLRetry` 成组剥离早期历史。

### 原则八：多代理的意义是把不确定性分区
- **哲学内涵**：多代理的价值不是并发加速，而是把不同种类的不确定性关进不同容器。
- **工程落地**：实施 Cache-Safe Forking 保证 Prompt Cache 命中率；子代理默认深拷贝隔离可变状态；协调者坚守强制综合律（**Always Synthesize**），严惩二道贩子懒惰转发。

### 原则九：验证必须独立，不能让系统自己给自己打分
- **哲学内涵**：模型极度擅长在改动与正确之间搭“纸桥”自证完成。
- **工程落地**：实现 Worker 与验证 Worker 强制角色解耦（`assert verification_worker != implementation_worker`）；验证 Worker 必须带着怀疑者立场，在外部真实物理环境运行自动化测试，杜绝橡皮图章。

### 原则十：团队制度比个人技巧重要
- **哲学内涵**：高手靠个人盯防把 Agent 驯服，团队无法复制个人直觉，必须依靠显式制度。
- **工程落地**：确立团队最低四边界（范围、Review 责任、统一验证、禁区）；审批按后果不可逆性分级（READ / WRITE / IRREVERSIBLE）；统一验证口径先于盲目扩充 Skill。

---

## 3. 《十条原则》设计不变式与反模式对照表

| 原则序号 | 核心原则名称 | 工业级运行时不变式 (Runtime Invariants) | 典型业余反模式 (Anti-Patterns) |
|---|---|---|---|
| **P1** | 模型是不稳定部件 | `assert unverified_output not committed_to_master` | 迷信模型输出，无环境单测直接自动提交 |
| **P2** | Prompt 是控制面 | `assert static_system_prompt_bytes == constant` | 把实时时间/脏状态塞进 Prompt 头部毁掉 KV 缓存 |
| **P3** | Query Loop 是心跳 | `assert count(tool_use) == count(tool_result)` | 中途中断留下未闭合的悬空 tool_use 导致 API 400 |
| **P4** | 工具是受管接口 | `assert irreversible_action in {ASK, DENY}` | 允许模型直接无提示 `rm -rf` 或 `git push -f` |
| **P5** | 上下文是内存预算 | `assert memory_lines <= 200 and bytes <= 25000` | 把 `MEMORY.md` 当成日志本无限堆叠导致爆窗 |
| **P6** | 错误属于主路径 | `assert ptl_unrecoverable => skip_stop_hooks` | 报错后 Stop Hook 再次拦截询问，陷入死循环死锁 |
| **P7** | 恢复目标是继续工作 | `assert mot_continuation.has_no_recap` | 截断后模型来一段客套道歉，浪费 token 且语义漂移 |
| **P8** | 多代理不确定性分区 | `assert child.CacheSafeParams == parent.CacheSafeParams` | 子代理重新创建空会话，将算力浪费并行化 |
| **P9** | 独立怀疑验证 | `assert verification_worker != implementation_worker` | 改完代码的同一个 Worker 顺便说一句“我觉得没问题” |
| **P10** | 团队制度胜于技巧 | `assert approval_tier determined_by consequence` | 审批按工具名字一刀切，或完全无统一验收定义 |

---

## 4. 全书终局收官心法

> **Harness 比激情重要，制度比聪明重要，验证比自信重要。**
> 
> 在大模型时代，真正的全栈不是指掌握前端和后端，而是指**既懂得大模型的概率本质，又拥有极度扎实的底层系统与车身防御工程功底**。  
> 唯有经受住这十条法则淬炼的系统，才能在工业级真实战场上临危不乱、死而不僵、持续交付！
