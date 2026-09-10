# Day 18: 上下文治理、Memory 索引与 AutoCompact 上下文压紧 (Context Compaction & Memory Governance)

> **学习模块**：Week 3 工业级蜕变 —— 驾驭工程与系统防御 (Claude Code Harness Engineering)  
> **核心参考**：`harness-books/book1-claude-code` 第 5 章（上下文治理：Memory、CLAUDE.md 与 Compact 是预算制度）  
> **源码对应**：[day18_context_compaction.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/day18_context_compaction.py) | [day18_test.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/day18_test.py)  

---

## 1. 核心哲学：上下文不是仓库，而是工作内存预算

“信息越多，系统越聪明”是一个在初学者中极其普遍却致命的低级神话。
代理系统不是图书馆，上下文更不是“塞进去就算拥有”的废纸篓。在真实的工业级代码代理（如 Claude Code）中：
1. **注意力稀释（Attention Dilution）**：上下文膨胀后，模型检索关键约束的准确率急剧下降，产生大量“读过却无视”的低级失误；
2. **自我污染（Self-Contamination）**：历史中失败的尝试、过期的报错日志、被推翻的中间方案若不清理，模型会在后续轮次中反复重蹈覆辙；
3. **KV Cache 经济学**：上下文不是免费的，每一次对话都伴随着真实 Token 的计费与显存消耗。

```text
       ┌────────────────────────────────────────────────────────┐
       │             上下文治理：从聊天记录到工作内存           │
       │                                                        │
       │  [失控系统]                                             │
       │  历史对话 ──> 报错日志 ──> 巨型文件 ──> 爆炸与遗忘      │
       │                                                        │
       │  [治理系统 (Claude Code)]                              │
       │  ┌───────────────┐ ┌───────────────┐ ┌───────────────┐ │
       │  │   CLAUDE.md   │ │   MEMORY.md   │ │ SessionMemory │ │
       │  │  长期制度约束  │ │  轻量寻址索引  │ │ 结构化工作书  │ │
       │  └───────┬───────┘ └───────┬───────┘ └───────┬───────┘ │
       │          └─────────────────┼─────────────────┘         │
       │                            ▼                           │
       │               ┌────────────────────────┐               │
       │               │ AutoCompact 受控重启   │ 预算警戒线    │
       │               │ (前置清洗 ──> 后置重建)│ 熔断保护      │
       │               └────────────────────────┘               │
       └────────────────────────────────────────────────────────┘
```

---

## 2. 三层记忆解耦：长期规则、持久索引与会话连续性

Claude Code 从架构上拒绝把“长期协作规则”、“持久经验知识”和“临场临时对话”混为一谈，而是实施严密的三层分工：

### ① 长期制度层：`CLAUDE.md` 体系
- **职责**：团队规范、编码风格、仓库核心架构约束。
- **层级加载链条**：
  `Managed (/etc/...)` -> `User (~/.claude/...)` -> `Project (repo/CLAUDE.md)` -> `Local (CLAUDE.local.md)`。
- **加载法则**：离当前工作目录越近的 project 规则，优先级越高；越偏向私有、越偏向本地的规则越晚加载，越靠近模型注意力前沿。
- **安全过滤**：支持 `@include`，但严格限制文本扩展名白名单，严防二进制与巨型垃圾侵入。

### ② 长期记忆索引层：`MEMORY.md`（低成本寻址，拒绝日记本）
- **职责**：跨会话的持久经验与用户习惯存储。
- **核心模式（Two-Step Convention）**：
  1. 将具体 memory 详情写入独立的 topic 文件（如 `.claude/memory/react_tips.md`）；
  2. 在入口文件 `MEMORY.md` 中仅追加一行指针与精简摘要。
- **物理硬约束**：
  - `MAX_ENTRYPOINT_LINES = 200`（最大 200 行）
  - `MAX_ENTRYPOINT_BYTES = 25_000`（最大 25 KB）
