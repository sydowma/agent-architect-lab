# 🛡️ Aegis-Sandbox: Production-Ready Computer Use Execution Engine

> **定位**：面向 AI Agent / Computer Use 场景的高性能、高安全隔离执行引擎与运行时调度系统。

---

## 架构特性

1. **执行治理与安全红线 (`src/executor.py`)**：
   - 零信任安全策略：`network=none`、`memory=256MB`、`pids=64` 硬隔离；
   - 双密钥特权隔离代理（Dual-Key Privilege Broker）：宿主 Master Key 绝不入沙箱，仅下发会话级受限 Token；
   - 双驱动自适应：支持本地无特权仿真沙箱（Hermetic In-Process）与生产级 Docker/Podman 容器驱动。

2. **OverlayFS 预热池与 COW 秒级复位 (`src/pool.py`)**：
   - 不可变 Gold Image 共享，基于 OverlayFS 差量层动态挂载；
   - Standby Pool 维持，借出延迟 **< 50ms**；
   - Action Diff 文件变更审计与 UpperDir 秒级快速重置；
   - Cgroups v2 严格配额（`pids.max` 防 Fork 炸弹，`memory.oom.group=1` 组级联杀）。

3. **零拷贝视觉与内核事件注入 (`src/display.py`)**：
   - X11 MIT-SHM 共享内存映射直接读取指针，截屏延迟 **< 8ms**；
   - Linux `/dev/uinput` 硬件级虚拟输入设备，支持修饰键组合与 `EV_SYN` 事务保障；
   - 坐标归一化、1000 网格映射与三次贝塞尔平滑鼠标轨迹生成（抗反爬/拟人化）。

---

## 目录结构

```text
aegis-sandbox/
├── README.md
├── demo.py                   # 🚀 架构状态机闭环仿真演示脚本
├── real_macos_computer_use.py# 🍏 真实 macOS 物理级原生应用 Computer Use 脚本
├── artifacts/                # 真实截屏与执行产物留存
│   ├── real_calc_before.png
│   └── real_calc_after.png
├── src/
│   ├── __init__.py
│   ├── executor.py           # 隔离沙箱与进程治理引擎
│   ├── pool.py               # OverlayFS 预热池与生命周期管理器
│   └── display.py            # MIT-SHM 零拷贝截屏与 /dev/uinput 输入注入
└── tests/
    ├── __init__.py
    ├── test_executor.py      # 执行器与安全红线测试
    ├── test_pool.py          # 预热池与 COW 隔离测试
    └── test_display.py       # 坐标变换与屏幕捕获测试
```

## 运行体验

### 1. 运行真实 macOS 物理级 Computer Use（肉眼可见操控真机）
```bash
python3 aegis-sandbox/real_macos_computer_use.py
```
> **真实效果**：在你的 Mac 屏幕上前台唤起系统自带“计算器”，通过 CoreGraphics 精准捕获窗口物理 ID 截取操作前纯净图像，模拟人类打字节奏注入物理按键（如 `999*888=`），截取操作后新画面比对视觉差分，并从 macOS UI 无障碍树中读取 `887,112` 断言验证！

### 2. 运行云端沙箱架构状态机 Demo
```bash
python3 aegis-sandbox/demo.py
```

### 3. 运行 Aegis-Sandbox 专属测试套件
```bash
PYTHONPATH=aegis-sandbox/src python3 -m unittest discover -s aegis-sandbox/tests -p "test_*.py" -v
```

