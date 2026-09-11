# Day 20: 多 Agent 协同、独立验证与团队工程落地 (Multi-Agent, Verification & Team Practices)

> **学习模块**：Week 3 工业级蜕变 —— 驾驭工程与系统防御 (Claude Code Harness Engineering)  
> **核心参考**：`harness-books/book1-claude-code` 第 7 章（多代理与验证）与 第 8 章（团队落地）  
> **源码对应**：[day20_multi_agent_coordinator.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/day20_multi_agent_coordinator.py) | [day20_test.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/day20_test.py)  

---

## 1. 核心哲学：多代理的本质是“给不确定性分区”

很多初学者系统一谈多 Agent，就兴奋地设计“CEO Agent 调度 Product Agent，再调度 Developer Agent”——这种拟人化狂欢在真实编码场景中往往是一场灾难：
- 不加约束的并发，只是把单 Agent 的混乱复制了几份；
- 多个 Agent 在没有上下文边界的情况下各说各话、互相覆盖文件；
- 最终生成的代码漏洞百出，谁也不对结果负责。

Claude Code 在第 7 章点破了多代理的本质：
> **多代理依赖清晰分工：研究、实现、验证和综合各自处在不同约束容器里，最后由协调者把结果重新缝合成可交付结果。**  
> **多代理真正解决的，不是并发提速，而是给不确定性分区（Partitioning of Uncertainty）。**

```text
       ┌────────────────────────────────────────────────────────┐
       │             四阶段受管不确定性分区流转                 │
       │                                                        │
       │   [用户高阶目标]                                       │
       │         │                                              │
       │         ▼                                              │
       │  ┌───────────────┐     Fork (Cache-Safe & 状态隔离)    │
       │  │ Research      ├───────────────────────────────┐     │
       │  │ (探索代码/搜寻)│                               │     │
       │  └───────┬───────┘                               ▼     │
       │          │ 返回事实线索                ┌──────────────────┐
       │          ▼                             │ Coordinator      │
       │  ┌───────────────┐ 协调者亲自消化       │ (协调者)         │
       │  │ Synthesis     ├────────────────────>│ ★ Always         │
       │  │ (强制综合收束) │ 拒绝二道贩子转发    │   Synthesize ★   │
       │  └───────┬───────┘ 产出精确坐标与方案  └─────────┬────────┘
       │          │                                       │     │
       │          ▼                                       │     │
       │  ┌───────────────┐ 专注按图索骥修改文件          │     │
       │  │ Implementation│<──────────────────────────────┤     │
       │  │ (实现 Worker) ├─────────────────────────┐     │     │
       │  └───────────────┘ 改完自述完成            │     │     │
       │                                            ▼     │     │
       │                    ┌─────────────────────────┐   │     │
       │                    │ Independent Verification│<──┘     │
       │                    │ (独立怀疑验证 Worker)   │         │
       │                    │ ★ 物理跑单测，严禁放水 ★│         │
       │                    └─────────────────────────┘         │
       └────────────────────────────────────────────────────────┘
```

---

## 2. Cache-Safe Forking：多代理首先是运行时经济学

在 `src/utils/forkedAgent.ts` 中，Claude Code 将 Fork 一个子代理的第一原则定义为：**确保 Prompt Cache Hit**。

### ① CacheSafeParams 对齐
一个子代理如果每次都把父代理的上下文重新烧一遍 Token，看上去是并行分工，实际只是把算力浪费并行化。子代理必须与父代理严格对齐 `CacheSafeParams`：
- `systemPrompt`
- `userContext`
- `systemContext`
- `toolUseContext`
- `forkContextMessages`

严禁在 Fork 时随意篡改 `maxOutputTokens` 或 Prompt 前缀，因为它们是 Prefix Cache Key 的核心组成部分。

### ② 状态隔离的默认伦理（Default Isolation）
在 `createSubagentContext()` 中，Claude Code 践行严苛的数据库事务级隔离哲学：
```python
readFileState = clone(parent.readFileState)      # 默认深拷贝，子读写不污染父
abortController = ChildAbortController(parent)   # 子控制器挂接父控制器（父死子亡）
getAppState = wrap(parent.getAppState)           # 避免弹出权限干扰
setAppState = noop                               # 默认只读，禁止写回父状态
```
**隔离才是默认伦理，共享必须显式 Opt-In！**  
子代理最宝贵的地方，在于它的局部探索哪怕走入歧途、读错文件、产生幻觉，都**绝不会回流污染主线程的工作内存**。

---

## 3. 协调者法则：综合理解绝不可外包（Always Synthesize）

在 `src/coordinator/coordinatorMode.ts` 中，有一条被加粗的工程铁律：**Always Synthesize**。

- **常见的反面教材（二道贩子）**：  
  Research Worker 返回了一大堆搜索结果，协调者直接对 Implementation Worker 说：“根据 Research 找到的结果，请去修改一下代码”。  
  这是严重的渎职！因为这等于把最关键的“理解与决策”推卸给了下游。