- **超限自动截断**：一旦超标，立即执行截断并追加明确系统警告：`[WARNING: Memory entrypoint truncated... Please move detailed content into dedicated topic files.]`。彻底粉碎把入口文件当垃圾仓库的倾向。

### ③ 会话连续性层：`SessionMemory`（结构化工作说明书）
- **职责**：当前任务做到哪一步了、踩过什么坑、改了哪些文件、下一步干什么。
- **固定骨架结构**：
  - `Current State`（当前状态，始终反映最近工作）
  - `Task specification`（任务具体定义）
  - `Files and Functions`（涉及的文件与核心函数）
  - `Workflow`（执行流）
  - `Errors & Corrections`（踩坑与修复经验）
  - `Codebase and System Documentation`（关键文档）
  - `Learnings`（重要收获）
  - `Key results`（关键成果）
  - `Worklog`（工作流水）
- **预算硬顶与激进浓缩（Aggressive Condensation）**：
  - 单节限制：`MAX_SECTION_LENGTH = 2,000` tokens/字符；
  - 总预算：`MAX_TOTAL_SESSION_MEMORY_TOKENS = 12,000` tokens。
  - **浓缩优先级**：当超出预算时，必须激进压缩，**优先且强制保留 `Current State` 与 `Errors & Corrections`**！

---

## 3. AutoCompact 预算与缓冲制度

上下文治理首先是数学上的预算治理。系统绝不能等窗口被吃得只剩最后 1 个 token 时才惊慌抢救。

### ① 关键预算阈值表

| 常量名称 | 数值 | 架构作用 | 源码对应 |
|---|---|---|---|
| `MAX_ENTRYPOINT_LINES` | `200` | `MEMORY.md` 入口文件行数物理上限 | `memdir/memdir.ts` |
| `MAX_ENTRYPOINT_BYTES` | `25,000` | 入口文件物理字节上限（25KB） | `memdir/memdir.ts` |
| `MAX_SECTION_LENGTH` | `2,000` | `SessionMemory` 单节长度预算 | `SessionMemory/prompts.ts` |
| `MAX_TOTAL_SESSION_MEMORY_TOKENS` | `12,000` | `SessionMemory` 全文总预算上限 | `SessionMemory/prompts.ts` |
| `MAX_OUTPUT_TOKENS_FOR_SUMMARY` | `20,000` | 执行 Compact 摘要生成时预留的输出预算 | `compact/autoCompact.ts` |
| `AUTOCOMPACT_BUFFER_TOKENS` | `13,000` | 触发 AutoCompact 的警戒缓冲垫 | `compact/autoCompact.ts` |
| `MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES` | `3` | 连续压缩失败熔断阈值（Circuit Breaker） | `compact/autoCompact.ts` |

### ② 预算推导公式
```python
# 1. 计算留给上下文历史的有效容量
effective_context_window = context_window - MAX_OUTPUT_TOKENS_FOR_SUMMARY

# 2. 扣除安全警戒缓冲，得到自动压缩触发阈值
auto_compact_threshold = effective_context_window - AUTOCOMPACT_BUFFER_TOKENS

# 3. 判定触发
should_compact = (current_tokens >= auto_compact_threshold)
```
以 128k 上下文为例：
- `effective_context_window = 128,000 - 20,000 = 108,000`
- `auto_compact_threshold = 108,000 - 13,000 = 95,000`
- 当对话消耗达到 95,000 tokens 时，系统就会在下一轮自动启动压缩，留下足足 33,000 tokens 的安全余裕，防止模型在压紧过程中遭遇 `Prompt Too Long` 暴毙！

---

## 4. `compactConversation()`：从“聊天总结”到“受控重启”

许多初级 Agent 框架对压缩的理解就是：“调用 LLM 把历史消息总结成一段话，然后把历史清空，替换为这一句话”。
**这种做法在真实的编码场景下会导致灾难性的失忆**：
- 模型忘了正在编辑的文件内容，只得重新全部读一遍；
- 模型丢掉了刚制定好的 Plan，开始无序乱窜；
- 模型丢掉了特定的 Skill 指令约束。

