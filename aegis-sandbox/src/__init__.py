"""Aegis-Sandbox: Production-Ready Computer Use & Agent Execution Runtime.

Core Modules:
- executor: Sandboxed process execution, dual-key privilege broker, and security invariants.
- pool: High-frequency OverlayFS pre-warmed standby pool manager and action diff inspector.
- display: Zero-copy screen capture (MIT-SHM) and kernel-grade input injection (/dev/uinput).
- orchestrator: Event-sourced state machine & deterministic replay workflow engine (Temporal.io).
"""

from .display import (
    BaseInputDriver,
    BaseScreenCaptureDriver,
    CoordinateTransformer,
    DisplayGeometry,
    DisplayManager,
    FrameBufferData,
    HermeticMockDisplayDriver,
    HermeticMockInputDriver,
    InputEventType,
    KernelInputEvent,
    X11ShmCaptureDriver,
)
from .executor import (
    BaseExecutionSandbox,
    DualKeyPrivilegeBroker,
    ExecutionResult,
    LocalDockerSandbox,
    NetworkMode,
    OutboundReverseConnection,
    ProcessBackend,
    SandboxError,
    SandboxInvariantEnforcer,
    SandboxLifecycleManager,
    SandboxNotRunningError,
    SandboxOverheadProfiler,
    SandboxSecurityPolicy,
    SandboxSecurityViolation,
    SandboxState as ExecutorState,
    SandboxedToolExecutor,
    SimulatedIsolatedSandbox,
)
from .orchestrator import (
    ActivityDefinition,
    EventHistory,
    EventRecord,
    EventSourcedAgentWorkflowEngine,
    SignalChannel,
    SignalPayload,
    SignalType,
    WorkflowEventType,
    WorkflowStatus,
)
from .pool import (
    ActionDiff,
    BaseOverlayDriver,
    CapacityExhaustedError,
    CgroupsV2Quota,
    HermeticMockOverlayDriver,
    InvalidLeaseTokenError,
    LinuxNativeOverlayDriver,
    SandboxInstance,
    SandboxPoolError,
    SandboxState,
    StandbySandboxPoolManager,
)

__all__ = [
    # Executor
    "BaseExecutionSandbox",
    "DualKeyPrivilegeBroker",
    "ExecutionResult",
    "LocalDockerSandbox",
    "NetworkMode",
    "OutboundReverseConnection",
    "ProcessBackend",
    "SandboxError",
    "SandboxInvariantEnforcer",
    "SandboxLifecycleManager",
    "SandboxNotRunningError",
    "SandboxOverheadProfiler",
    "SandboxSecurityPolicy",
    "SandboxSecurityViolation",
    "ExecutorState",
    "SandboxedToolExecutor",
    "SimulatedIsolatedSandbox",
    # Pool
    "ActionDiff",
    "BaseOverlayDriver",
    "CapacityExhaustedError",
    "CgroupsV2Quota",
    "HermeticMockOverlayDriver",
    "InvalidLeaseTokenError",
    "LinuxNativeOverlayDriver",
    "SandboxInstance",
    "SandboxPoolError",
    "SandboxState",
    "StandbySandboxPoolManager",
    # Display
    "BaseInputDriver",
    "BaseScreenCaptureDriver",
    "CoordinateTransformer",
    "DisplayGeometry",
    "DisplayManager",
    "FrameBufferData",
    "HermeticMockDisplayDriver",
    "HermeticMockInputDriver",
    "InputEventType",
    "KernelInputEvent",
    "X11ShmCaptureDriver",
    # Orchestrator
    "ActivityDefinition",
    "EventHistory",
    "EventRecord",
    "EventSourcedAgentWorkflowEngine",
    "SignalChannel",
    "SignalPayload",
    "SignalType",
    "WorkflowEventType",
    "WorkflowStatus",
]
