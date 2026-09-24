# Day 25: 收敛与分歧：现代 AI Agent Harness 架构演化共识

> **学习模块**：Week 4 工业体系与方法论 (Harness Books Book 2: Epilogue & Convergence)  
> **核心参考**：`harness-books/book2-comparing` 终章（收敛与分歧：未来系统的轮廓）；[Pi 蓝皮书 / Earendil 译文集](https://github.com/xiaomoBoy/pi-bluebook)（极简开源 Agent Harness 原则）；OpenAI Codex 架构演进  
> **源码对应**：[day25_architecture_synthesis.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/day25_architecture_synthesis.py) | [day25_test.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/day25_test.py)  

---

## 1. 架构三极坐标系：Claude Code、Codex 与 Pi

在经过前 24 天对原子循环、多智能体协同、安全沙箱、动态技能与生命周期 Hook 的系统性拆解后，AI Agent 的工程演化进入了一个收敛与分歧并存的成熟阶段。
业界并非只有一种“正确”的 Coding Agent 实现，而是分化出了三大极具代表性的架构路线：

```text
                                [Codex: 制度理性派]
                             强类型公文 / 沙箱红线 / 委派协议
                                      ▲
                                     / \
                                    /   \
                                   /     \
                                  /       \
   [Claude Code: 现场经验派] ◄────┼───────┼────► [Pi: 极简微内核派]
   运行时优先 / 弹性自愈 / 动态装配     \       /    单一事件循环 / 状态即文件 / 零魔法
                                    \     /
                                     \   /
                                      \ /
                                       ▼
                             [生产级落地平衡点]
```

### 三大路线的工程特质对照

| 维度 | Claude Code（现场经验派） | Codex（制度理性派） | Pi（极简微内核派） |
|---|---|---|---|
| **代表工程** | Anthropic Claude Code (`claude-code`) | OpenAI Codex / ChatGPT Desktop | Pi by Mario Zechner (`pi-bluebook`) |
| **设计信仰** | **运行时优先（Runtime First）**：模型已在循环中，随时准备修补破损状态 | **控制面优先（Control Plane First）**：秩序安在强类型基座与规则治理条例中 | **极简纯净（Micro-kernel First）**：消除一切不可预测的中间抽象，透明胜于便利 |
| **核心抽象** | Query Loop、Compact 状态机、Withheld Errors、PTL 自愈 | Typed Substrate、Contextual Fragment、Exec Policy、Sandboxes | Async Event Loop、Append-only JSONL Stream、Hand-off 摘要 |
| **状态持久化** | 内存工作说明书（Session Memory）+ 动态折叠 | 结构化 Rollout 数据库 + 分支快照 | 纯追加文件（Session-as-Files），无数据库依赖 |
| **扩展机制** | Markdown 现场规矩（`CLAUDE.md`）+ 按需 Skill 注入 | 显式 Hook 引擎（排序/超时/平台可解释）+ 工具化委派 | 单纯的函数/工具注册，不引入复杂中间件层 |
| **主要代价** | 逻辑复杂度极高，难以在受限环境进行形式化验证 | 系统体量偏重，二次定制和快速敏捷实验门槛高 | 业务防御与多智能体协调需上层开发者自行手写 |

---

## 2. 五大收敛共识（Convergences）

不论是重型的 Claude Code / Codex，还是轻型的 Pi，所有在真实高负载生产环境中存活下来的 Harness，都在以下**五大底层工程不变式**上达成了绝对共识：

### ① 零信任模型假定（Zero-Trust Model Invariant）
> **原则**：永远不要把大模型当成“理性的开发同事”，它本质是一个不可信的概率执行器。
- 模型具备强烈的幻觉、顺从倾向（Sycophancy）与试探行为；
- 拒绝任何让模型直接执行宿主系统提权命令的设计；
- 认知防线（Prompt 约束、HITL 确认）与物理防线（容器沙箱、只读文件系统、网络隔离）必须强行解耦。

### ② 提示缓存分界纪律（Prefix Caching Boundary Discipline）
> **原则**：提示缓存不仅是省钱手段，更是防止推理集群 TTFT 排队雪崩的物理架构基石。
- **单调追加约束（Monotonic Append-only）**：System Prompt、Tools Schema、基础环境上下文必须严格放置于最前端且字节级不变；
- **禁止中间插入（No Mid-stream Drift）**：严禁在多轮历史中间动态插拔时间戳、环境变量或局部通知；任何动态内容必须作为最新的 User 消息或在末尾追加；
- 破坏前缀缓存的代价：单轮执行延迟上升 5~10 倍，GPU 吞吐暴跌。

```text
  [静态稳定前缀 (KV Cache 命中: 100%)]  │ [动态增长区 (KV Prefill)]
 ────────────────────────────────────────┼────────────────────────► Token 轴
  System Prompt + Tools + Core Rules     │ History Turn 1..N + Latest Observation
```

### ③ 循环账本闭合与合成补账（Loop Ledger & Synthetic Invariant）
> **原则**：任何非正常的执行中断，都不能以破坏底层模型协议为代价。
- 大模型协议严格规定：每一个 `tool_use (id=xyz)` 必须有对应的 `tool_result (id=xyz)` 配对闭合；
- 当发生**用户强制打断 (Ctrl+C)**、**沙箱超时熔断**或**网络崩溃**时，Harness 必须合成 `Synthetic Tool Result`（例如：`[Execution Aborted by User]`）回灌上下文；
- 绝不能直接丢弃未完成的调用帧，否则下一轮推理模型会因上下文非法而彻底崩溃（Protocol Deserialization Failure）。

### ④ 带语义锚点的显式上下文压紧（Explicit Compaction with Semantic Anchoring）
> **原则**：上下文不可逆膨胀是客观物理定律，但压紧不能靠盲目暴力截断。
- **微压缩（Microcompact）**：对冗长工具输出（如 5000 行 grep 结果）做就地折叠，仅保留首尾与关键命中行；
- **宏观压缩（Macro Compaction）**：到达窗口硬上限（如 80%）时，通过受控子模型提炼 **Hand-off Summary / Session Memory**；
- 必须明确持久化保留：当前未完成的 TODO 任务、已修改的关键文件列表、失败的尝试教训；
- 压紧后显式重置缓存边界，并在新起点注入状态快照。

### ⑤ 工具调度与物理环境隔离（Managed Dispatch & Sandboxing）
> **原则**：工具调度是受管接口，代码执行必须放逐进独立物理环境。
- 拒绝同步阻塞宿主线程，采用并发安全分组（Partitioning）与异步协程派发；
- 真正的代码解释器必须运行在轻量 Docker 容器或 MicroVM（如 E2B / Firecracker）中，封死外网与特权。

---

## 3. 三大分歧争鸣（Divergences）

在底层共识之外，三大流派在工程权衡与设计哲学上展现了深远的分歧：

```text
                              三大关键架构分歧
  
  [1. 会话持久化模型]   Append-only JSONL 流   vs   Typed Rollout 快照   vs   Session Memory
  [2. 规则扩展机制]     显式 Hook 引擎/指纹     vs   现场 Markdown 经验   vs   无中间件纯函数
  [3. 架构体量选型]     极简微内核 (Micro)     vs   工业重型全包 (Heavy Batteries-included)
```

### 分歧一：会话与持久化形态（Session Persistence）
- **Pi 流派（Append-only JSONL Stream）**：
  - 会话就是一个简单的 `.jsonl` 文件，每发生一个事件就追加一行（`UserMessage`、`AssistantChunk`、`ToolResult`）；
  - 优点：具备极高的**可移植性（Session Portability）**，跨机器直接 copy 文件即可完整重放与分支（Fork）；
  - 缺点：检索长历史时需要全量遍历解析，缺乏关系型索引。
- **Codex 流派（Typed Rollout 快照与数据库）**：
  - 强依赖结构化数据库或复杂内存 Rollout 模型，将线程、子代理、审批记录作为一等公民持久化；
  - 优点：支持多维度审计、精确时间旅行与企业权限隔离；
  - 缺点：系统状态与外部存储高度耦合，迁移成本高。
- **Claude Code 流派（Living Working Memory）**：
  - 将会话记忆提炼为结构化工作说明书，随着任务演进就地重写工作说明书；
  - 优点：对当前长任务专注度高，模型时刻明确主线目标；
  - 缺点：原始探索细节会被摘要覆盖，回溯特定调试过程相对困难。

### 分歧二：扩展性与规则治理（Extensibility & Governance）
- **制度化资产（Codex 路线）**：
  - 引入完整的 `Skill` 安装生命周期、SHA256 指纹比对、`Hook` 排序与平台能力可解释探测；
  - 适合跨组织、上百名工程师共享标准化 Agent 规则。
- **现场经验（Claude Code 路线）**：
  - 依靠目录级 `CLAUDE.md` 级联继承与动态 Skill 注入，容许开发者用最自然的自然语言教导 Agent；
  - 适合极客个人、单兵作战与快速演进的代码库。

### 分歧三：重型 Harness vs 极简微内核的终极权衡
- **重型 Harness**：将错误自愈、沙箱驱动、权限确认、多代理通信等全部打包（Batteries-included），开箱即用，但架构像一艘航空母舰；
- **极简微内核**：只提供最基础的“事件循环 + 消息追加 + 工具调用”，其他一切作为纯净插件外挂，代码量少于 1000 行，透明易控。

---

## 4. 工业落地决策矩阵：如何为团队技术选型？

针对不同业务场景，团队应如何选择或裁剪 Agent Harness 架构？

```text
                           场景决策树 (Decision Tree)
  
  你的业务是否涉及不可信第三方代码执行？
    ├─► 是 ──► [必选] Codex 风格沙箱隔离 (E2B / Docker network=none)
    └─► 否 ──► 是否需要企业级审计与多组织规范分发？
                 ├─► 是 ──► [推荐] Codex 制度路线 (Typed Hooks + Fingerprint Skills)
                 └─► 否 ──► 是否需要高度定制并嵌入专用 IDE / 极速推理？
                              ├─► 是 ──► [推荐] Pi 风格极简微内核 (JSONL + Zero Magic)
                              └─► 否 ──► [推荐] Claude Code 经验路线 (Dynamic Loop + Compact)
```

| 场景画像 | 推荐架构形态 | 必配防御与特性 |
|---|---|---|
| **个人极客 CLI 工具** | 极简微内核 (Pi 模式) | Append-only JSONL 会话，Prompt 严格前缀缓存，本地直接执行 |
| **团队敏捷 Coding Agent** | 运行时弹性优先 (Claude Code 模式) | 动态 `AGENTS.md` 规则级联，双层记忆压缩，HITL 权限拦截器 |
| **企业级安全研发平台** | 制度公文与沙箱优先 (Codex 模式) | 显式 Hook 审计，Local Docker/MicroVM 隔离，严格工具化委派协议 |
| **多租户高危代码评测 SaaS** | 强类型沙箱微内核架构 | 纯出网反向 WSS，双密钥隔离，JIT 按需冷启动与空闲快照回收 |

---

## 5. 本日工程资产与交付标准

在今天的实战落地中，我们在 `mini-harness` 中交付统一收敛引擎：
1. **`PrefixCacheDisciplineValidator`**：校验并保护提示缓存前缀稳定性，物理拦截非法漂移；
2. **`SessionPortabilityEngine`**：打通 Pi（JSONL 流）、Codex（Rollout 快照）与 Claude Code（工作说明书）三方会话互转；
3. **`SyntheticLedgerInvariants`**：形式化闭合异常中断下的工具调用账本；
4. **`ArchitectureDecisionAdvisor`**：参数化决策算法，为特定生产场景自动推荐架构防御参数。
