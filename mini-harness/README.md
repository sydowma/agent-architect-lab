# 🛠️ mini-harness: 轻量级工业级终端编程智能体

`mini-harness` 是伴随本实验室 4 周冲刺迭代自主研发的核心代码资产。它从零依赖的原生 ReAct 循环起步，历经反射与规划架构、Claude Code 弹性工程防御加固，最终在 **Week 4 完成工业级大一统重构，正式发布 `v1.0` 完备版**。

---

## 🚀 mini-harness v1.0 架构特性

```text
 ┌────────────────────────────────────────────────────────────────────────┐
 │                    mini-harness v1.0 统一工业架构                      │
 ├────────────────────────────────────────────────────────────────────────┤
 │ [1. 控制面 (Control Plane)]                                             │
 │   • PromptControlPlane: 静态前缀与动态尾部分离 (KV Cache 稳定)          │
 │   • PrefixCacheDisciplineValidator: 防时间戳漂移与缓存击穿 (TTFT ~25ms) │
 │   • AGENTS.md / CLAUDE.md: 目录级单调作用域继承 (project > user > sys)  │
 ├────────────────────────────────────────────────────────────────────────┤
 │ [2. 制度与治理层 (Governance & Rule Substrate)]                         │
 │   • SkillRegistry & SkillDirectoryLoader: 资产指纹防抖, 按需注入        │
 │   • HookEngine: session_start ➔ submit ➔ pre(block) ➔ post ➔ stop     │
 │   • DelegationEngine: 工具化多代理委派 (spawn/send/wait/close/cascade)  │
 │   • DelegationLedger: 不可篡改证据链, 供 Skeptical Verifier 审计       │
 ├────────────────────────────────────────────────────────────────────────┤
 │ [3. 运行时驱动与防御循环 (Runtime & Resilience Engine)]                 │
 │   • QueryLoopEngine: 心跳状态机, 用户打断 (Ctrl+C) 账本合成补账         │
 │   • SafeBashGuard: 阻断破坏性指令, 限制子命令链条                       │
 │   • ContextCompactor: 滑动窗口截断 + SessionMemory 语义锚点提炼         │
 │   • ErrorRecovery: 扣留错误 (Withheld Errors), PTL 分层恢复            │
 ├────────────────────────────────────────────────────────────────────────┤
 │ [4. 受管执行与持久化 (Execution & Session Portability)]                 │
 │   • SandboxedToolExecutor: 双密钥降权 + 纯出网反向 WSS 语义             │
 │   • 双驱动沙箱: LocalDockerSandbox (生产) + SimulatedIsolatedSandbox   │
 │   • SessionPortabilityEngine: Pi 风格 JSONL DAG 流 ↔ Codex 快照 ↔ 内存 │
 └────────────────────────────────────────────────────────────────────────┘
```

---

## 📦 演进里程碑 (Milestones)

- [x] **v0.1: 原生 ReAct 核心循环 (Week 1 交付)**
  - 原生 Tool Calling 消息协议调度
  - `Thought -> Action -> Observation` 循环
  - 基础文件与命令工具集
- [x] **v0.2: 工业级防御加固 (Week 3 交付)**
  - 三态权限模型（`ALLOW` / `ASK` / `DENY`）与 `SafeBashGuard`
  - 带语义锚点的长会话上下文压紧（Context Compaction）
  - 异常扣留（Withheld Errors）与死亡螺旋切断
  - 父死子亡级联中断（No-Orphan Invariant）
- [x] **v1.0: 生产级完备态 (Week 4 交付)**
  - **动态 Skills 资产管理**：支持从 `.agent/skills/` 自动发现并按指纹（Fingerprint）去重安装
  - **Hook 生命周期事件引擎**：阻断型 Pre-Tool 门禁与跨平台可解释探测
  - **双驱动隔离沙箱**：集成 `SimulatedIsolatedSandbox`（零依赖）与 `LocalDockerSandbox`（真实 cgroups 断网）
  - **双密钥降权**：宿主核心密钥零泄漏，仅下发会话作用域 `EXECUTOR_KEY`
  - **提示缓存（Prefix Caching）物理守卫**：拦截时间戳漂移，保障 GPU 推理首字延迟（TTFT）
  - **Pi 风格会话可移植性**：纯追加 JSONL 事件流与无副作用 DAG 分支

---

## 💻 快速开始 (Quickstart)

### 1. 启动终端控制台
```bash
# 启动零依赖沙箱模式 (开箱即用)
python3 mini-harness/src/cli.py

# 启动真实本地 Docker 隔离模式
python3 mini-harness/src/cli.py --docker

# 指定外部技能库目录
python3 mini-harness/src/cli.py --skills-dir .agent/skills
```

### 2. 交互控制指令 (Slash Commands)
在终端 REPL 中输入以下指令控制 Agent 运行时：
- `/skills`：查看已挂载的本地技能与 SHA256 指纹状态；
- `/hooks`：查看已注册的生命周期 Hook 与阻断规则；
- `/sandbox`：检查当前沙箱隔离策略（网络模式、内存配额 256MB、PIDs 配额 64）；
- `/ledger`：打印工具调度与多代理委派的双向审计账本；
- `/export`：将当前会话完整导出为 Pi 风格的纯追加 `.jsonl` 事件流文件；
- `/stats`：查看提示缓存命中率与耗时统计。

### 3. 运行自动化测试
```bash
# 运行 v1.0 端到端完备验收测试
python3 mini-harness/src/test_v10_release.py

# 运行各子系统单元测试
python3 mini-harness/src/day22_test.py  # 控制面与公文继承
python3 mini-harness/src/day23_test.py  # 安全沙箱与本地执行治理
python3 mini-harness/src/day24_test.py  # 技能挂载与工具化委派
python3 mini-harness/src/day25_test.py  # 前缀缓存与会话可移植性
```
