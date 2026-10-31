# -*- coding: utf-8 -*-
"""
Phase 2 `/api/v1/*` Pydantic v2 请求与响应契约定义。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.domain.enums import (
    AgentReportedStatus,
    ArtifactType,
    ConnectionStatus,
    ControlLevel,
    DecisionResolvedBy,
    DecisionRiskLevel,
    DecisionTimeoutPolicy,
    DecisionType,
    DependencyKind,
    LinkStatus,
    MachineConnectionKind,
    MachineOS,
    ProjectStatus,
    RemoteEntryKind,
    RuntimeHealthStatus,
    RuntimeProviderKey,
    SessionPlatformStatus,
    SessionSourceChannel,
    StepPlatformStatus,
    TaskPlatformStatus,
    TaskPriority,
)
from backend.domain.types import (
    CapabilityManifestItem,
    DecisionOptionItem,
    ModelEndpointDescriptor,
)


# -------------------- Project & MCP Token --------------------

class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=160, description="项目显示名称")
    root_path: str = Field(..., min_length=1, description="本地工作区绝对目录路径")
    slug: Optional[str] = Field(default=None, max_length=80, description="可选项目唯一短代号")
    description: str = Field(default="", max_length=4000, description="项目背景与目标说明")
    git_remote: Optional[str] = Field(default=None, max_length=500, description="Git 远端地址")
    summary_next_action: str = Field(default="", max_length=1000, description="下一步关键动作摘要")


class ProjectUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: Optional[int] = Field(default=None, ge=1, description="乐观锁期望版本号")
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    description: Optional[str] = Field(default=None, max_length=4000)
    status: Optional[ProjectStatus] = Field(default=None)
    summary_next_action: Optional[str] = Field(default=None, max_length=1000)


class McpTokenIssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token_name: str = Field(default="local-agent-mcp", min_length=1, max_length=120)
    scopes: list[str] = Field(
        default_factory=lambda: [
            "project:read",
            "task:read",
            "progress:write",
            "checkpoint:request",
            "decision:read",
            "artifact:write",
        ]
    )
    expires_at: Optional[str] = Field(default=None)


# -------------------- Task, Step, Dependency & Revision --------------------

class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=240, description="任务标题")
    goal: str = Field(default="", max_length=8000, description="任务目标详情")
    acceptance_criteria: list[str] = Field(default_factory=list, description="验收标准列表")
    parent_task_id: Optional[str] = Field(default=None, description="同项目父任务 ID")
    priority: TaskPriority = Field(default=TaskPriority.MEDIUM, description="优先级")
    sort_order: int = Field(default=0, description="同级排序号")


class TaskReviseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, min_length=1, max_length=240)
    goal: Optional[str] = Field(default=None, max_length=8000)
    acceptance_criteria: Optional[list[str]] = Field(default=None)
    change_reason: str = Field(default="人工修订任务内容", max_length=1000)
    expected_version: Optional[int] = Field(default=None, ge=1)


class TaskStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform_status: Optional[TaskPlatformStatus] = Field(default=None)
    agent_reported_status: Optional[AgentReportedStatus] = Field(default=None)
    clear_revalidation: bool = Field(default=False)


class TaskDependencyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    depends_on_task_id: str = Field(..., min_length=1, description="前置任务 ID")
    dependency_kind: DependencyKind = Field(default=DependencyKind.FINISH_TO_START)


class StepCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=240, description="步骤标题")
    instruction: str = Field(default="", max_length=8000, description="步骤具体执行说明")
    seq_order: Optional[int] = Field(default=None, ge=1, description="步骤顺序号（留空自动追加到末尾）")


class StepReviseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, min_length=1, max_length=240)
    instruction: Optional[str] = Field(default=None, max_length=8000)
    change_reason: str = Field(default="人工修订步骤内容", max_length=1000)
    expected_version: Optional[int] = Field(default=None, ge=1)


class StepStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform_status: Optional[StepPlatformStatus] = Field(default=None)
    agent_reported_status: Optional[AgentReportedStatus] = Field(default=None)
    clear_revalidation: bool = Field(default=False)


# -------------------- Machine & RuntimeInstance --------------------

class MachineRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(..., min_length=1, max_length=120)
    os_type: MachineOS = Field(default=MachineOS.WINDOWS)
    hostname_alias: str = Field(default="", max_length=120)
    connection_kind: MachineConnectionKind = Field(default=MachineConnectionKind.LOCAL)
    health_status: RuntimeHealthStatus = Field(default=RuntimeHealthStatus.ONLINE)


class RuntimeInstanceRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    provider_key: RuntimeProviderKey
    machine_id: str = Field(default="local-machine", min_length=1)
    display_name: Optional[str] = Field(default=None, max_length=120)
    runtime_version: str = Field(default="unknown", max_length=64)
    connector_version: str = Field(default="none", max_length=64)
    control_level: ControlLevel = Field(default=ControlLevel.L0)
    health_status: RuntimeHealthStatus = Field(default=RuntimeHealthStatus.UNKNOWN)
    capabilities: Optional[list[CapabilityManifestItem]] = Field(default=None)
    model_endpoint_descriptor: Optional[ModelEndpointDescriptor] = Field(default=None)


# -------------------- Session & ExternalSessionRef --------------------

class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=240)
    task_id: Optional[str] = Field(default=None)
    runtime_instance_id: Optional[str] = Field(default=None)
    source_channel: SessionSourceChannel = Field(default=SessionSourceChannel.REMOTE_LINK)


class SessionStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform_status: Optional[SessionPlatformStatus] = Field(default=None)
    agent_reported_status: Optional[AgentReportedStatus] = Field(default=None)
    runtime_status: Optional[RuntimeHealthStatus] = Field(default=None)
    connection_status: Optional[ConnectionStatus] = Field(default=None)
    expected_status_seq: Optional[int] = Field(default=None, ge=1)


class ExternalSessionRefBindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_instance_id: str = Field(..., min_length=1)
    external_session_id: str = Field(default="", max_length=160)
    remote_entry_kind: RemoteEntryKind = Field(default=RemoteEntryKind.NONE)
    remote_entry_url: str = Field(default="", max_length=1000)
    link_status: LinkStatus = Field(default=LinkStatus.ACTIVE)


# -------------------- Execution & Attempt --------------------

class ExecutionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    task_id: str = Field(..., min_length=1)
    step_id: Optional[str] = Field(default=None)
    session_id: Optional[str] = Field(default=None)
    runtime_instance_id: Optional[str] = Field(default=None)
    summary_note: str = Field(default="", max_length=2000)
    model_endpoint_descriptor: Optional[ModelEndpointDescriptor] = Field(default=None)


class ExecutionAttemptStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    runtime_instance_id: Optional[str] = Field(default=None)
    model_endpoint_descriptor: Optional[ModelEndpointDescriptor] = Field(default=None)


# -------------------- Interactive Decision --------------------

class DecisionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=240)
    context_summary: str = Field(default="", max_length=4000)
    decision_type: DecisionType = Field(default=DecisionType.INTERACTIVE_QUESTION)
    risk_level: DecisionRiskLevel = Field(default=DecisionRiskLevel.NORMAL)
    options: list[DecisionOptionItem] = Field(default_factory=list)
    recommended_option_id: Optional[str] = Field(default=None, max_length=64)
    allow_custom_input: bool = Field(default=True)
    timeout_policy: DecisionTimeoutPolicy = Field(default=DecisionTimeoutPolicy.AUTO_SUBMIT_RECOMMENDED)
    timeout_sec: int = Field(default=45, ge=0, le=86400)
    task_id: Optional[str] = Field(default=None)
    step_id: Optional[str] = Field(default=None)
    session_id: Optional[str] = Field(default=None)
    execution_id: Optional[str] = Field(default=None)


class DecisionResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chosen_option_id: Optional[str] = Field(default=None, max_length=64)
    custom_note: str = Field(default="", max_length=4000)
    resolved_by: DecisionResolvedBy = Field(default=DecisionResolvedBy.USER_MANUAL)
    idempotency_key: Optional[str] = Field(default=None, max_length=120)


# -------------------- Artifact --------------------

class ArtifactCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=240)
    artifact_type: ArtifactType = Field(default=ArtifactType.MARKDOWN_NOTE)
    summary_content: str = Field(default="", max_length=32000)
    relative_path: str = Field(default="", max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)
    task_id: Optional[str] = Field(default=None)
    session_id: Optional[str] = Field(default=None)
    execution_id: Optional[str] = Field(default=None)


# -------------------- Auth & Push --------------------

class PairExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pair_code: str = Field(..., min_length=1, max_length=120)
    device_name: str = Field(default="Mobile Browser", max_length=120)


class PushSubscribeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(default="default", max_length=64)
    subscription: dict[str, Any]
    user_agent: str = Field(default="", max_length=240)

