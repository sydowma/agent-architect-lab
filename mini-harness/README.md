# 🛠️ mini-harness: 轻量级工业级终端智能体

`mini-harness` 是伴随本学习计划迭代演进的自主研发核心代码工程。它将逐步从一个零依赖的 ReAct 玩具脚本，蜕变为具备工业级防御能力（长会话压缩、权限确认、崩溃自愈）的命令行编程智能体。

---

## 🏗️ 架构演进规划

### v0.1: 基础 ReAct 循环 (Week 1 交付)
- [ ] 零三方框架依赖，纯原生 API 通信
- [ ] 核心 Loop：`Prompt -> Model -> Tool Call -> Execute -> Observation -> Next Step`
- [ ] 基础工具集：
  - `read_file(path)`
  - `write_file(path, content)`
  - `run_bash(command)`
- [ ] 运行步数保护机制（防死循环）

### v0.2: 工业级防御加固 (Week 3 交付)
- [ ] **人在回路（HITL Interrupt）**：危险命令（如 `rm`、`git push`、修改系统配置）主动向用户发起 `[y/N]` 终端审批
- [ ] **上下文压紧（Context Compaction）**：当累计 Token 超过阈值（如 70%）时，自动执行首尾保护与中间过程调用微损折叠
- [ ] **工具报错自愈**：命令执行异常（如缺少依赖、文件不存在）自动捕获并交由模型分析修正

### v1.0: 生产级完备态 (Week 4 交付)
- [ ] **Skills 动态扩展**：支持从 `.agent/skills/` 目录下按需发现和加载自定义说明与工具
- [ ] **会话持久化与恢复**：支持会话中断后断点重续
- [ ] **单元测试与全真用例验证**
