# Day 19: 错误自愈、分层恢复与死锁防线 (Error Recovery, Resilience & Process Governance)

> **学习模块**：Week 3 工业级蜕变 —— 驾驭工程与系统防御 (Claude Code Harness Engineering)  
> **核心参考**：`harness-books/book1-claude-code` 第 6 章（错误与恢复：出错后仍能继续工作的代理系统）  
> **源码对应**：[day19_error_recovery.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/day19_error_recovery.py) | [day19_test.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/day19_test.py)  

---

## 1. 核心哲学：工程世界最不值得相信的，就是“正常情况下”

许多原型阶段的 Agent 设计文档通篇都在描述“正常情况下”的理想链路，仿佛只要主路径足够优雅，异常就会自然消失。
然而，一旦进入工业级真实生产环境（长时运行、海量代码库、并发 I/O、复杂 Shell 交互）：
- 模型输出随时会被长度截断（`max_output_tokens`）；
- 上下文随时会溢出（`prompt_too_long`）；
- 救火工具自身会因为输入过长而暴毙；
- Stop Hooks 会与重试逻辑死锁咬合；
- 用户会随时按下 `Esc` 中断执行。

Claude Code 确立了极其冷静的工程准则：
> **错误属于主路径，恢复是必须提前设计好的核心运行机制。**  
> **判断一个代理系统是否成熟，不是看它顺畅时多像人，而是看它出故障时像不像一个有章法、有防线的工业系统。**

```text
       ┌────────────────────────────────────────────────────────┐
       │             错误治理机制：从偶发异常到主路径分层恢复   │
       │                                                        │
       │  [流式执行产生错误 / 截断]                              │
       │                 │                                      │
       │                 ▼                                      │
       │   ┌───────────────────────────┐                        │
       │   │  Withheld Error 扣留机制  │ 识别可恢复错误         │
       │   │  (PTL, MOT, MediaSize)    │ (非白名单立即抛出)     │
       │   └─────────────┬─────────────┘                        │
       │                 │                                      │
       │                 ▼                                      │
       │   ┌───────────────────────────┐                        │
       │   │   分层自愈阶梯 (Ladder)   │ 1. Collapse Drain      │
       │   │   先便宜保守，后全文压缩   │ 2. Reactive Compact    │
       │   └─────────────┬─────────────┘ 3. Truncate Head (救火)│
       │                 │                                      │
       │                 ▼                                      │
       │   ┌───────────────────────────┐                        │
       │   │   死循环防线 (Guardrails) │ 跳过 Stop Hooks        │
       │   │   维持执行叙事一致性       │ Abort 闭合悬空账本     │
       │   └───────────────────────────┘                        │
       └────────────────────────────────────────────────────────┘
```

---

## 2. 可恢复错误扣留暂存（Withheld Errors）

在 `src/query.ts` 的流式执行生命周期中，Claude Code 引入了 `withheld` 机制：**暂时扣下特定类型的错误，不立刻把原始堆栈原样抛给用户**。

### ① 可扣留白名单
```python
assert withheld_error in {
    "prompt_too_long",    # 上下文超出窗口
    "media_size",         # 图片/多媒体超标
    "max_output_tokens",  # 输出达到生成上限被物理截断
}
```
### ② 架构意义
用户真正关心的是“系统还能不能继续干活”。
如果模型吐到一半因为 `max_output_tokens` 停住了，或者因为多读了一个大文件触发了 `prompt_too_long`，系统不应该惊慌失措地把用户赶出交互界面，而是先将错误转交给恢复子系统分层尝试。**只有当所有分层自愈路径全部耗尽时，扣留的错误才会被正式 Surface**。

---

## 3. PTL（Prompt Too Long）分层自愈阶梯与死循环防线

当上下文超长时，很多初级框架要么直接崩溃退出，要么立刻粗暴地调用重型模型做全量总结。Claude Code 制定了严格的阶梯式推进秩序：

```text
               [收到 prompt_too_long 错误]
                           │
                           ▼
          ┌───────────────────────────────────┐
          │  阶梯 1: recoverFromOverflow()    │
          │  排空已 Staged 的 Context Collapse│
          └────────────────┬──────────────────┘
                           │ 仍超长？
                           ▼
          ┌───────────────────────────────────┐
          │  阶梯 2: tryReactiveCompact()     │ (单回合仅限 1 次)
          │  触发响应式全文压缩与受控重启     │
          └────────────────┬──────────────────┘
                           │ 再次超长 或 hasAttemptedReactiveCompact？
                           ▼
          ┌───────────────────────────────────┐
          │  阶梯 3: Surface 错误             │
          │  ★ 强制 skipStopHooks = True ★    │ (切除死循环神经)
          └───────────────────────────────────┘
```

