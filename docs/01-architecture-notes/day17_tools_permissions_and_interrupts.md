# Day 17: 工具受管调度、权限分级与人在回路 (Tools, Permissions & Interrupts)

> **学习模块**：Week 3 工业级蜕变 —— 驾驭工程与系统防御 (Claude Code Harness Engineering)  
> **核心参考**：`harness-books/book1-claude-code` 第 4 章（工具、权限与中断：为什么代理不能直接碰世界）  
> **源码对应**：[day17_permissions_and_tools.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/day17_permissions_and_tools.py) | [day17_test.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/day17_test.py)  

---

## 1. 核心哲学：为什么代理不能直接碰世界？

只输出文本的模型，出错时主要增加沟通成本；**一旦开始调用工具，动作就会直接作用于物理真实世界**：
- 把一段解释写错了，只是理解偏差；
- 执行了一条错误的 Shell 命令，生产文件会被物理删除、后台服务会被异常杀掉、Git 历史会被强制篡改。

能力增强伴随后果增强。在 Claude Code 中，工具绝不是“大模型能力的自然延长线”，而是**必须由运行时代为管理风险的受管执行接口（Managed Execution Interfaces）**。

```text
       ┌────────────────────────────────────────────────────────┐
       │                工具调用的受管防护车身                  │
       │                                                        │
       │   [LLM 吐出 Tool Calls: A, B, C]                       │
       │                 │                                      │
       │                 ▼                                      │
       │     ┌────────────────────────┐                         │
       │     │ partitionToolCalls()   │ 并发安全分组            │
       │     │ (只读并行 vs 写操作串行) │                         │
       │     └───────────┬────────────┘                         │
       │                 │                                      │
       │                 ▼                                      │
       │     ┌────────────────────────┐                         │
       │     │ useCanUseTool()        │ 三态权限判定             │
       │     │ (allow / deny / ask)   │ (拒绝布尔偷懒)           │
       │     └───────────┬────────────┘                         │
       │                 │                                      │
       │                 ▼                                      │
       │     ┌────────────────────────┐                         │
       │     │ SafeBashGuard 深度防线 │ 高危命令/子命令上限拦截 │
       │     └───────────┬────────────┘                         │
       │                 │                                      │
       │                 ▼                                      │
       │   [受控沙箱物理执行 & 顺序回放 Context Modifier]       │
       └────────────────────────────────────────────────────────┘
```

---

## 2. 并发调度与因果顺序回放 (Concurrency & Replay)

很多初学者系统一看到模型并发吐出了多个 `tool_use`，就无脑使用 `Promise.all` 或线程池全量并发跑——**这会彻底毁灭系统状态的一致性**！

### ① 并发安全分组 (`partitionToolCalls`)
Claude Code 在执行前先调用 `isConcurrencySafe()` 检查工具的 `inputSchema` 与操作类型：
- **只读操作（Concurrency-Safe）**：如 `read_file`, `grep_search`, `list_dir`。它们不改变外部文件状态，被安全归入**并行批次**并发提速；
- **副作用/修改操作（Concurrency-Unsafe）**：如 `edit_file`, `write_file`, `bash`。它们会引发文件系统或进程状态变更，**必须强制拆解为单步串行批次**，依次排队执行。

### ② 上下文修饰符顺序回放 (`contextModifier Replay`)
即便多个只读工具以多线程并发完成，哪个工具先返回是随机的网络/I/O 事件。
Claude Code 严禁最先完成的工具抢先修改上下文！它会在内存中先将所有工具的 `contextModifier` 暂存，**最后严格按照大模型最初生成的 Block 原始顺序统一回放**。
> **工程铁律**：**并发可以提高吞吐，但绝对不能破坏因果秩序。**

---

## 3. 权限先于能力：拒绝布尔偷懒的三态模型

在 `useCanUseTool()` 中，Claude Code 从结构上否认了“模型懂了意图就等于拥有授权”的危险想法。权限判定必须是严苛的三态（Tri-State）：

```text
                       [Tool Call Request]
                                │
                                ▼
                   ┌─────────────────────────┐
                   │   hasPermissionsTo...   │
                   └────────────┬────────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
      【ALLOW】              【DENY】               【ASK】
    (安全直接放行)        (物理硬性驳回)        (进入人在回路审批)
          │                     │                     │
          ▼                     ▼                     ▼
      执行沙箱              记录拒绝原因           挂起并向终端用户
                        (同一 ID 锁定粘性)       展示危险等级与审批
```

1. **`ALLOW`**：安全的低风险操作（如读取只读代码、获取环境状态）；
2. **`DENY`**：明确违规的破坏性动作（如 `rm -rf /`, 探测私有环境变量, 越界写文件），物理拦截；
3. **`ASK`（人在回路 HITL）**：可能引发不可逆后果的高危动作（如 `git push`, 删除目录, 运行编译命令），**系统自己也不替用户做主，必须向真实开发者弹窗/终端确认！**

### 🛡️ 权限系统的三大硬约束不变式 (Invariants)
```python
# 不变式 1: 权限结果必须是严谨的三态，绝不允许布尔值偷懒
assert decision in {ALLOW, DENY, ASK}

# 不变式 2: 无授权绝不得自动提权 (ASK 绝不能自动假定为 ALLOW)
assert ask_never_auto_escalates_to_allow

# 不变式 3: 粘性拒绝 (同一 tool_use_id 被 DENY 后，锁定状态，禁止重试洗白)
assert deny_is_sticky_for_this_tool_use_id
```

---

## 4. 中断行为与 Sibling Error 联动取消

当并发批次中正在运行多个工具时，外部世界和子任务随时可能出意外：
- **`interruptBehavior` 策略分流**：
  - `cancel`：用户一旦在终端按键打断，立即通过 `AbortController` 杀死子进程，生成 `[User interrupted]` 补账；
  - `block`：某些必须保持原子性的操作，用户插话时允许工具跑完自然终点，但在其完成前阻塞新消息进入。
- **兄弟故障联动（Sibling Error Cancellation）**：
  在同一个并行批次中，如果 Tool A 遭遇致命崩溃（如 `FileNotFoundError`），运行时立即触发 `siblingAbortController`，**联动态中止正在执行的 Tool B 和 Tool C**，并为它们补齐合成错误信息（Synthetic Error），避免无意义的算力浪费与状态脏写。

---

## 5. Bash 为什么必须接受特殊高压看管？

在所有的工具中，Shell/Bash 是绝对的**风险放大器**。它几乎没有任何领域边界，可以直接接触文件、进程、网络、环境变量与 Git 树。

Claude Code 对 Bash 采取了最严苛的特例高压治理（`SafeBashGuard`）：
1. **复合子命令上限检查**：
   禁止模型利用管道和分号拼接过长命令（如 `cmd1 && cmd2 || cmd3 ; cmd4`）逃逸权限审查，超过子命令阈值直接 `DENY`；
2. **高危动作正则红线**：
   物理阻断破坏性模式：
   - `rm -rf` / `mkfs` / `dd`
   - `git push -f` / `git push --force`
   - `chmod -R 777`
   - `sudo` / `su`
3. **参数化执行优先**：能用专用小工具（如 `read_file`）解决的，绝不使用 `cat` 或 `head` 走 Bash 绕道。
