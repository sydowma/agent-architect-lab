# Day 22: 双雄对决：Claude Code vs Codex 架构哲学与控制面对比

> **学习模块**：Week 4 工业体系与方法论 (Harness Books Book 2: Comparing Claude Code and Codex)  
> **核心参考**：`harness-books/book2-comparing` 第 1 章（为什么要把 Claude Code 和 Codex 放在一起看）与 第 2 章（两种控制面：Prompt 拼装与 Instruction Fragment）  
> **源码对应**：[day22_dual_control_planes.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/day22_dual_control_planes.py) | [day22_test.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/day22_test.py)  

---

## 1. 核心问题：两种“不信任模型”的驯化路线

如果只把 Claude Code 和 Codex 看作“终端编程助手”，比较就会沦为表层的工具多寡或命令语法。
真正让这两套工业级框架在架构史上分道扬镳的，是它们**对“大模型本质不可靠”这一事实的不同防御哲学与秩序落脚点**：

```text
       ┌────────────────────────────────────────────────────────┐
       │             双雄驯化路线的秩序落脚点对比               │
       │                                                        │
       │  [共同前提：模型不是同事，是不可信的概率执行体]        │
       │                 │                                      │
       │        ┌────────┴────────┐                             │
       │        ▼                 ▼                             │
       │  ┌───────────────┐ ┌───────────────┐                   │
       │  │  Claude Code  │ │     Codex     │                   │
       │  │ (运行时优先)  │ │ (控制面优先)  │                   │
       │  └───────┬───────┘ └───────┬───────┘                   │
       │          │                 │                           │
       │          ▼                 ▼                           │
       │  [动态装配线]       [带编号公文系统]                   │
       │  秩序安在心跳    秩序安在类型基座             │
       │  (Runtime Loop)    (Typed Substrate)                   │
       │  - 动态拼接 Prompt  - 显式 Fragment 片段               │
       │  - 现场折叠/压紧    - START/END 物理 Marker            │
       │  - 防下一轮弄坏     - 目录层级公文继承                 │
       └────────────────────────────────────────────────────────┘
```

| 维度 | Claude Code（现场经验派） | Codex（制度理性派） |
|---|---|---|
| **核心问题意识** | “模型已经在循环里了，怎样保证它这一轮不把下一轮弄坏？” | “模型会接触大量控制信息，怎样把这些做成显式、可策略化的结构？” |
| **首要关注点** | Query loop、compact、中断处理、恢复分支、工具结果回灌 | Fragment 定义、tool schema、exec policy 策略语言、thread 状态持久化 |
| **工程气质** | 像一个经常处理线上事故的值班组长，充满实战防御机制 | 像一个制定公文规范与沙箱条例的系统架构师，强调类型与边界 |
| **代码基座** | TypeScript，强偏向主循环与响应式流编排 | Rust，强偏向类型系统（Typed Substrate）与独立 Crate 隔离 |

---

## 2. 控制面两大范式：动态装配线 vs 结构化公文系统

控制面的本质是**行为协议的总线**，绝不是“把 Prompt 写得像老程序员说话”的文风问题。

### ① Claude Code：动态装配线（Dynamic Assembly Line）
在 `constants/prompts.ts`、`utils/systemPrompt.ts` 和 `claudemd.ts` 中，控制面被视为一段在每轮循环中根据现场条件动态重算的拼接物：
```python
system_prompt = concat(
    default_prompt,     # 基础宪法底板
    append_prompt,      # 外加要求
    agent_prompt,       # 角色补丁
    claudemd_layers,    # team -> personal -> project 层级规则
    memory_sections,    # session memory 工作说明书
    output_style        # 表达约束
)
```
- **核心特点**：同一套主循环动态适配多种任务场景；每轮根据当前工作区状态、活跃文件进行微调。
- **潜在代价**：如果装配顺序管理不当，容易发生前后规则覆盖、注意力稀释；高度依赖运行时的 Prompt 治理与压缩机制。

