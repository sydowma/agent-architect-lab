# Day 16: Query Loop 驱动机制、心跳检查与生命周期状态机

> **学习模块**：Week 3 工业级蜕变 —— 驾驭工程与系统防御 (Claude Code Harness Engineering)  
> **核心参考**：`harness-books/book1-claude-code` 第 3 章（Query Loop：代理系统的心跳）  
> **源码对应**：[day16_query_loop.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/day16_query_loop.py) | [day16_test.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/day16_test.py)  

---

## 1. 核心架构认知：循环是智能体的“心跳”

把 AI Coding Agent 简单理解为一个“能调工具的单次问答接口”，是工程落地中最致命的认知误区。真实世界的软件开发具有以下典型特征：
- 一次目标需要跨越 5 ~ 30 个动作轮次；
- 中途可能遭遇文件不存在、命令超时、权限审批被拒、单测报错；
- 用户可能会中途强行按下 `Ctrl+C` 打断；
- 输出可能撞上 `max_tokens` 被截断，或者上下文撑爆。

在 Claude Code 源码（`src/query.ts:241`）中，`query()` 仅仅是一个薄薄的入口外壳，真正支撑系统长期运转的核心是 **`queryLoop()`**。

```text
       ┌────────────────────────────────────────────────────────┐
       │                 Query Loop 运行时状态机                │
       │                                                        │
       │   ┌───────────────┐           ┌──────────────────┐     │
       │   │ govern_input  │ ────────► │   stream_model   │     │
       │   │  (前置治理)   │           │   (事件流消费)   │     │
       │   └───────▲───────┘           └────────┬─────────┘     │
       │           │                            │               │
       │           │ ◄─────── advance ──────────┤               │
       │           │   (状态单调推进/自愈)      ▼               │
       │   ┌───────┴───────┐           ┌──────────────────┐     │
       │   │   recover     │ ◄──────── │ schedule(tools)  │     │
       │   │ (梯级自愈分支)│           │ (工具调度与补账) │     │
       │   └───────────────┘           └──────────────────┘     │
       └────────────────────────────────────────────────────────┘
```

> **核心箴言**：
> **代理系统的关键能力，不在于它单次回答多么惊艳，而在于它经过 20 轮执行、报错、打断后，还知不知道自己在做什么，以及能不能优雅恢复。**

---

## 2. 状态属于主业务：解剖 `State` 实体

在许多玩具级系统中，状态散落在全局变量或局部循环中；而在 Claude Code 中，状态被显式定义为一个完整的跨迭代对象（`src/query.ts:203-217`）：

```typescript
// Claude Code 核心运行时状态对象
interface State {
  messages: Message[];                       // 严格按因果追加的对话事件流
  toolUseContext: ToolUseContext;            // 工具注册表、权限状态与沙箱配置
  autoCompactTracking: AutoCompactTracking;  // 上下文压缩水位追踪与失败计数器
  maxOutputTokensRecoveryCount: number;      // 输出截断自愈重试次数
  hasAttemptedReactiveCompact: boolean;      // 响应式压缩熔断标志，防止死回环
  pendingToolUseSummary: string | null;      // 挂起的工具执行状态摘要
  stopHookActive: boolean;                   // 停止钩子与审查器激活状态
  turnCount: number;                         // 轮次单调递增计数器
  transition: TransitionType;                // 状态转移指令 (continue | terminate | break)
}
```

每个 Query Loop 迭代绝不是单纯的重试，而是**前一轮留下的所有问题（错误、预算、未完成工具）作为状态整体输入给下一轮**。

---

## 3. 输入治理先于模型推理 (Input Governance Pipeline)

传统工程师常以为系统最核心的动作是“调模型”，但 Claude Code 在 `queryLoop()` 中把大批工程代码放在了**模型调用之前**：

```text
[进入模型流前的前置治理管线 (Pre-call Governance Pipeline)]
1. 记忆预取 (Memory & Skill Prefetch): 异步拉取与当前任务相关的持久记忆
2. 截取有效边界 (Compact Boundary): 过滤掉已被压缩折叠的远古历史
3. 工具结果预算 (Tool Result Budget): 强制截断单次吐出数万字符的巨型输出
4. 历史轻量修剪 (History Snip & Microcompact): 对早期工具结果实施微压缩
5. 自动折叠触发 (AutoCompact Check): 水位逼近 75% 时主动执行 GC
```