Claude Code 认为：**Compact 是一次严密受控的系统重启（Controlled Reboot）**！

```text
                      [原始上下文超限触发 Compact]
                                  │
         ┌────────────────────────┴────────────────────────┐
         │                                                 │
         ▼                                                 ▼
[阶段一：前置清洗 Pre-Cleanse]                  [阶段二：结构化摘要 Extraction]
- stripImagesFromMessages (图片替为 [image])    - 提炼 SessionMemory 骨架
- stripReinjectedAttachments (剥离待恢复附件)    - 若超长触发 PTL 头部截断
         │                                                 │
         └────────────────────────┬────────────────────────┘
                                  │
                                  ▼
                    [阶段三：后置工作语义重建 Reconstruction]
                    - 清空过期 readFileState 缓存
                    - 重新装配活跃文件附件 (Active File Attachments)
                    - 重新装配 Plan & Plan Mode 纪律附件
                    - 重新装配 Invoked Skills (应用 token cap 截断保头)
                    - 写入 CompactBoundary 锚点消息
                                  │
                                  ▼
                     [建立全新 KV Cache 物理基线]
```

### 关键工程细节：
1. **前置剥离（Pre-Cleanse）**：
   图片、大型原始输出、即将在后置流程中重新注入的文件附件，全部从待摘要列表中剥离或简化（如替为 `[image]`）。避免浪费宝贵的摘要输入 token。
2. **救火工具防爆（PTL Retry）**：
   如果发送给摘要模型的请求本身就报了 `Prompt Too Long`，说明系统不仅主流程溢出，连救火动作也溢出了。系统内置 `truncateHeadForPTLRetry()`，安全裁剪最早期的无用历史继续重试。
3. **后置工作语义重建（Post-Compact Reconstruction）**：
   摘要不是为了“悼念过去”，而是为了“继续工作”。压缩后必须把当前正在改的文件、活跃的 Plan 状态、当前用到的 Skill 重新装配回上下文！
4. **Per-skill 截断原则（Per-skill truncation beats dropping）**：
   恢复技能附件时，若技能内容过长，宁可限制每个技能的 token 上限（保留前 N 行核心规则），也绝不能因为超标而整条丢弃！

---

## 5. 连续失败熔断器（Circuit Breaker）

在全球工业化运行中，模型有时会因为 prompt 过长、输出畸形或格式解析失败而导致 Compact 失败。
如果没有熔断器，系统会在每一轮查询循环中反复检测到“上下文超标”，于是无限次发起无意义的 Compact API 请求，造成数百美元的算力空转与死锁。

Claude Code 设计了 `AutoCompactTrackingState`：
```python
@dataclass
class AutoCompactTrackingState:
    compacted: bool = False
    turn_counter: int = 0
    consecutive_failures: int = 0
    circuit_broken: bool = False
```
- 每次 Compact 成功，`consecutive_failures` 立即归零；
- 每次 Compact 失败，`consecutive_failures += 1`；
- **当 `consecutive_failures >= 3` 时，立即触发 Circuit Breaker（熔断锁死）**！后续不再盲目重试，而是升级为致命异常上报给用户，彻底终结无限浪费循环。

---

## 6. 核心架构总结

> **上下文是工作内存。治理它的目标不是“留存历史”，而是“支持系统接下来能继续把事情做对”。**
> 
> 1. **规则入 `CLAUDE.md`**：稳定长期约束层级加载，不占对话轮次；
> 2. **索引入 `MEMORY.md`**：只放指针不放长文，200行/25KB物理硬拦截；
> 3. **过程入 `SessionMemory`**：九大固定骨架结构化，首保当前状态与纠错记录；
> 4. **预算留足余裕**：20,000 输出预算 + 13,000 缓冲警戒线，3次失败坚决熔断；
> 5. **受控重启重建语义**：剥离临时噪点，提炼骨架，重建活跃文件与计划，写下 Compact Boundary！
