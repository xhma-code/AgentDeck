# -*- coding: utf-8 -*-
"""
通知支撑域 ORM 模型：
1. notification_outbox（业务事件到通知的可靠派发任务表）
2. notifications（通知中心主台账，单用户全局已读）
3. notification_events（通知与底层领域事件溯源关联表）
4. notification_deliveries（按订阅追踪的外呼状态机与租约表）
5. notification_changes（持久化通知变更流，支持 SSE 断线补流）
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base


class NotificationOutboxModel(Base):
    """业务事务到通知派发解耦的极简可靠 Outbox 任务表。"""

    __tablename__ = "notification_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'retry', 'processed', 'failed')",
            name="ck_notif_outbox_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1",
            name="ck_notif_outbox_attempts",
        ),
        CheckConstraint(
            "(status != 'processing') OR (locked_by IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_notif_outbox_lock",
        ),
        CheckConstraint(
            "(status != 'processed' OR processed_at IS NOT NULL) AND (status != 'failed' OR failed_at IS NOT NULL)",
            name="ck_notif_outbox_terminal",
        ),
        Index("ix_notif_outbox_claim", "status", "next_retry_at"),
        Index("ix_notif_outbox_lease", "status", "lease_expires_at"),
        {"comment": "业务事件到通知派发解耦的可靠事务 Outbox 任务表"},
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="Outbox 任务全局唯一标识（UUID）"
    )
    event_seq_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("events.seq_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="关联的底层领域审计事件序号（外键，全局唯一防重复）",
    )
    event_type: Mapped[str] = mapped_column(
        String(80), nullable=False, comment="冗余领域事件类型（便于分流过滤）"
    )
    payload_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default="{}", comment="脱敏后的领域事件载荷（JSON 字符串）"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending",
        comment="Outbox 处理状态（pending/processing/retry/processed/failed）",
    )
    locked_by: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="认领该任务的 Dispatcher 进程/协程标识"
    )
    locked_at: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="认领加锁时间戳（UTC ISO 8601）"
    )
    lease_expires_at: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="任务租约过期绝对时间戳（UTC ISO 8601，超时可被接管）"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0", comment="已尝试派发次数"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5", comment="最大允许尝试次数（默认 5）"
    )
    next_retry_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="下次可被扫描认领的时间戳（UTC ISO 8601）"
    )
    last_error: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="最近一次派发异常原因与错误摘要（脱敏截断）"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="Outbox 任务创建时间（UTC ISO 8601，与业务同事务）"
    )
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="最后更新时间（UTC ISO 8601）"
    )
    processed_at: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="派发成功终态完成时间（UTC ISO 8601）"
    )
    failed_at: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="派发达到最大重试上限后的熔断失败时间（UTC ISO 8601）"
    )


class NotificationModel(Base):
    """通知中心主台账实体表（单用户多端共享已读状态）。"""

    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "category IN ('decision_required', 'task_blocked', 'task_completed', 'execution_failed', 'system_alert')",
            name="ck_notifications_category",
        ),
        CheckConstraint(
            "priority IN ('urgent', 'high', 'normal', 'low')",
            name="ck_notifications_priority",
        ),
        CheckConstraint(
            "aggregated_count BETWEEN 1 AND 20",
            name="ck_notifications_count",
        ),
        CheckConstraint(
            "is_read IN (0, 1) AND is_window_open IN (0, 1)",
            name="ck_notifications_flags",
        ),
        CheckConstraint(
            "(is_read = 0) OR (read_at IS NOT NULL)",
            name="ck_notifications_read",
        ),
        CheckConstraint(
            "link_url LIKE '/%' AND link_url NOT LIKE '//%' AND instr(link_url, char(92)) = 0 AND instr(link_url, char(0)) = 0 AND instr(link_url, char(9)) = 0 AND instr(link_url, char(10)) = 0 AND instr(link_url, char(13)) = 0",
            name="ck_notifications_link",
        ),
        Index("ix_notifications_cursor", text("created_at DESC"), text("id DESC")),
        Index("ix_notifications_project_cursor", "project_id", text("created_at DESC"), text("id DESC")),
        Index("ix_notifications_unread", "is_read", text("created_at DESC")),
        Index(
            "uq_notifications_open_agg",
            text("coalesce(project_id, '')"),
            "aggregation_key",
            unique=True,
            sqlite_where=text("is_window_open = 1 AND aggregation_key IS NOT NULL"),
        ),
        {"comment": "单用户全局通知中心主实体表（支持时间窗口聚合与多端同步已读）"},
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="通知唯一标识（UUID）"
    )
    project_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        comment="归属项目唯一标识（可空，项目删除时级联清除，NULL 表示全局系统通知）",
    )
    category: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="通知业务分类（decision_required/task_blocked/task_completed/execution_failed/system_alert）",
    )
    priority: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="normal",
        server_default="normal",
        comment="通知优先级（urgent/high/normal/low）",
    )
    title: Mapped[str] = mapped_column(
        String(240), nullable=False, comment="通知展示标题（纯文本简述）"
    )
    body: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default="", comment="通知正文内容或聚合更新摘要"
    )
    link_url: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="/",
        server_default="/",
        comment="点击跳转的受控站内相对路径（严格以单斜杠开头，拒绝外部或协议相对路径）",
    )
    target_entity_type: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="关联目标实体类型（如 decision/task/step/execution）"
    )
    target_entity_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="关联目标实体唯一标识"
    )
    aggregation_key: Mapped[Optional[str]] = mapped_column(
        String(120), nullable=True, comment="降噪聚合分组键（NULL 表示独立通知不折叠）"
    )
    aggregated_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1", comment="当前通知已聚合折叠的事件条数（1-20）"
    )
    window_started_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="聚合时间窗口起始时间戳（UTC ISO 8601）"
    )
    is_window_open: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
        comment="聚合窗口是否依然开放允许合并（1：开放，0：已关闭，配合唯一索引杜绝并发重复创建）",
    )
    is_read: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0", comment="全局已读状态（0：未读，1：已读）"
    )
    read_at: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="用户标记已读时间戳（UTC ISO 8601）"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="通知创建时间（UTC ISO 8601）"
    )
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="通知最后更新或聚合时间（UTC ISO 8601）"
    )


class NotificationEventModel(Base):
    """通知与底层领域事件的多对一溯源关联表。"""

    __tablename__ = "notification_events"
    __table_args__ = (
        Index("ix_notif_events_notification", "notification_id"),
        {"comment": "通知中心与原始领域事件的关联溯源表（唯一键防重复统计）"},
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="关联记录唯一标识（UUID）"
    )
    notification_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("notifications.id", ondelete="CASCADE"),
        nullable=False,
        comment="关联通知主表唯一标识（外键级联删除）",
    )
    event_seq_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("events.seq_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="关联底层领域事件序号（外键，全局唯一防重复关联）",
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="关联创建时间戳（UTC ISO 8601）"
    )


class NotificationDeliveryModel(Base):
    """按设备订阅独立追踪的 Web Push 外呼状态机与租约重试表。"""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("notification_id", "subscription_id", name="uq_notif_deliveries_notif_sub"),
        ForeignKeyConstraint(
            ["subscription_id", "device_id"],
            ["push_subscriptions.subscription_id", "push_subscriptions.device_id"],
            name="fk_notif_deliveries_sub_device",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'retry', 'delivered', 'failed', 'gone')",
            name="ck_notif_deliveries_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1",
            name="ck_notif_deliveries_attempts",
        ),
        CheckConstraint(
            "(status != 'processing') OR (locked_by IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_notif_deliveries_lock",
        ),
        CheckConstraint(
            "(status != 'delivered' OR delivered_at IS NOT NULL) AND (status NOT IN ('failed', 'gone') OR failed_at IS NOT NULL)",
            name="ck_notif_deliveries_terminal",
        ),
        Index("ix_notif_deliveries_claim", "status", "next_retry_at"),
        Index("ix_notif_deliveries_lease", "status", "lease_expires_at"),
        Index("ix_notif_deliveries_subscription", "subscription_id", text("created_at DESC")),
        {"comment": "多设备 Web Push 外呼投递任务状态追踪表（带租约与复合外键约束）"},
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, comment="投递任务唯一标识（UUID）"
    )
    notification_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("notifications.id", ondelete="CASCADE"),
        nullable=False,
        comment="关联通知主实体唯一标识（外键级联删除）",
    )
    subscription_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="目标推送订阅凭据标识（与 device_id 组成复合外键）"
    )
    device_id: Mapped[str] = mapped_column(
        String(36), nullable=False, comment="目标设备唯一标识（与 subscription_id 组成复合外键保证设备强一致）"
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
        comment="投递状态（pending/processing/retry/delivered/failed/gone）",
    )
    locked_by: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="认领该投递任务的 Worker 协程标识"
    )
    locked_at: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="Worker 认领加锁时间戳（UTC ISO 8601）"
    )
    lease_expires_at: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="投递租约过期绝对时间戳（UTC ISO 8601，超时可被其他 Worker 接管）"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0", comment="已尝试外呼次数"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=4, server_default="4", comment="最大允许外呼次数（1次首发+3次重试，默认 4）"
    )
    next_retry_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="下次可被扫描尝试外呼的时间戳（UTC ISO 8601）"
    )
    last_http_status: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="本任务最近一次外呼获得的 HTTP 响应状态码（用于连续 404 判断，非 404 会覆盖清零）"
    )
    last_error: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="最近一次外呼失败的异常摘要（脱敏截断）"
    )
    delivered_at: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="网关外呼成功送达时间戳（UTC ISO 8601）"
    )
    failed_at: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, comment="外呼终态失败或失效（failed/gone）时间戳（UTC ISO 8601）"
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="投递记录创建时间（UTC ISO 8601）"
    )
    updated_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="最后更新时间（UTC ISO 8601）"
    )


class NotificationChangeModel(Base):
    """持久化通知中心变更流水表（单调自增游标，支撑 SSE 增量对齐与断线补流）。"""

    __tablename__ = "notification_changes"
    __table_args__ = (
        CheckConstraint(
            "change_type IN ('created', 'updated', 'read', 'read_all', 'purged')",
            name="ck_notif_changes_type",
        ),
        Index("ix_notif_changes_project_seq", "project_id", "change_seq"),
        {"comment": "持久化通知中心变更流表（单调递增游标，支撑 SSE 增量对齐与断线补流）"},
    )

    change_seq: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="全局单调严格递增变更序列号（客户端 SSE Last-Event-ID 游标）",
    )
    project_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        comment="归属项目唯一标识（可空，项目删除时级联清理，NULL 表示全局系统通知或项目整体清理通知）",
    )
    change_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="变更类型（created/updated/read/read_all/purged）",
    )
    notification_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        nullable=True,
        comment="关联通知唯一标识（可空；有意不设外键以确保通知物理归档清理后变更流仍可保留以便补流）",
    )
    payload_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="{}",
        server_default="{}",
        comment="脱敏结构化变更快照与未读总数快照（JSON 格式）",
    )
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="变更产生时间戳（UTC ISO 8601）"
    )