这体现了极高的工程克制：**先由运行时把现场整理干净，再把高纯度的上下文交给模型；绝不把混乱的垃圾输入甩给模型去赌概率！**

---

## 4. 中断处理与工具账本平衡 (Synthetic Tool Results)

在长链路 Agent 中，用户随时可能按下 `Ctrl+C` 中断任务。
许多初学者系统的做法是直接 `break` 退出循环——**这在现代 Tool Calling 协议下会造成致命灾难！**

### 为什么不能直接退出？
在大模型 API 规范中，一旦 Assistant 消息中吐出了一个带有 `id="call_123"` 的 `tool_use`，紧随其后的下一条消息**必须包含对应 `tool_use_id="call_123"` 的 `tool_result`**。
如果用户在中途强行打断，而系统直接退出，那么留在磁盘 Transcript 里的记录就是一段**断头残账**！当下次加载该会话时，API 将直接抛出 `400 Invalid Message: Missing matching tool_result`！

### Claude Code 的解法：合成补账（Synthetic Tool Results）
```python
def drain_tools_with_synthetic_results(state: QueryLoopState):
    """当遭遇用户中断或超时时，自动为所有挂起未完成的 tool_use 补齐合成结果"""
    for pending_call in state.get_pending_tool_calls():
        state.append_message({
            "role": "tool",
            "tool_call_id": pending_call.id,
            "name": pending_call.name,
            "content": "[Execution aborted by user interrupt. State preserved.]"
        })
```
> **工程铁律**：**只要系统向外承诺了一段执行，就要在中断时把账补平。**

---

## 5. 梯级自愈分支与停止条件矩阵

Claude Code 将失败视为一等公民（First-Class Citizens），构建了层层递进的自愈路径：

### ① `max_output_tokens` 截断恢复
- **第 1 级**：若未达系统最大输出上限，自动提升 `maxOutputTokensOverride`，重试本轮；
- **第 2 级**：若已达上限，系统自动生成一条 Meta Message：`"Your output was truncated due to token limits. Continue directly from where you left off without repeating previous text."`，驱动模型在下一轮实现**无缝拼接续写**！

### ② `prompt_too_long` 上下文超限恢复
- **第 1 级**：尝试将积压的局部折叠（Context Collapse）提交；
- **第 2 级**：若仍超限，触发强行响应式压缩（Reactive Compact）；
- **防自回环熔断**：一旦 `hasAttemptedReactiveCompact` 为 True 且依然超限，立即安全退出，**严禁陷入“压缩 -> 依然超限 -> 再压缩”的死回环！**

### 📊 Claude Code 停止条件矩阵 (Termination Matrix)

| 事件触发 | 前置条件 | 运行时处理机制 | 下一步状态 |
| :--- | :--- | :--- | :--- |
| **Stream 结束 + 包含 `tool_use`** | 有未完成工具调用 | 工具调度器并行/串行执行 | `FOLLOW_UP` 继续循环 |
| **Stream 结束 + 无 `tool_use`** | 正常文本输出 | 运行 Stop Hooks 检查 | `DONE` 干净终结 |
| **用户打断 (`Ctrl+C`)** | 任意轮次 | 补齐 Synthetic Tool Result | `ABORTED` 退出 |
| **Token 截断 (`max_tokens`)** | 重试次数未超限 | 自动追加 Meta 续写指令 | `RECOVER_CONTINUE` 续写 |
| **上下文溢出 (`prompt_too_long`)**| 未尝试过响应式压缩 | 触发 Reactive Compact | `REACTIVE_COMPACT` 重整 |
| **二次溢出 (Double Failure)** | `hasAttemptedReactiveCompact == True` | 触发熔断保护 | `TERMINATE_ERROR` 报错退出 |

---

## 6. Query Loop 的三大硬约束不变式 (Invariants)

任何符合工业级标准的 Query Loop 引擎，必须在每一轮循环严格保持以下三条数学不变式：

```python
# 不变式 1: 轮次与状态推进单调递增 (防时光倒流)
assert state.turn_count >= previous_turn_count

# 不变式 2: 工具账本绝对闭环 (每一个 tool_use 必须有且仅有一个 tool_result)
assert len(emitted_tool_uses) == len(collected_tool_results)

# 不变式 3: 防双重失败自回环 (一次长任务中 Reactive Compact 只能尝试一次)
if state.has_attempted_reactive_compact:
    assert no_further_reactive_compact_allowed
```