- **Claude Code 的强制综合要求**：  
  协调者收到研究线索后，**必须自己先读懂，提炼出关键逻辑，并在发送给下一级 Worker 的 Prompt 里写出具体的文件名、具体的函数名、具体的行号和变更内容**。  
  > **研究可以分布式，但决策与理解必须收束在中心。**

---

## 4. 独立验证关：实现与验证的角色强解耦

大模型有一个致命的劣根性：**它极度擅长在代码改动和逻辑正确之间“搭纸桥”**。  
如果让负责写代码的 Worker 顺便自测，它往往会输出一段看似天衣无缝的解释，甚至附带自造的 Fake 成功输出。

Claude Code 确立了严格的不变式：
```python
assert verification_worker != implementation_worker  # 验证与实现角色物理隔离
```
1. **实现 Worker（Implementation）**：专注于快速根据方案修改代码；
2. **验证 Worker（Verification）**：天然带着“怀疑者（Skeptical QA）”的立场。
   - 不看实现的自我夸赞；
   - 独立调用环境单测（如 `pytest` / `npm test`）；
   - 验证功能是否真的在物理系统里站住，杜绝“会改代码”冒充“解决问题”。

---

## 5. 子代理生命周期与孤儿防线（No-Orphan Guarantee）

子代理不是发出去就不管的异步线程，它在系统中是会持有文件句柄、消耗内存、占用网络端口的实体。

```text
               ┌─────────────────────────────────────┐
               │    Parent Agent (父级生命周期)       │
               └──────────────────┬──────────────────┘
                                  │
                  spawn()         │  Abort Signal (Esc / Ctrl+C)
         ┌────────────────────────┼────────────────────────┐
         │                        │                        │
         ▼                        ▼                        ▼
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│ SubagentStart    │    │ 级联中断传递     │    │ SubagentStop     │
│ Hook 记录 PID/ID │    │ parent.abort     │    │ 清理句柄/回灌转录│
└──────────────────┘    │  => child.abort  │    └──────────────────┘
                        └──────────────────┘
```

### 核心运行时不变式
1. **父死子亡（Parent Abort Cascade）**：
   `assert parent.abort => propagate(child.abort)`。父进程一旦被用户中断，所有后台飞行的子代理必须在当前滴答周期内全部杀死，禁止留下孤儿进程继续消耗 Token。
2. **生命周期闭合（Lifecycle Closure）**：
   `assert SubagentStart fired => SubagentStop fired eventually`。每一个启动的子代理，无论正常退出、报错崩溃还是被强制 Abort，都必须触发 `SubagentStop` Hook，完成输出清理与回调注销。

---

## 6. 团队工程落地：从个人技巧到制度边界（Chapter 8）

把一个 Agent 工具推广给整个技术团队，最容易犯的错误是“靠高手个人盯防”或“一上来就搞全链路审计”。

### ① 团队起步的最低四边界
1. **允许范围**：明确哪些任务允许 Agent 参与（如编写单测、重构小模块），哪些严禁（如核心鉴权协议修改）；
2. **Review 责任**：改动必须有人类 Engineer 承担最终责任，模型不是责任主体；
3. **验证口径**：改完至少跑什么检查（Lint, TypeCheck, Unit Tests），定义统一的“完成标准”；
4. **禁区清单**：仓库级明确禁止修改的目录（如 `.github/workflows`）和危险命令。

### ② 按后果分级的三态审批矩阵
团队审批绝不能按工具名字一刀切，而必须**按操作后果的不可逆性分级**：

| 风险等级 | 操作性质 | 示例工具/命令 | 审批裁决 |
|---|---|---|---|
| **Tier 1: READ** | 只读分析，无外部状态副作用 | `read_file`, `grep_search`, `list_dir` | **ALLOW**（自动放行） |
| **Tier 2: WRITE** | 修改工作区，可借助 Git 回滚 | `edit_file`, `write_file`, `pytest` | **ASK**（会话级/轮次级确认） |
| **Tier 3: IRREVERSIBLE** | 不可逆破坏或跨越安全边界 | `git push -f`, `rm -rf`, 访问生产库 | **OPERATOR_ASK / DENY**（严格人工放行） |

---

## 7. 核心架构总结

> **多代理不是人格表演，多代理是给不确定性分区。**  
> 
> 1. **Fork 先保 Cache**：`CacheSafeParams` 严格同构，避免浪费并行化；
> 2. **隔离作为默认伦理**：可变状态单向隔离，禁止局部混乱污染全局；
> 3. **协调者负责消化**：Always Synthesize，拒绝二道贩子无脑转发；
> 4. **验证与实现解耦**：改代码者不自测，独立怀疑者物理跑测试；
> 5. **生命周期严格闭合**：父死子亡无孤儿，Start 与 Stop 终态对应；
> 6. **团队落地先定边界**：统一验证口径先于扩充 Skill，按后果三级分层审批。
