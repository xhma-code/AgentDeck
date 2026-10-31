# -*- coding: utf-8 -*-
"""
项目主实体与 MCP 项目作用域安全令牌 ORM 模型。
所有表名与字段均包含完整中文注释。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.v2.db.base import V2Base


class ProjectModel(V2Base):
    """研发项目主表。"""

    __tablename__ = "projects"
    __table_args__ = {"comment": "研发项目主表（Project-first 顶层隔离边界）"}

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="项目唯一标识（UUID）"
    )
    slug: Mapped[str] = mapped_column(
        String(80), nullable=False, unique=True, comment="项目唯一短代号（URL 安全）"
    )
    name: Mapped[str] = mapped_column(
        String(160), nullable=False, comment="项目显示名称"
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="项目背景与目标说明"
    )
    root_path: Mapped[str] = mapped_column(
        Text, nullable=False, unique=True, comment="项目本地工作区规范化绝对路径（文件读取白名单根边界）"
    )
    git_remote: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="关联 Git 远端仓库地址（可空）"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", comment="项目生命周期状态（active/paused/archived）"
    )
    summary_next_action: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="当前下一步关键动作摘要"
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="乐观锁递增版本号"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="项目创建时间（UTC ISO 8601）"
    )
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="项目最后更新时间（UTC ISO 8601）"
    )


class McpProjectTokenModel(V2Base):
    """MCP 项目作用域最小权限访问令牌表（禁止跨项目通用令牌）。"""

    __tablename__ = "mcp_project_tokens"
    __table_args__ = (
        Index("ix_mcp_project_tokens_project_revoked", "project_id", "is_revoked"),
        {"comment": "MCP 项目级最小权限令牌表（强制绑定单一 Project，仅存 SHA-256 哈希）"},
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="令牌记录唯一标识（UUID）"
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        comment="强制绑定的单一项目唯一标识",
    )
    token_name: Mapped[str] = mapped_column(
        String(120), nullable=False, comment="令牌用途显示名称（如 claude-code-local）"
    )
    token_prefix: Mapped[str] = mapped_column(
        String(24), nullable=False, comment="令牌可识别安全前缀（不含完整明文）"
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, comment="访问令牌 SHA-256 哈希值（严禁存储明文）"
    )
    scopes_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", comment="授予的最小权限范围列表（JSON 数组）"
    )
    is_revoked: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="是否已吊销（0：有效，1：已吊销）"
    )
    expires_at: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="令牌过期时间（UTC ISO 8601，可空）"
    )
    last_used_at: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="最近一次鉴权使用时间（UTC ISO 8601）"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="令牌创建时间（UTC ISO 8601）"
    )

