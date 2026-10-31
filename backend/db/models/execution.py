# -*- coding: utf-8 -*-
"""
执行域 ORM 模型：executions, execution_attempts（与 Session 三层严格分离，对齐 v1.3 第 11.2 节）。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class ExecutionModel(Base):
    """任务/步骤执行单表（同一 Session 可关联多个 Execution）。"""

    __tablename__ = "executions"
    __table_args__ = (
        Index("ix_executions_project_task", "project_id", "task_id"),
        Index("ix_executions_session", "session_id"),
        {"comment": "任务与步骤执行单表（与 Session 及 ExecutionAttempt 三层分离）"},
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="执行单唯一标识（UUID）"
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属项目唯一标识",
    )
    task_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        comment="关联的任务唯一标识",
    )
    step_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("steps.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联的具体步骤唯一标识（可空）",
    )
    session_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联的协同会话唯一标识（可空）",
    )
    task_revision_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="执行关联的任务修订版本号快照"
    )
    step_revision_no: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="执行关联的步骤修订版本号快照（可空）"
    )
    platform_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="queued",
        comment="平台执行管理状态（queued/running/waiting_interaction/succeeded/failed/cancelled/needs_revalidation）",
    )
    agent_reported_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unknown",
        comment="Agent 主动汇报的执行状态",
    )
    runtime_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unknown",
        comment="Runtime 返回的执行状态",
    )
    connection_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unknown",
        comment="执行期间的远程连接状态（断开不自动判失败）",
    )
    summary_note: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="执行过程或结果高层摘要说明"
    )
    started_at: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="执行开始时间（UTC ISO 8601）"
    )
    finished_at: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="执行结束时间（UTC ISO 8601）"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="执行单创建时间（UTC ISO 8601）"
    )
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="执行单最后更新时间（UTC ISO 8601）"
    )


class ExecutionAttemptModel(Base):
    """单次执行尝试表（同一 Execution 可包含多次重试 Attempt，历史不可覆盖）。"""

    __tablename__ = "execution_attempts"
    __table_args__ = (
        UniqueConstraint("execution_id", "attempt_no", name="uq_execution_attempts_exec_no"),
        Index("ix_execution_attempts_project_exec", "project_id", "execution_id"),
        {"comment": "执行尝试记录表（记录每次运行尝试的 Runtime 实例、状态与退出信息）"},
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="执行尝试唯一标识（UUID）"
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属项目唯一标识",
    )
    execution_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("executions.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属执行单唯一标识",
    )
    attempt_no: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="单调递增尝试次数序号（从 1 开始）"
    )
    runtime_instance_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("runtime_instances.id", ondelete="SET NULL"),
        nullable=True,
        comment="本次尝试使用的 Runtime 实例唯一标识（可空）",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="running",
        comment="本次尝试运行状态（running/waiting_interaction/succeeded/failed/cancelled/timed_out）",
    )
    exit_code: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="进程或调用退出码（可空）"
    )
    error_summary: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="失败或异常高层诊断摘要（不含敏感凭证）"
    )
    model_endpoint_descriptor_json: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="可选非敏感模型环境说明快照（JSON）"
    )
    started_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="本次尝试开始时间（UTC ISO 8601）"
    )
    ended_at: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="本次尝试结束时间（UTC ISO 8601，可空）"
    )

