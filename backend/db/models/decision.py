# -*- coding: utf-8 -*-
"""
交互式决策与检查点 ORM 模型：decisions（对齐 v1.3 与交互式 Decision 规范）。

核心特性：
1. 持久化挂起决策，服务重启后待决策状态 100% 保留不丢失。
2. 完整记录风险等级、超时策略 (timeout_policy)、倒计时秒数 (timeout_sec)、推荐选项、
   处理来源 (user_manual / timeout_auto_recommended) 与幂等提交键 (resolution_idempotency_key)。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class DecisionModel(Base):
    """持久化交互式决策与人工检查点表。"""

    __tablename__ = "decisions"
    __table_args__ = (
        Index("ix_decisions_project_status", "project_id", "status"),
        Index("ix_decisions_session_status", "session_id", "status"),
        Index("ix_decisions_task", "task_id"),
        {"comment": "持久化交互式决策与人工检查点表（支持超时策略、推荐选项与幂等拍板留痕）"},
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="决策唯一标识（UUID）"
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属项目唯一标识",
    )
    task_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联的任务唯一标识（可空）",
    )
    step_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("steps.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联的步骤唯一标识（可空）",
    )
    session_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联的协同会话唯一标识（可空）",
    )
    execution_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("executions.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联的执行单唯一标识（可空）",
    )
    decision_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="interactive_question",
        comment="决策类型（interactive_question/plan_confirm/scope_change/risk_gate/direction_choice）",
    )
    risk_level: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="normal",
        comment="风险等级（low/normal/high/critical；高风险禁止默认超时自动提交）",
    )
    title: Mapped[str] = mapped_column(
        String(240), nullable=False, comment="决策问题主标题"
    )
    context_summary: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="决策背景、影响范围与判断依据说明"
    )
    options_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
        comment="结构化候选选项列表（DecisionOptionItem 数组 JSON）",
    )
    recommended_option_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="推荐选项 ID（供高亮提示及合规超时自动采纳）"
    )
    allow_custom_input: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="是否允许用户附加自定义文本说明（0：否，1：是）"
    )
    timeout_policy: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="auto_submit_recommended",
        comment="超时处理策略（auto_submit_recommended/manual_only/expire_on_timeout）",
    )
    timeout_sec: Mapped[int] = mapped_column(
        Integer, nullable=False, default=45, comment="前端交互倒计时秒数（默认 45 秒，0 表示无倒计时）"
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        comment="决策生命周期状态（pending/resolved/expired/cancelled）",
    )
    chosen_option_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="最终选定的选项 ID（可空）"
    )
    custom_note: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="用户拍板时填写的补充说明或超时自动提交留痕说明"
    )
    resolution_payload_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", comment="完整决策结果结构化载荷（JSON 字符串）"
    )
    resolved_by: Mapped[Optional[str]] = mapped_column(
        String(40),
        nullable=True,
        comment="决策处理来源（user_manual：人工确认，timeout_auto_recommended：超时自动采纳推荐，system：系统）",
    )
    resolution_idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(120),
        nullable=True,
        comment="拍板提交幂等键（防止前端网络抖动指数退避重试产生重复冲突）",
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="乐观锁递增版本号"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="决策创建时间（UTC ISO 8601）"
    )
    resolved_at: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="决策完成时间（UTC ISO 8601，可空）"
    )
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="决策最后更新时间（UTC ISO 8601）"
    )

