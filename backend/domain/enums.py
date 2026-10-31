# -*- coding: utf-8 -*-
"""
领域核心状态字典与枚举定义（严格对齐架构路线图 v1.3 与交互式 Decision 规范）。

核心原则：
1. 严格分离平台管理状态 (platform_status)、Agent 自报状态 (agent_reported_status)、
   Runtime 运行状态 (runtime_status) 与远程连接状态 (connection_status)。
2. 正式支持 waiting_interaction 状态，消除遇到交互式问答或决策门禁时的假 running 卡死。
3. Remote 断开不代表 Agent 执行失败；Agent 自报完成不代表平台验收完成。
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
    WAITING_INTERACTION = "waiting_interaction"
    WAITING_DECISION = "waiting_decision"
    BLOCKED = "blocked"
    NEEDS_REVALIDATION = "needs_revalidation"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class StepPlatformStatus(StrEnum):
    """平台对任务步骤的管理状态。"""

    PENDING = "pending"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    WAITING_INTERACTION = "waiting_interaction"
    BLOCKED = "blocked"
    NEEDS_REVALIDATION = "needs_revalidation"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class AgentReportedStatus(StrEnum):
    """外部 Agent 主动汇报的状态（独立于平台验收状态）。"""

    UNKNOWN = "unknown"
    RUNNING = "running"
    WAITING_INTERACTION = "waiting_interaction"
    WAITING_INPUT = "waiting_input"
    COMPLETED = "completed"
    FAILED = "failed"


class SessionPlatformStatus(StrEnum):
    """平台对协同会话的管理状态。"""

    ACTIVE = "active"
    WAITING_INTERACTION = "waiting_interaction"
    IDLE = "idle"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class ExecutionPlatformStatus(StrEnum):
    """平台对 Execution 执行单的管理状态。"""

    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INTERACTION = "waiting_interaction"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_REVALIDATION = "needs_revalidation"


class AttemptStatus(StrEnum):
    """单次 ExecutionAttempt 尝试状态。"""

    RUNNING = "running"
    WAITING_INTERACTION = "waiting_interaction"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class DecisionType(StrEnum):
    """交互式决策分类。"""

    INTERACTIVE_QUESTION = "interactive_question"
    PLAN_CONFIRM = "plan_confirm"
    SCOPE_CHANGE = "scope_change"
    RISK_GATE = "risk_gate"
    DIRECTION_CHOICE = "direction_choice"


class DecisionRiskLevel(StrEnum):
    """决策风险等级（高风险禁止默认超时自动提交）。"""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class DecisionTimeoutPolicy(StrEnum):
    """交互式决策超时策略。"""

    AUTO_SUBMIT_RECOMMENDED = "auto_submit_recommended"
    MANUAL_ONLY = "manual_only"
    EXPIRE_ON_TIMEOUT = "expire_on_timeout"


class DecisionStatus(StrEnum):
    """交互式决策生命周期状态。"""

    PENDING = "pending"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class DecisionResolvedBy(StrEnum):
    """决策处理来源标识。"""

    USER_MANUAL = "user_manual"
    TIMEOUT_AUTO_RECOMMENDED = "timeout_auto_recommended"
    SYSTEM = "system"


class ArtifactType(StrEnum):
    """交付产物类型。"""

    PLAN_DOC = "plan_doc"
    GIT_DIFF_SUMMARY = "git_diff_summary"
    TEST_REPORT = "test_report"
    MARKDOWN_NOTE = "markdown_note"
    FILE_REF = "file_ref"
    EXTERNAL_LINK = "external_link"


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

    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


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

