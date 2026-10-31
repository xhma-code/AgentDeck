# -*- coding: utf-8 -*-
"""
全局领域事件审计流 ORM 模型：events（与业务状态同事务原子持久化）。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class EventModel(Base):
    """全局有序持久化领域事件流表。"""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_project_seq", "project_id", "seq_id"),
        Index("ix_events_entity", "entity_type", "entity_id"),
        {"comment": "全局领域事件持久化审计流表（与业务写操作同事务提交，支持断线按 seq_id 补拉）"},
    )

    seq_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="全局严格递增事件序列号（客户端增量订阅游标）",
    )
    event_id: Mapped[str] = mapped_column(
        String(36), nullable=False, unique=True, comment="事件全局唯一标识（UUID）"
    )
    project_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        comment="关联项目唯一标识（系统级事件可空）",
    )
    entity_type: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="变更领域实体类型（project/task/step/session/execution/decision/artifact/runtime_instance/security）"
    )
    entity_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="变更领域实体唯一标识"
    )
    event_type: Mapped[str] = mapped_column(
        String(80), nullable=False, comment="标准领域事件名称（如 decision.created / task.revision_created）"
    )
    actor_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="user", comment="触发主体类型（user/mcp_agent/runtime/system）"
    )
    actor_id: Mapped[str] = mapped_column(
        String(120), nullable=False, default="", comment="触发主体非敏感标识"
    )
    correlation_id: Mapped[str] = mapped_column(
        String(120), nullable=False, default="", comment="请求或命令关联追踪 ID"
    )
    payload_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", comment="脱敏后的事件结构化载荷（JSON 字符串，严禁写入密钥）"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="事件发生时间（UTC ISO 8601）"
    )

