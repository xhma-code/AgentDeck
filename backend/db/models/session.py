# -*- coding: utf-8 -*-
"""
协同会话域 ORM 模型：sessions, external_session_refs。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class SessionModel(Base):
    """项目协同会话主表。"""

    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_project_status", "project_id", "platform_status"),
        Index("ix_sessions_task", "task_id"),
        Index("ix_sessions_runtime_instance", "runtime_instance_id"),
        {"comment": "项目协同会话主表（强制归属 Project，四层状态分离）"},
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="平台会话唯一标识（UUID）"
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
        comment="强制归属的项目唯一标识（禁止游离无项目会话）",
    )
    task_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        comment="当前关联的任务唯一标识（可空，非空时必须同属一个项目）",
    )
    runtime_instance_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("runtime_instances.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联的 Runtime 实例唯一标识（可空）",
    )
    title: Mapped[str] = mapped_column(
        String(240), nullable=False, comment="协同会话显示标题"
    )
    source_channel: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="remote_link",
        comment="会话接入通道（remote_link/mcp/local_runtime/hosted）",
    )
    platform_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
        comment="平台会话管理状态（active/waiting_interaction/idle/paused/completed/archived）",
    )
    agent_reported_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unknown",
        comment="Agent 主动汇报状态（unknown/running/waiting_interaction/waiting_input/completed/failed）",
    )
    runtime_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unknown",
        comment="底层 Runtime 实例返回状态（online/offline/degraded/unknown）",
    )
    connection_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unknown",
        comment="Remote 或 Server 连接状态（connected/disconnected/unknown，断开不代表失败）",
    )
    status_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="状态单调递增序号（防止乱序旧事件覆盖新状态）"
    )
    last_activity_at: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="最近一次会话活动时间（UTC ISO 8601）"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="会话创建时间（UTC ISO 8601）"
    )
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="会话最后更新时间（UTC ISO 8601）"
    )


class ExternalSessionRefModel(Base):
    """外部 Agent 原生会话映射与官方 Remote 入口登记表。"""

    __tablename__ = "external_session_refs"
    __table_args__ = (
        Index("ix_external_session_refs_project_session", "project_id", "session_id"),
        Index("ix_external_session_refs_runtime_ext", "runtime_instance_id", "external_session_id"),
        {"comment": "外部 Runtime 会话引用与脱敏官方 Remote 入口表"},
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="外部会话引用唯一标识（UUID）"
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属项目唯一标识（强制与关联 Session 同项目）",
    )
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        comment="关联的平台 Session 唯一标识",
    )
    runtime_instance_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("runtime_instances.id", ondelete="RESTRICT"),
        nullable=False,
        comment="关联的 Runtime 实例唯一标识",
    )
    external_session_id: Mapped[str] = mapped_column(
        String(160), nullable=False, default="", comment="外部 Runtime 原生会话标识（未知允许为空）"
    )
    remote_entry_kind: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="none",
        comment="官方远程入口类型（none/claude_remote_control/chatgpt_remote/opencode_web/antigravity_remote/custom_safe_link）",
    )
    remote_entry_url: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="脱敏后的官方 Remote 入口地址（严禁含认证 Token 或一次性凭证）"
    )
    link_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unknown",
        comment="外部链接可用状态（active/stale/disconnected/unknown）",
    )
    last_activity_at: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="最近一次外部会话活动时间（UTC ISO 8601）"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="引用登记创建时间（UTC ISO 8601）"
    )
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="引用最后更新时间（UTC ISO 8601）"
    )

