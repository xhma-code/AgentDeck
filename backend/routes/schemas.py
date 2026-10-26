# -*- coding: utf-8 -*-
"""
AgentDeck API 请求/响应 Pydantic 模型与 ETag 校验工具。
"""

from __future__ import annotations

from typing import Any, Optional, Union
from pydantic import BaseModel, Field


class PairCodeExchangeRequest(BaseModel):
    pair_code: str = Field(..., description="二维码中的一次性配对码")
    device_name: str = Field(default="Mobile PWA", description="手机端设备标识")


class CreateSessionRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    agent_type: str = Field(default="claude_code", description="Agent 类型 (claude_code/agy_cli/opencode)")
    project_path: str = Field(default="", description="工作区目录绝对路径")
    title: str = Field(default="", description="初始会话标题")
    execution_mode: str = Field(default="auto", description="初始执行模式 (auto/accept_edits/plan/ask)")
    model_name: str = Field(default="", description="选定模型名称")
    initial_prompt: str = Field(default="", description="可选首条发送指令")


class SendPromptRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    prompt: str = Field(..., description="发送给 Agent 的指令内容")
    execution_mode: Optional[str] = Field(default=None, description="本轮执行模式覆盖")
    model_name: Optional[str] = Field(default=None, description="本轮模型名称覆盖")
    project_path: Optional[str] = Field(default=None, description="工作区路径覆盖")
    auto_fork_ide: bool = Field(default=True, description="若为 Antigravity IDE 会话是否自动安全克隆至 CLI")


class ToolApprovalDecisionRequest(BaseModel):
    decision: str = Field(..., description="approved / approve_session_edits / denied")
    feedback: str = Field(default="", description="拒绝或补充指导意见")


class SwitchSessionModeRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    execution_mode: Optional[str] = Field(default=None, description="选定切换的执行/权限模式 (plan/ask/accept_edits/auto)")
    mode: Optional[str] = Field(default=None, description="兼容简写模式参数")

    @property
    def target_mode(self) -> str:
        return (self.execution_mode or self.mode or "").strip()


class SwitchSessionModelRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    model_name: str = Field(..., description="选定切换的模型名称")


class SessionMetaUpdateRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    is_pinned: Optional[int] = Field(default=None, description="是否置顶 (0/1)")
    execution_mode: Optional[str] = Field(default=None, description="执行策略 (plan/ask/accept_edits/auto)")
    model_name: Optional[str] = Field(default=None, description="当前会话选定的模型名称")
    title: Optional[str] = Field(default=None, description="自定义会话标题")


class AddWorkspaceRequest(BaseModel):
    workspace_name: str = Field(..., description="工作区名称")
    workspace_path: str = Field(..., description="工作区绝对路径")
    default_agent: str = Field(default="claude_code", description="默认 Agent 类型")


class HideWorkspaceRequest(BaseModel):
    workspace_path: str = Field(..., description="要从项目列表移出隐藏的目录路径")


def check_etag_matches(if_none_match: Optional[str], current_etag: str) -> bool:
    """统一校验客户端 If-None-Match 是否命中服务端 ETag（支持弱验证 W/ 前缀与引号容错）。"""
    if not if_none_match or not current_etag:
        return False
    inm = if_none_match.strip()
    etag = current_etag.strip()
    return inm == etag or inm == etag.strip('"') or inm.strip('"') == etag.strip('"')


class MarkAllViewedRequest(BaseModel):
    session_ids: Optional[list[str]] = Field(default=None, description="可选指定批量标记的会话列表，留空表示全量")


class SyncViewedRequest(BaseModel):
    viewed_sessions: dict[str, Union[str, int, float]] = Field(default_factory=dict, description="会话ID到时间戳的字典")

class FormReplyRequest(BaseModel):
    """交互式决策表单答复请求体。"""

    answer: dict[str, Any]
    source: Optional[str] = "user"
