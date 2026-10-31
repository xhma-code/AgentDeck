# -*- coding: utf-8 -*-
"""
MCP 8 大核心协作工具的入参 Pydantic v2 Schema、JSON Schema 导出与统一响应信封（backend/mcp/schemas.py）。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.domain.enums import (
    ArtifactType,
    DecisionRiskLevel,
    DecisionTimeoutPolicy,
    DecisionType,
)
from backend.domain.exceptions import (
    CrossProjectReferenceError,
    DomainError,
    EntityNotFoundError,
    InvariantViolationError,
    SecurityBoundaryError,
    VersionConflictError,
)
from backend.domain.types import DecisionOptionItem

MCP_SCHEMA_VERSION = "1.0"
MCP_PROTOCOL_VERSION_2026 = "2026-07-28"
MCP_PROTOCOL_VERSION_2025_11 = "2025-11-25"
MCP_PROTOCOL_VERSION_2025_03 = "2025-03-26"
SUPPORTED_MCP_PROTOCOL_VERSIONS: tuple[str, ...] = (
    MCP_PROTOCOL_VERSION_2026,
    MCP_PROTOCOL_VERSION_2025_11,
    MCP_PROTOCOL_VERSION_2025_03,
)

AgentReportableStatusLiteral = Literal[
    "in_progress",
    "blocked_by_decision",
    "completed",
    "failed",
]


class ProjectContextGetArgs(BaseModel):
    """工具 1: project_context_get 入参。"""

    model_config = ConfigDict(extra="ignore")

    project_id: Optional[str] = Field(
        default=None,
        description="可选项目 ID（若传入必须与当前 MCP Token 绑定的项目一致；省略则自动取 Token 绑定项目）",
    )
    include_completed_tasks: bool = Field(
        default=False,
        description="是否包含平台已验收完成的任务摘要（默认 false，仅返回活跃/待办上下文）",
    )
    max_tasks: int = Field(
        default=20,
        ge=1,
        le=50,
        description="返回任务摘要的最大条数（1..50）",
    )


class TaskGetArgs(BaseModel):
    """工具 2: task_get 入参（支持按 ID 查询或自动选取最高优先级可执行任务，并可选原子认领）。"""

    model_config = ConfigDict(extra="ignore")

    project_id: Optional[str] = Field(default=None, description="可选项目 ID（校验与 Token 一致）")
    task_id: Optional[str] = Field(
        default=None,
        description="指定任务 ID；若省略，自动选取当前项目下前置依赖已满足、优先级最高的待执行任务",
    )
    claim_next_ready: bool = Field(
        default=False,
        description="是否自动挑选并认领下一个 Ready 可执行任务",
    )
    session_id: Optional[str] = Field(
        default=None,
        description="可选：认领任务绑定的同项目 Session ID（与 claim_for_session_id 等价）",
    )
    claim_for_session_id: Optional[str] = Field(
        default=None,
        description="可选：将该任务原子认领并绑定至指定的同项目 Session ID（需具备 progress:write 权限）",
    )
    idempotency_key: Optional[str] = Field(
        default=None,
        max_length=100,
        description="当传入 claim_for_session_id 执行认领写操作时可选的幂等键",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_claim_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        if not out.get("claim_for_session_id") and out.get("session_id"):
            out["claim_for_session_id"] = out["session_id"]
        return out


class TaskRevisionGetArgs(BaseModel):
    """工具 3: task_revision_get 入参。"""

    model_config = ConfigDict(extra="ignore")

    project_id: Optional[str] = Field(default=None, description="可选项目 ID（校验与 Token 一致）")
    task_id: str = Field(..., min_length=1, max_length=64, description="目标任务 ID")
    revision_no: Optional[int] = Field(
        default=None,
        ge=1,
        description="指定修订版本号；省略则返回当前生效修订版及全部修订历史摘要",
    )


class TaskReportProgressArgs(BaseModel):
    """工具 4: task_report_progress 入参（只更新 Agent 自报状态，绝不自动完成平台验收态）。"""

    model_config = ConfigDict(extra="ignore")

    project_id: Optional[str] = Field(default=None, description="可选项目 ID（校验与 Token 一致）")
    task_id: str = Field(..., min_length=1, max_length=64, description="目标任务 ID")
    agent_reported_status: AgentReportableStatusLiteral = Field(
        ...,
        description="Agent 汇报的执行状态（in_progress / blocked_by_decision / completed / failed）",
    )
    summary: str = Field(
        default="Agent 进度更新",
        min_length=1,
        max_length=2000,
        description="本次进度汇报的结构化说明摘要",
    )
    percent_complete: Optional[int] = Field(
        default=None,
        ge=0,
        le=100,
        description="可选完成百分比估计（0..100）",
    )
    session_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description="可选关联协同会话 ID（传入时同步刷新会话心跳时间）",
    )
    idempotency_key: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="必填写操作幂等键",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_decision_type(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        dt = out.get("decision_type")
        alias_map = {
            "architecture_choice": DecisionType.DIRECTION_CHOICE.value,
            "checkpoint": DecisionType.PLAN_CONFIRM.value,
        }
        if isinstance(dt, str) and dt in alias_map:
            out["decision_type"] = alias_map[dt]
        return out

    @model_validator(mode="before")
    @classmethod
    def _normalize_progress_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        if "agent_reported_status" not in out and "reported_status" in out:
            out["agent_reported_status"] = out["reported_status"]
        if "percent_complete" not in out and "progress_percent" in out:
            out["percent_complete"] = out["progress_percent"]
        return out


class StepUpdateArgs(BaseModel):
    """工具 5: step_update 入参（强制校验 expected_revision_no 防止覆盖用户修改）。"""

    model_config = ConfigDict(extra="ignore")

    project_id: Optional[str] = Field(default=None, description="可选项目 ID（校验与 Token 一致）")
    task_id: Optional[str] = Field(default=None, description="可选所属任务 ID")
    step_id: str = Field(..., min_length=1, max_length=64, description="目标步骤 ID")
    expected_revision_no: int = Field(
        ...,
        ge=1,
        description="必填：Agent 读取该步骤时的修订号 active_revision_no（若用户已修订步骤则触发冲突拦截）",
    )
    expected_version: Optional[int] = Field(
        default=None,
        ge=1,
        description="可选乐观锁版本号 expected_version",
    )
    agent_reported_status: AgentReportableStatusLiteral = Field(
        ...,
        description="Agent 汇报的步骤状态（in_progress / blocked_by_decision / completed / failed）",
    )
    execution_note: str = Field(
        default="",
        max_length=2000,
        description="步骤执行备注说明",
    )
    session_id: Optional[str] = Field(default=None, max_length=64, description="可选关联会话 ID")
    idempotency_key: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="必填写操作幂等键",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_decision_type(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        dt = out.get("decision_type")
        alias_map = {
            "architecture_choice": DecisionType.DIRECTION_CHOICE.value,
            "checkpoint": DecisionType.PLAN_CONFIRM.value,
        }
        if isinstance(dt, str) and dt in alias_map:
            out["decision_type"] = alias_map[dt]
        return out

    @model_validator(mode="before")
    @classmethod
    def _normalize_step_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        if "agent_reported_status" not in out and "status" in out:
            out["agent_reported_status"] = out["status"]
        if "execution_note" not in out and "summary" in out:
            out["execution_note"] = out["summary"]
        return out


class CheckpointRequestArgs(BaseModel):
    """工具 6: checkpoint_request 入参（请求人工决策/风险门禁，高风险或 risk_gate 强制 manual_only）。"""

    model_config = ConfigDict(extra="ignore")

    project_id: Optional[str] = Field(default=None, description="可选项目 ID（校验与 Token 一致）")
    title: str = Field(..., min_length=1, max_length=240, description="决策标题")
    decision_type: DecisionType = Field(
        default=DecisionType.INTERACTIVE_QUESTION,
        description="决策类型（interactive_question / plan_confirm / scope_change / risk_gate / direction_choice）",
    )
    risk_level: DecisionRiskLevel = Field(
        default=DecisionRiskLevel.NORMAL,
        description="风险等级（low / normal / high / critical）",
    )
    options: list[DecisionOptionItem] = Field(
        default_factory=lambda: [
            DecisionOptionItem(option_id="confirm", label="确认通过", is_recommended=True),
            DecisionOptionItem(option_id="reject", label="驳回重做", is_recommended=False),
        ],
        min_length=1,
        description="候选决策选项列表（至少 1 项）",
    )
    context_summary: str = Field(default="", max_length=4000, description="决策背景与上下文摘要")
    recommended_option_id: Optional[str] = Field(default=None, max_length=64, description="推荐选项 ID")
    allow_custom_input: bool = Field(default=True, description="是否允许用户填写补充说明")
    timeout_policy: Optional[DecisionTimeoutPolicy] = Field(
        default=None,
        description="超时策略（risk_gate 或 high/critical 风险强制为 manual_only，禁止自动通过）",
    )
    timeout_sec: int = Field(default=45, ge=0, le=86400, description="倒计时秒数")
    task_id: Optional[str] = Field(default=None, max_length=64, description="关联任务 ID")
    step_id: Optional[str] = Field(default=None, max_length=64, description="关联步骤 ID")
    session_id: Optional[str] = Field(default=None, max_length=64, description="关联会话 ID")
    idempotency_key: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="必填写操作幂等键",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_decision_type(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        dt = out.get("decision_type")
        alias_map = {
            "architecture_choice": DecisionType.DIRECTION_CHOICE.value,
            "checkpoint": DecisionType.PLAN_CONFIRM.value,
        }
        if isinstance(dt, str) and dt in alias_map:
            out["decision_type"] = alias_map[dt]
        return out


class DecisionGetArgs(BaseModel):
    """工具 7: decision_get 入参。"""

    model_config = ConfigDict(extra="ignore")

    project_id: Optional[str] = Field(default=None, description="可选项目 ID（校验与 Token 一致）")
    decision_id: str = Field(..., min_length=1, max_length=64, description="目标决策 ID")


class ArtifactRegisterArgs(BaseModel):
    """工具 8: artifact_register 入参（强制项目内相对路径校验与 metadata 体积限制）。"""

    model_config = ConfigDict(extra="ignore")

    project_id: Optional[str] = Field(default=None, description="可选项目 ID（校验与 Token 一致）")
    title: str = Field(..., min_length=1, max_length=240, description="产物标题")
    artifact_type: ArtifactType = Field(
        default=ArtifactType.MARKDOWN_NOTE,
        description="产物类型（file_diff / test_report / plan_summary / markdown_note / link）",
    )
    summary_content: str = Field(
        default="产物登记摘要",
        min_length=1,
        max_length=65536,
        description="产物结构化内容或摘要（<= 64 KB）",
    )
    relative_path: str = Field(
        default="",
        max_length=500,
        description="项目内相对路径（严禁 Windows 盘符、UNC 路径、绝对路径或 '..' 穿越）",
    )
    task_id: Optional[str] = Field(default=None, max_length=64, description="关联任务 ID")
    step_id: Optional[str] = Field(default=None, max_length=64, description="可选关联步骤 ID")
    session_id: Optional[str] = Field(default=None, max_length=64, description="关联会话 ID")
    execution_id: Optional[str] = Field(default=None, max_length=64, description="关联执行记录 ID")
    metadata: Optional[dict[str, Any]] = Field(
        default=None,
        description="轻量结构化元数据（JSON 序列化后 <= 16 KB）",
    )
    idempotency_key: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="必填写操作幂等键",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_artifact_type(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        at = out.get("artifact_type")
        alias_map = {
            "markdown_report": ArtifactType.MARKDOWN_NOTE.value,
            "file_diff": ArtifactType.GIT_DIFF_SUMMARY.value,
            "plan_summary": ArtifactType.PLAN_DOC.value,
            "link": ArtifactType.EXTERNAL_LINK.value,
        }
        if isinstance(at, str) and at in alias_map:
            out["artifact_type"] = alias_map[at]
        return out

    @model_validator(mode="before")
    @classmethod
    def _normalize_decision_type(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        dt = out.get("decision_type")
        alias_map = {
            "architecture_choice": DecisionType.DIRECTION_CHOICE.value,
            "checkpoint": DecisionType.PLAN_CONFIRM.value,
        }
        if isinstance(dt, str) and dt in alias_map:
            out["decision_type"] = alias_map[dt]
        return out


def map_domain_error_to_mcp_code(exc: Exception) -> str:
    """将领域/安全异常映射为统一的 MCP 业务错误码。"""
    if isinstance(exc, DomainError):
        explicit = (exc.context or {}).get("mcp_error_code")
        if explicit:
            return str(explicit)
    if isinstance(exc, CrossProjectReferenceError):
        return "MCP_CROSS_PROJECT_DENIED"
    if isinstance(exc, SecurityBoundaryError):
        return "MCP_UNAUTHORIZED"
    if isinstance(exc, EntityNotFoundError):
        return "MCP_RESOURCE_NOT_FOUND"
    if isinstance(exc, VersionConflictError):
        return "MCP_VERSION_CONFLICT"
    if isinstance(exc, InvariantViolationError):
        return "MCP_STATE_NOT_ALLOWED"
    return "MCP_INTERNAL_ERROR"


def build_mcp_success_envelope(
    tool_name: str,
    data: dict[str, Any],
    *,
    is_idempotent_replay: bool = False,
    protocol_version: str = MCP_PROTOCOL_VERSION_2026,
) -> dict[str, Any]:
    """构造稳定版本化的 MCP 工具成功响应结构。"""
    return {
        "ok": True,
        "schema_version": MCP_SCHEMA_VERSION,
        "protocol_version": protocol_version,
        "tool": tool_name,
        "is_idempotent_replay": is_idempotent_replay,
        "replayed": is_idempotent_replay,
        "data": data,
    }


def build_mcp_error_envelope(
    tool_name: str,
    exc: Exception,
    *,
    protocol_version: str = MCP_PROTOCOL_VERSION_2026,
) -> dict[str, Any]:
    """构造稳定版本化的 MCP 工具错误响应结构。"""
    code = map_domain_error_to_mcp_code(exc)
    if isinstance(exc, DomainError):
        return {
            "ok": False,
            "schema_version": MCP_SCHEMA_VERSION,
            "protocol_version": protocol_version,
            "tool": tool_name,
            "error_code": code,
            "error": {
                "code": code,
                "problem": exc.problem,
                "cause": exc.cause,
                "fix": exc.fix,
                "context": exc.context or {},
            },
        }
    return {
        "ok": False,
        "schema_version": MCP_SCHEMA_VERSION,
        "protocol_version": protocol_version,
        "tool": tool_name,
        "error_code": code,
        "error": {
            "code": code,
            "problem": f"MCP 工具 '{tool_name}' 调用参数或执行异常",
            "cause": str(exc),
            "fix": "请检查工具入参 JSON Schema 与必填字段后重试。",
            "context": {},
        },
    }
