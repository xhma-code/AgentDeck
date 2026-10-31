# -*- coding: utf-8 -*-
"""
任务规划域 ORM 模型：tasks, task_dependencies, task_revisions, steps, step_revisions。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class TaskModel(Base):
    """项目任务主表（支持父子任务树结构与平台/Agent 双轨状态）。"""

    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_project_parent", "project_id", "parent_task_id"),
        Index("ix_tasks_project_status", "project_id", "platform_status"),
        {"comment": "项目任务主表（支持同项目父子树层级与双轨状态管理）"},
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="任务唯一标识（UUID）"
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属项目唯一标识（强制归属）",
    )
    parent_task_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("tasks.id", ondelete="RESTRICT"),
        nullable=True,
        comment="父任务唯一标识（根任务为空，必须同属一个项目）",
    )
    title: Mapped[str] = mapped_column(
        String(240), nullable=False, comment="任务标题"
    )
    goal: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="当前生效版本的任务目标详情"
    )
    acceptance_criteria_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", comment="当前生效版本的验收标准列表（JSON 数组）"
    )
    platform_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="draft",
        comment="平台任务管理状态（draft/ready/in_progress/waiting_interaction/waiting_decision/blocked/needs_revalidation/completed/cancelled）",
    )
    agent_reported_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unknown",
        comment="Agent 主动汇报状态（unknown/running/waiting_interaction/waiting_input/completed/failed）",
    )
    priority: Mapped[str] = mapped_column(
        String(20), nullable=False, default="medium", comment="任务优先级（low/medium/high/critical）"
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="同级任务展示排序序号"
    )
    active_revision_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="当前生效的任务修订版本号"
    )
    needs_revalidation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="是否因上游修订待人工重验（0：否，1：是）"
    )
    revalidation_reason: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="标记待重验的具体原因说明"
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="乐观锁递增版本号"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="任务创建时间（UTC ISO 8601）"
    )
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="任务最后更新时间（UTC ISO 8601）"
    )


class TaskDependencyModel(Base):
    """同项目任务轻量前置依赖关系表（应用层强制校验无环 DAG）。"""

    __tablename__ = "task_dependencies"
    __table_args__ = (
        UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependencies_pair"),
        CheckConstraint("task_id != depends_on_task_id", name="no_self_dependency"),
        Index("ix_task_dependencies_project", "project_id"),
        Index("ix_task_dependencies_prereq", "depends_on_task_id"),
        {"comment": "同项目任务轻量前置依赖关系表（强制同项目且有向无环）"},
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="任务依赖关系唯一标识（UUID）"
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
        comment="后置受依赖任务唯一标识",
    )
    depends_on_task_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        comment="前置依赖任务唯一标识",
    )
    dependency_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="finish_to_start", comment="依赖语义类型（finish_to_start）"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="依赖创建时间（UTC ISO 8601）"
    )


class TaskRevisionModel(Base):
    """任务追加式修订快照历史表（只追加不覆盖）。"""

    __tablename__ = "task_revisions"
    __table_args__ = (
        UniqueConstraint("task_id", "revision_no", name="uq_task_revisions_task_rev"),
        Index("ix_task_revisions_project_task", "project_id", "task_id"),
        {"comment": "任务追加式修订历史快照表（保留历次目标与验收标准变更）"},
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="任务修订记录唯一标识（UUID）"
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
        comment="所属任务唯一标识",
    )
    revision_no: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="单调递增修订版本号（从 1 开始）"
    )
    title_snapshot: Mapped[str] = mapped_column(
        String(240), nullable=False, comment="修订时的任务标题快照"
    )
    goal_snapshot: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="修订时的任务目标快照"
    )
    acceptance_criteria_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", comment="修订时的验收标准列表快照（JSON 数组）"
    )
    change_reason: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="本次修订变更原因说明"
    )
    changed_by: Mapped[str] = mapped_column(
        String(40), nullable=False, default="user", comment="修订发起方（user/mcp_agent/system）"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="修订记录创建时间（UTC ISO 8601）"
    )


class StepModel(Base):
    """任务执行步骤表。"""

    __tablename__ = "steps"
    __table_args__ = (
        Index("ix_steps_task_seq", "task_id", "seq_order"),
        Index("ix_steps_project_status", "project_id", "platform_status"),
        {"comment": "任务拆解步骤表（支持追加式修订与下游待重验标记）"},
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="步骤唯一标识（UUID）"
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
        comment="所属任务唯一标识",
    )
    seq_order: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="任务内步骤执行顺序号（从 1 开始递增）"
    )
    title: Mapped[str] = mapped_column(
        String(240), nullable=False, comment="步骤标题"
    )
    instruction: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="当前生效版本的步骤具体说明"
    )
    platform_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        comment="平台步骤管理状态（pending/ready/in_progress/waiting_interaction/blocked/needs_revalidation/completed/skipped）",
    )
    agent_reported_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unknown",
        comment="Agent 主动汇报步骤状态",
    )
    active_revision_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="当前生效的步骤修订版本号"
    )
    needs_revalidation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="是否因前置步骤或任务修订待重验（0：否，1：是）"
    )
    revalidation_reason: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="步骤进入待重验的原因说明"
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="乐观锁递增版本号"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="步骤创建时间（UTC ISO 8601）"
    )
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="步骤最后更新时间（UTC ISO 8601）"
    )


class StepRevisionModel(Base):
    """步骤追加式修订快照历史表（只追加不覆盖）。"""

    __tablename__ = "step_revisions"
    __table_args__ = (
        UniqueConstraint("step_id", "revision_no", name="uq_step_revisions_step_rev"),
        Index("ix_step_revisions_task_step", "task_id", "step_id"),
        {"comment": "任务步骤追加式修订历史快照表"},
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="步骤修订记录唯一标识（UUID）"
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
        comment="所属任务唯一标识",
    )
    step_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("steps.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属步骤唯一标识",
    )
    revision_no: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="单调递增步骤修订版本号（从 1 开始）"
    )
    title_snapshot: Mapped[str] = mapped_column(
        String(240), nullable=False, comment="修订时的步骤标题快照"
    )
    instruction_snapshot: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="修订时的步骤说明内容快照"
    )
    change_reason: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="本次步骤修订原因说明"
    )
    changed_by: Mapped[str] = mapped_column(
        String(40), nullable=False, default="user", comment="修订发起方（user/mcp_agent/system）"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="步骤修订创建时间（UTC ISO 8601）"
    )

