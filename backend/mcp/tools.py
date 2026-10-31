# -*- coding: utf-8 -*-
"""
8 大核心 MCP 协作工具定义、JSON Schema 注册与工具元数据绑定（backend/mcp/tools.py）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from pydantic import BaseModel

from backend.mcp.auth import TOOL_REQUIRED_SCOPES
from backend.mcp.schemas import (
    ArtifactRegisterArgs,
    CheckpointRequestArgs,
    DecisionGetArgs,
    ProjectContextGetArgs,
    StepUpdateArgs,
    TaskGetArgs,
    TaskReportProgressArgs,
    TaskRevisionGetArgs,
)
from backend.mcp.service import McpCollaborationService
from backend.security.mcp_token_guard import VerifiedMcpPrincipal


@dataclass(frozen=True)
class McpToolDefinition:
    """MCP 协作工具完整契约定义。"""

    name: str
    title: str
    description: str
    required_scope: str
    is_mutating: bool
    args_model: type[BaseModel]

    def to_mcp_tool_dict(self) -> dict[str, Any]:
        """转换为标准 MCP Tool 描述结构（兼容 2026-07-28 与 2025-11-25）。"""
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        return {
            "name": self.name,
            "title": self.title,
            "description": f"{self.description} [所需权限: {self.required_scope}]",
            "inputSchema": schema,
            "annotations": {
                "title": self.title,
                "readOnlyHint": not self.is_mutating,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
                "requiredScope": self.required_scope,
            },
        }


MCP_TOOL_DEFINITIONS: tuple[McpToolDefinition, ...] = (
    McpToolDefinition(
        name="project_context_get",
        title="获取项目工作上下文摘要",
        description=(
            "按当前 MCP Token 绑定的单一项目返回最小工作上下文（项目信息、待办/进行中任务摘要、"
            "未决 Decision 与活跃 Session），不导出全库数据。"
        ),
        required_scope=TOOL_REQUIRED_SCOPES["project_context_get"],
        is_mutating=False,
        args_model=ProjectContextGetArgs,
    ),
    McpToolDefinition(
        name="task_get",
        title="获取或原子认领研发任务",
        description=(
            "获取指定任务（或自动挑选前置依赖已满足、最高优先级的待执行任务）的完整详情、生效修订版与步骤列表；"
            "传入 claim_for_session_id 时可执行多会话并发安全的原子认领。"
        ),
        required_scope=TOOL_REQUIRED_SCOPES["task_get"],
        is_mutating=False,
        args_model=TaskGetArgs,
    ),
    McpToolDefinition(
        name="task_revision_get",
        title="读取任务修订历史与重验状态",
        description=(
            "读取指定任务的生效修订快照（或指定 revision_no）、历史修订摘要以及是否处于上游修订待人工重验状态。"
        ),
        required_scope=TOOL_REQUIRED_SCOPES["task_revision_get"],
        is_mutating=False,
        args_model=TaskRevisionGetArgs,
    ),
    McpToolDefinition(
        name="task_report_progress",
        title="汇报任务执行进度",
        description=(
            "向 AgentDeck 汇报任务执行状态与进度摘要（仅更新 agent_reported_status 并记录事件，"
            "绝不越权将平台验收状态 platform_status 标记为完成）。"
        ),
        required_scope=TOOL_REQUIRED_SCOPES["task_report_progress"],
        is_mutating=True,
        args_model=TaskReportProgressArgs,
    ),
    McpToolDefinition(
        name="step_update",
        title="更新步骤状态（含修订乐观锁校验）",
        description=(
            "更新任务步骤的 Agent 执行状态，强制校验 expected_revision_no 防止覆盖用户中途修订的指令。"
        ),
        required_scope=TOOL_REQUIRED_SCOPES["step_update"],
        is_mutating=True,
        args_model=StepUpdateArgs,
    ),
    McpToolDefinition(
        name="checkpoint_request",
        title="发起人工决策或高风险门禁请求",
        description=(
            "创建持久化 Decision 并唤醒平台跨页面决策弹窗（risk_gate 及 high/critical 风险强制 manual_only 禁自动通过）。"
        ),
        required_scope=TOOL_REQUIRED_SCOPES["checkpoint_request"],
        is_mutating=True,
        args_model=CheckpointRequestArgs,
    ),
    McpToolDefinition(
        name="decision_get",
        title="查询人工决策处理结果",
        description="查询指定 Decision 的当前状态（pending/resolved/expired）及用户选择的方案与补充意见。",
        required_scope=TOOL_REQUIRED_SCOPES["decision_get"],
        is_mutating=False,
        args_model=DecisionGetArgs,
    ),
    McpToolDefinition(
        name="artifact_register",
        title="登记项目内结构化交付物",
        description=(
            "登记测试报告、计划摘要、差异说明等结构化产物，强制校验项目内相对路径防穿越并自动关联任务当前修订号。"
        ),
        required_scope=TOOL_REQUIRED_SCOPES["artifact_register"],
        is_mutating=True,
        args_model=ArtifactRegisterArgs,
    ),
)

MCP_TOOLS_BY_NAME: dict[str, McpToolDefinition] = {t.name: t for t in MCP_TOOL_DEFINITIONS}


def list_mcp_tool_dicts(granted_scopes: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """返回全部 8 个标准 MCP 工具的 Schema 定义列表。"""
    _ = granted_scopes
    return [t.to_mcp_tool_dict() for t in MCP_TOOL_DEFINITIONS]


def dispatch_mcp_tool_call(
    collab_service: McpCollaborationService,
    principal: VerifiedMcpPrincipal,
    tool_name: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """根据工具名称路由调用 McpCollaborationService 对应方法。"""
    handlers: dict[
        str,
        Callable[[VerifiedMcpPrincipal, dict[str, Any]], tuple[dict[str, Any], bool]],
    ] = {
        "project_context_get": collab_service.project_context_get,
        "task_get": collab_service.task_get,
        "task_revision_get": collab_service.task_revision_get,
        "task_report_progress": collab_service.task_report_progress,
        "step_update": collab_service.step_update,
        "checkpoint_request": collab_service.checkpoint_request,
        "decision_get": collab_service.decision_get,
        "artifact_register": collab_service.artifact_register,
    }
    handler = handlers.get(tool_name)
    if handler is None:
        raise KeyError(f"未知的 MCP 工具: '{tool_name}'")
    return handler(principal, arguments)

