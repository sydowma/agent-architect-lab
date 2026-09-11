# Week 3 阶段性架构综述报告：工业级工程防御与车身架构演进

> **实验室周期**：Week 3 (Day 15 - Day 21)  
> **核心文本**：[harness-books Book 1 (Claude Code 架构解剖)](https://github.com/wquguru/harness-books/tree/main/book1-claude-code)  
> **核心产出**：`mini-harness v0.2` 工业级集成防御智能体 + 《Harness Engineering 十条黄金法则》  
> **累计工时**：**Phase 3 累计 20.0h / 24.0h** | **实验室总计 49.5h / 90.0h (55.0% 全程里程碑突破！)**  

---

## 1. 认知蜕变轨迹：从玩具到重工业

回顾 Agent Architect Lab 的完整技术进阶史：

| 阶段 | 核心任务 | 典型产物 | 核心认知局限 |
|---|---|---|---|
| **Phase 1 (Week 1)** | 原生机制手搓 | `mini-harness v0.1` (ReAct 循环) | 认为只要有了 Thought-Action-Observation 循环，Agent 就能工作。 |
| **Phase 2 (Week 2)** | 架构范式较量 | 横向基准评测 (Direct vs ReAct vs Reflection vs PEV) | 发现盲目反思会陷入 9.8KB 过度纠偏陷阱，确定了 PEV 是最稳健的推理骨架。 |
| **Phase 3 (Week 3)** | **工业级工程防御** | **`mini-harness v0.2` 工业车身** | **彻底摒弃“模型是同事”的拟人幻想，确立了“围绕不可靠引擎打造绝对可靠的 Harness 车身”的工业工程观！** |

---

## 2. Phase 3 逐日攻坚全景复盘

### Day 15: 控制面宪法与 Prompt 分层 (Ch 1 & 2)
- **破除迷思**：Prompt 不是拟人设定，Prompt 是系统的控制总线。
- **关键突破**：实现了 5 级优先级覆盖链条（`override > coord > agent > custom > default + append`）；划分静态不变宪法与动态尾部 `<system-reminder>`，保全 90% 成本的 KV Prefix Cache。

### Day 16: 心跳状态机与账本一致性 (Ch 3)
- **破除迷思**：单次请求不叫 Agent，持续受控的心跳执行循环才是运行时。
- **关键突破**：实现 `QueryLoopState` 单调递增轮次；打造**悬空工具调用账本平衡器**（无论正常执行还是异常中断，每一个 `tool_use` 必须严格闭合 `tool_result`，杜绝 API 400 崩溃）；落地轻量微压缩（Microcompact）。

### Day 17: 受管工具编排与权限沙箱 (Ch 4)
- **破除迷思**：会写代码不等于允许直接碰世界。
- **关键突破**：打造 `ToolOrchestrator`（只读操作并发吞吐、写操作强制单步串行）；实现因果顺序回放（`contextModifier Replay`）；确立严谨的三态权限判定（ALLOW / ASK / DENY）；部署 `SafeBashGuard` 高危 Shell 命令与复合命令深度拦截。

### Day 18: 上下文压紧与受控重启 (Ch 5)
- **破除迷思**：“信息越多越聪明”是低级神话；上下文是工作内存预算。
- **关键突破**：解耦长期制度（`CLAUDE.md`）、持久索引（`MEMORY.md` 200行/25KB物理截断）与会话说明书（`SessionMemory`）；实现 AutoCompact 预算缓冲计算（20,000预留 + 13,000缓冲）；实现**受控重启（Controlled Reboot）**，剥离临时噪点，重建活跃文件与计划，写入 `CompactBoundary`。

### Day 19: 错误自愈与死锁防线 (Ch 6)
- **破除迷思**：工程世界最不可信的就是“正常情况下”；错误属于主路径。
- **关键突破**：建立 `withheld` 可恢复错误暂存白名单；实现 PTL 分层降级自愈（Collapse Drain -> Compact -> Surface）；**PTL 最终失败坚决跳过 Stop Hooks 切断死亡螺旋**；MOT 续写延续论（无道歉、无 Recap）；部署救火逃生舱（`truncateHeadForPTLRetry`）。

### Day 20: 多 Agent 分区与独立怀疑验证 (Ch 7 & 8)
- **破除迷思**：多代理不是人格表演，多代理是给不确定性分区。
- **关键突破**：实现 Cache-Safe Forking（对齐 `CacheSafeParams`）；默认深拷贝隔离可变状态；协调者坚守强制综合律（**Always Synthesize**）；**实现 Worker 与独立验证 Worker 角色物理解耦**，由独立怀疑者物理跑单测验收；实施父死子亡（`parent.abort => child.abort`）与三级后果审批矩阵。

---

## 3. 《Harness Engineering 十条黄金法则》终局总结

1. **把模型当不稳定部件，不要当同事**
2. **Prompt 是控制面的一部分**
3. **Query loop 才是代理系统的心跳**
4. **工具是受管执行接口**
5. **上下文是工作内存预算**
6. **错误路径就是主路径**
7. **恢复的目标是继续工作**
8. **多代理的意义是把不确定性分区**
9. **验证必须独立，不能让系统自己给自己打分**
10. **团队制度比个人技巧重要**

> **收官结语**：
> **Harness 比激情重要，制度比聪明重要，验证比自信重要。**  
> Week 3 的全栈突破标志着我们不仅掌握了智能体的算法骨架，更铸造了工业级软件工程的钢铁车身！