### ① 死亡螺旋防线（Breaking the Death Spiral）
在代理系统中，最隐蔽、破坏力最大的 Bug 就是**恢复逻辑与拦截逻辑咬合导致的死循环**：
$$\text{错误} \longrightarrow \text{Stop Hook 拦截阻塞} \longrightarrow \text{发起重试} \longrightarrow \text{再次错误} \longrightarrow \text{Stop Hook 再次拦截} \dots$$

Claude Code 源码中有极具工程警示意义的防线：**一旦 PTL 判定无法恢复，系统在 Surface 错误时坚决跳过 Stop Hooks！**  
因为在系统已经无法容纳上下文的生死关头，继续走形式化的流程或阻塞询问，只会让坏事以更加体面的姿态无限自我复制。

### ② 救火工具自身的逃生舱（`truncateHeadForPTLRetry`）
当系统调用 Compact 工具去做上下文压缩时，这个 Compact 请求本身的 Prompt 也有可能超长！  
如果救火工具自己也报了 `prompt_too_long`，Claude Code 启动终极逃生门：
- 在 `truncateHeadForPTLRetry()` 中，**成组地从头部丢弃最早期的 API 交互轮次（API rounds）**；
- 哪怕牺牲部分历史保真度，也优先让系统恢复呼吸，绝不允许卡在“连压缩都压不动”的绝对死锁中。

---

## 4. MOT（Max Output Tokens）续写延续论：拒绝废话

当输出达到上限被硬件截断时，许多 LLM 应用会打印一段客气的废话：“抱歉，我刚才被截断了，接下来我为您总结一下……”。
**在工业级代码重构场景下，这种行为不仅毫无价值，而且有害**：
1. 浪费宝贵的 Token 预算；
2. 导致代码生成出现前后格式不一致的语义漂移；
3. 模型不再聚焦于写代码，而开始沉迷于“回顾自己写代码”。

Claude Code 在 `src/query.ts:1185` 确立了纯粹的工程续写哲学：

| 恢复层级 | 策略动作 | 关键约束 |
|---|---|---|
| **第一层（低成本）** | 将 `maxOutputTokensOverride` 从保守 Cap 提升至 MAX，直接重发请求 | 不插入任何 Meta Message，不改变 Prompt，给模型把任务完整输出的机会 |
| **第二层（续写拼接）** | 若已达 MAX Cap，向上下文追加 Meta User Message，要求继续输出 | **严禁道歉、严禁 Recap！** 若中断发生在半句，直接从半句接着写；将剩余工作拆小 |

---

## 5. Abort（用户中断）的语义收尾与账本一致性

很多人把中断（`Ctrl+C` / `Esc`）看作纯前端交互，Claude Code 将其视作**需要严肃进行语义收尾的状态转移**：

1. **悬空工具账本闭合**：
   流式输出过程中，模型可能吐出了一半的 `tool_use`，用户突然按了中断。
   此时如果不处理，下一轮上下文里就会留下一个没有 `tool_result` 对应的非法工具调用，直接导致大模型 API 报错！
   系统必须立刻调用 `getRemainingResults()`，为悬空的 `tool_use` 生成 `synthetic tool_result`，确保工具调用账本完全闭合。
2. **拒绝误报成功**：
   如果 Compact 或子任务在执行过程中被用户 Esc 中断，必须严密捕获 `APIUserAbortError`，严禁将其误判为成功，严禁更新 Compact 状态。

---

## 6. 执行叙事一致性（Narrative Consistency）

错误恢复最终保护的，是**系统的执行叙事一致性**：
- 系统必须永远能讲清楚：刚才试图做什么？为什么没做成？触发了哪一条分层恢复路径？现在的状态是继续、等待还是换轨？
- 一旦错误恢复变成了无序的 `try...except pass`，工程对象就会迅速退化为玄学对象。

### 核心不变式（Runtime Invariants）
```python
assert withheld_error in {"prompt_too_long", "media_size", "max_output_tokens"}
assert has_attempted_reactive_compact => skip_further_reactive_compact
assert consecutive_failures < MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES
assert compact_aborted_by_user != summary_success
assert every_withheld_error_surfaces_iff_recovery_exhausted
```
