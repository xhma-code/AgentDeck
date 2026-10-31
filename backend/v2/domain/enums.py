# -*- coding: utf-8 -*-
"""
领域核心状态字典与枚举定义（严格对齐架构路线图 v1.3 第 5、10、11 节）。

核心原则：
1. 严格分离平台管理状态 (platform_status)、Agent 自报状态 (agent_reported_status)、
   Runtime 运行状态 (runtime_status) 与远程连接状态 (connection_status)。
2. Remote 断开不代表 Agent 执行失败；Agent 自报完成不代表平台验收完成。
3. 未验证或未知能力一律显式标记为 unknown / unverified，绝不伪造。
"""

from __future__ import annotations

from enum import StrEnum


class ProjectStatus(StrEnum):
    """项目生命周期状态。"""

    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class TaskPlatformStatus(StrEnum):
    """平台对任务的管理状态。"""

    DRAFT = "draft"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    WAITING_DECISION = "waiting_decision"
    NEEDS_REVALIDATION = "needs_revalidation"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class StepPlatformStatus(StrEnum):
    """平台对任务步骤的管理状态。"""

    PENDING = "pending"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    NEEDS_REVALIDATION = "needs_revalidation"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class AgentReportedStatus(StrEnum):
    """外部 Agent 主动汇报的状态（独立于平台验收状态）。"""

    UNKNOWN = "unknown"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    COMPLETED = "completed"
    FAILED = "failed"


class SessionPlatformStatus(StrEnum):
    """平台对协同会话的管理状态。"""

    ACTIVE = "active"
    IDLE = "idle"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class RuntimeHealthStatus(StrEnum):
    """Runtime 实例或宿主机器的运行健康状态。"""

    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class ConnectionStatus(StrEnum):
    """Remote / Server 连接状态（断开不等于执行失败）。"""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    UNKNOWN = "unknown"


class LinkStatus(StrEnum):
    """外部会话引用链接状态。"""

    ACTIVE = "active"
    STALE = "stale"
    DISCONNECTED = "disconnected"
    UNKNOWN = "unknown"


class ControlLevel(StrEnum):
    """统一控制能力等级（L0-L3）。"""

    L0 = "L0"  # Remote Link：登记 Runtime/Machine/Session，打开官方 Remote
    L1 = "L1"  # Registry & Status：心跳、健康状态、最近活动、MCP 主动汇报
    L2 = "L2"  # Programmatic Control：官方接口创建/继续 Session、发送任务、读取事件、取消
    L3 = "L3"  # Approval & Recovery：官方接口支持的审批、检查点、恢复和状态协调


class SupportStatus(StrEnum):
    """单项能力支持度声明状态。"""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNVERIFIED = "unverified"


class EvidenceSource(StrEnum):
    """能力声明证据来源。"""

    OFFICIAL_DOCS = "official_docs"
    INTEGRATION_TEST = "integration_test"
    INFERRED = "inferred"


class RuntimeProviderKey(StrEnum):
    """受支持的 Agent Runtime 产品标识（非底层大模型服务商）。"""

    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    OPENCODE = "opencode"
    ANTIGRAVITY = "antigravity"


class IntegrationType(StrEnum):
    """Runtime 默认集成通道类型。"""

    REMOTE_ONLY = "remote_only"
    MCP_CLIENT = "mcp_client"
    HTTP_API = "http_api"
    SDK = "sdk"
    CLI = "cli"


class MachineOS(StrEnum):
    """宿主机器操作系统类型。"""

    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"


class MachineConnectionKind(StrEnum):
    """宿主机器连接方式。"""

    LOCAL = "local"
    TAILSCALE = "tailscale"
    LAN = "lan"
    VENDOR_REMOTE = "vendor_remote"
    OTHER = "other"


class SessionSourceChannel(StrEnum):
    """Session 接入来源通道。"""

    REMOTE_LINK = "remote_link"
    MCP = "mcp"
    LOCAL_RUNTIME = "local_runtime"
    HOSTED = "hosted"


class RemoteEntryKind(StrEnum):
    """官方远程入口类型。"""

    NONE = "none"
    CLAUDE_REMOTE_CONTROL = "claude_remote_control"
    CHATGPT_REMOTE = "chatgpt_remote"
    OPENCODE_WEB = "opencode_web"
    ANTIGRAVITY_REMOTE = "antigravity_remote"
    CUSTOM_SAFE_LINK = "custom_safe_link"


class TaskPriority(StrEnum):
    """任务优先级。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DependencyKind(StrEnum):
    """轻量任务前置依赖类型。"""

    FINISH_TO_START = "finish_to_start"


class ActorType(StrEnum):
    """操作或事件触发主体类型。"""

    USER = "user"
    MCP_AGENT = "mcp_agent"
    RUNTIME = "runtime"
    SYSTEM = "system"


class EndpointKind(StrEnum):
    """非敏感模型端点运行环境分类。"""

    OFFICIAL = "official"
    COMPATIBLE_API = "compatible_api"
    LOCAL_GATEWAY = "local_gateway"
    UNKNOWN = "unknown"


class EndpointVerifiedStatus(StrEnum):
    """模型端点与 Runtime 组合验证状态。"""

    UNVERIFIED = "unverified"
    VERIFIED_FOR_RUNTIME = "verified_for_runtime"