### ② Codex：带编号的结构化公文系统（Structured Fragment System）
Codex（`instructions/src/fragment.rs` 与 `user_instructions.rs`）拒绝让指令成为自由文本，而是将其封装为带明确边界与元数据的 **Contextual Fragment**：
```text
<AGENTS_MD_START_MARKER>
# AGENTS.md instructions for /Users/mark/workspace/backend
- Strictly avoid modifying auth tokens
- Run cargo test before PR
<AGENTS_MD_END_MARKER>
```
- **核心特点**：
  - 定义常量 `AGENTS_MD_START_MARKER` / `AGENTS_MD_END_MARKER`、`SKILL_OPEN_TAG` / `SKILL_CLOSE_TAG`；
  - 每个片段携带元数据：`source_dir`、`name`、`path`；
  - 包装为结构化的 `ResponseItem::Message`，直接挂载到 Thread 上；
  - **连“这段规则来自哪个目录、哪个 Skill 文件”都通过元数据显式告知模型，绝不让模型猜测。**
- **工程收益**：具备极强的可调试性与审计性，片段天然支持程序化的优先级合并（Merge Rules）与可见性控制（Visibility Policy）。

---

## 3. `CLAUDE.md` vs `AGENTS.md`：现场公告板 vs 法定公文

两者同样是项目根目录下的本地规则文件，但定位截然不同：

### ① `CLAUDE.md`：工程现场公告板
- 贴近任务目录，和 memory、skill 共同构成“做事时该记住什么”；
- 擅长登记项目常识、环境坑点、禁用命令与团队习惯；
- **定位**：让现场规则以自然的方式进入会话。

### ② `AGENTS.md`：带继承树的法定公文
- Codex 将其拉入严格的 **Hierarchy 继承体系**；
- 开启 `child_agents_md` 时，即使当前子目录没有 `AGENTS.md`，系统也会向上下文注入作用域与继承优先级说明（近目录覆写远目录）；
- **定位**：让现场规则以制度和公文的形式进入系统。

> **一句话总结**：  
> **Claude Code 让现场规则进入会话；Codex 让现场规则进入制度。**

---

## 4. 控制面核心运行时不变式（Runtime Invariants）

```python
# 1. 结构对称性：每个片段必须具备合法的成对 Marker
assert every fragment has matching (START_MARKER, END_MARKER)

# 2. 类型可识别性：来源必须属于合法受管枚举
assert fragment.source in {AGENTS_MD, SKILL, USER_INSTRUCTION}

# 3. 优先级单调性：项目级规则必须压倒团队级，团队级必须压倒默认级
assert precedence(project) > precedence(team) > precedence(default)

# 4. 继承显式性：子目录继承时必须显式标注适用范围，严禁模糊生效
assert child_agents_md enabled => scope_and_precedence_explicitly_declared
```

---

## 5. 架构代价与团队选型指南

| 评估维度 | 动态装配线 (Claude Code) | 结构化公文系统 (Codex) |
|---|---|---|
| **上手与灵活度** | 极高，像搭积木一样随调随改 | 较重，必须定义 Schema、Marker 与序列化管道 |
| **可观测与调试** | 需结合上下文快照反查来源 | 极高，Marker 标签与目录元数据清晰可追溯 |
| **长链路抗衰减** | 依赖运行时 Compact 与微修剪 | 依赖片段级过滤与按需激活（Lazy Loading） |
| **适合团队阶段** | 探索期、注重开发心流与交互的敏捷团队 | 平台化、强调合规审计、跨多团队协作的大型工程组织 |

**结论**：  
没有绝对的优劣，只有不同的恐惧。怕现场变化太快、长会话指令失真的，选择 Claude Code 路线；怕规则来源不清、作用域混乱、无法程序化治理的，选择 Codex 路线。
