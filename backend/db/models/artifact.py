# -*- coding: utf-8 -*-
"""
交付产物与关键变更 ORM 模型：artifacts。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class ArtifactModel(Base):
    """项目交付产物与关键变更登记表。"""

    __tablename__ = "artifacts"
    __table_args__ = (
        Index("ix_artifacts_project_task", "project_id", "task_id"),
        Index("ix_artifacts_session", "session_id"),
        {"comment": "项目交付产物与关键变更登记表（记录元数据、校验值与关联修订版本）"},
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="产物唯一标识（UUID）"
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
    artifact_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="markdown_note",
        comment="产物类型（plan_doc/git_diff_summary/test_report/markdown_note/file_ref/external_link）",
    )
    title: Mapped[str] = mapped_column(
        String(240), nullable=False, comment="产物显示标题"
    )
    relative_path: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="相对于所属 Project.root_path 的安全相对路径（可空）"
    )
    summary_content: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="产物高层摘要或 Markdown 内容"
    )
    checksum_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="内容或文件 SHA-256 校验值（可空）"
    )
    source_task_revision_no: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="产出时关联的任务修订版本号（可空）"
    )
    metadata_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", comment="扩展非敏感元数据（JSON 字符串）"
    )
    created_by: Mapped[str] = mapped_column(
        String(40), nullable=False, default="user", comment="登记来源（user/mcp_agent/runtime/system）"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="产物登记创建时间（UTC ISO 8601）"
    )

