# -*- coding: utf-8 -*-
"""
系统支撑域 ORM 模型：device_sessions, push_subscriptions, command_idempotency, table_column_comments。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Index, Integer, PrimaryKeyConstraint, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class DeviceSessionModel(Base):
    """配对设备与访问令牌鉴权表。"""

    __tablename__ = "device_sessions"
    __table_args__ = (
        Index("ix_device_sessions_token_hash", "auth_token_hash"),
        {"comment": "移动端与桌面端扫码配对设备及访问令牌鉴权表"},
    )

    device_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="设备唯一标识（UUID）"
    )
    device_name: Mapped[str] = mapped_column(
        String(120), nullable=False, comment="配对设备显示名称（如 iPhone / Desktop Browser）"
    )
    auth_token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, comment="设备访问令牌 SHA-256 哈希值（严禁存明文）"
    )
    tailscale_ip: Mapped[Optional[str]] = mapped_column(
        String(160), nullable=True, comment="最近连接的 Tailscale/ZeroTier/局域网漫游 IP 列表"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="配对创建时间（UTC ISO 8601）"
    )
    last_active_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="最近活跃访问时间（UTC ISO 8601）"
    )
    is_revoked: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="是否已吊销（0：有效，1：已吊销）"
    )


class PushSubscriptionModel(Base):
    """移动端 PWA 原生 Web Push 订阅凭据表。"""

    __tablename__ = "push_subscriptions"
    __table_args__ = (
        Index("ix_push_subscriptions_device_active", "device_id", "is_active"),
        {"comment": "移动端 PWA 原生 Web Push 推送订阅凭据表"},
    )

    subscription_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="订阅唯一标识（SHA-256(endpoint)）"
    )
    device_id: Mapped[str] = mapped_column(
        String(36), nullable=False, comment="关联的配对设备唯一标识"
    )
    endpoint: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Web Push 网关推送目标 URL"
    )
    p256dh_key: Mapped[str] = mapped_column(
        Text, nullable=False, comment="客户端加密公钥（Base64 URL-safe）"
    )
    auth_key: Mapped[str] = mapped_column(
        Text, nullable=False, comment="客户端鉴权密钥（Base64 URL-safe）"
    )
    user_agent: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="客户端浏览器 User-Agent"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="订阅创建时间（UTC ISO 8601）"
    )
    last_pushed_at: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="最近一次成功推送时间（UTC ISO 8601）"
    )
    is_active: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="是否有效（0：已失效，1：有效）"
    )


class CommandIdempotencyModel(Base):
    """命令与交互提交幂等记录持久化表。"""

    __tablename__ = "command_idempotency"
    __table_args__ = (
        Index("ix_command_idempotency_target", "target_entity_type", "target_entity_id"),
        {"comment": "客户端写命令与交互式决策提交幂等键持久化表"},
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(120), primary_key=True, comment="客户端生成的唯一幂等键（Idempotency-Key）"
    )
    command_type: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="命令或操作类型（如 decision.resolve / task.revise）"
    )
    target_entity_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="", comment="关联实体类型"
    )
    target_entity_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="关联实体主键"
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="done", comment="处理终态（done：已完成，error：失败可重放）"
    )
    response_snapshot_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", comment="幂等重放时直接返回的脱敏响应快照（JSON）"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="首次处理时间（UTC ISO 8601）"
    )
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="最后更新时间（UTC ISO 8601）"
    )


class TableColumnCommentModel(Base):
    """数据字典中文注释元数据注册表。"""

    __tablename__ = "table_column_comments"
    __table_args__ = (
        PrimaryKeyConstraint("table_name", "column_name", name="pk_table_column_comments"),
        {"comment": "全库数据表名与字段中文注释元数据字典表"},
    )

    table_name: Mapped[str] = mapped_column(
        String(80), nullable=False, comment="数据表英文名称"
    )
    table_comment: Mapped[str] = mapped_column(
        Text, nullable=False, comment="数据表中文注释说明"
    )
    column_name: Mapped[str] = mapped_column(
        String(80), nullable=False, comment="字段英文名称"
    )
    column_comment: Mapped[str] = mapped_column(
        Text, nullable=False, comment="字段中文注释说明"
    )

