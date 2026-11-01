# -*- coding: utf-8 -*-
"""Phase 5：Web Push 与桌面通知中心基础设施迁移脚本（全库 21 扩至 26 张表）

Revision ID: 20261101_0002
Revises: 20261031_0001
Create Date: 2026-11-01 12:00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from backend.db.base import sync_comments_to_connection

revision: str = "20261101_0002"
down_revision: Union[str, None] = "20261031_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _run_preflight_checks(conn: sa.engine.Connection) -> None:
    """执行迁移前 5 项关键前置预检，任一失败立即终止迁移。"""
    # 1. 检查 device_sessions.device_id 是否为主键或存在唯一约束
    table_info = conn.execute(sa.text("PRAGMA table_info(device_sessions);")).fetchall()
    has_pk = any(row[1] == "device_id" and row[5] == 1 for row in table_info)
    if not has_pk:
        raise RuntimeError("预检失败：device_sessions.device_id 不是主键，无法建立外键引用")

    # 2. 检查 push_subscriptions 中是否存在没有对应 device_sessions 的孤儿订阅
    orphan_subs = conn.execute(
        sa.text(
            """
            SELECT ps.subscription_id, ps.device_id
            FROM push_subscriptions ps
            LEFT JOIN device_sessions ds ON ps.device_id = ds.device_id
            WHERE ds.device_id IS NULL;
            """
        )
    ).fetchall()
    if orphan_subs:
        raise RuntimeError(
            f"预检失败：发现 {len(orphan_subs)} 条没有对应 device_sessions 的孤儿订阅记录，"
            f"示例: {orphan_subs[:3]}。为保护数据完整性，已中止迁移，请核对清理历史数据后重试。"
        )

    # 3. 检查是否存在违反新增 (subscription_id, device_id) 复合唯一约束的重复数据
    duplicate_subs = conn.execute(
        sa.text(
            """
            SELECT subscription_id, device_id, COUNT(*) as cnt
            FROM push_subscriptions
            GROUP BY subscription_id, device_id
            HAVING COUNT(*) > 1;
            """
        )
    ).fetchall()
    if duplicate_subs:
        raise RuntimeError(
            f"预检失败：发现 {len(duplicate_subs)} 条违反 (subscription_id, device_id) 复合唯一键的重复数据，"
            f"示例: {duplicate_subs[:3]}。已中止迁移。"
        )

    # 4. 检查当前是否有其他子表通过外键直接引用了 push_subscriptions
    # （若存在子表引用，在 foreign_keys=ON 下 batch 重建会受阻）
    tables_result = conn.execute(
        sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    ).fetchall()
    for row in tables_result:
        t_name = row[0]
        fk_list = conn.execute(sa.text(f"PRAGMA foreign_key_list({t_name});")).fetchall()
        for fk in fk_list:
            if len(fk) > 2 and fk[2] == "push_subscriptions":
                raise RuntimeError(
                    f"预检失败：检测到表 '{t_name}' 存在指向 push_subscriptions 的外键引用，"
                    "请先设计专项级联迁移方案后再执行表重建。"
                )

    # 5. 检查迁移前现有外键完整性基线
    fk_check = conn.execute(sa.text("PRAGMA foreign_key_check;")).fetchall()
    if fk_check:
        raise RuntimeError(
            f"预检失败：当前数据库存在历史外键违规记录: {fk_check}，已中止迁移。"
        )


def upgrade() -> None:
    """执行 Phase 5 数据库升级迁移。"""
    conn = op.get_bind()

    # 0. 迁移前前置预检
    _run_preflight_checks(conn)

    # 1. 重建 push_subscriptions 表：扩充 vapid_key_fingerprint，添加复合唯一键与设备外键
    with op.batch_alter_table("push_subscriptions", schema=None, recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column(
                "vapid_key_fingerprint",
                sa.String(length=64),
                nullable=False,
                server_default="legacy_unverified",
                comment="订阅绑定的 VAPID 公钥 SHA-256 指纹（存量为 legacy_unverified）",
            )
        )
        batch_op.create_unique_constraint(
            "uq_push_subscriptions_sub_device",
            ["subscription_id", "device_id"],
        )
        batch_op.create_foreign_key(
            "fk_push_subscriptions_device_id_device_sessions",
            "device_sessions",
            ["device_id"],
            ["device_id"],
            ondelete="CASCADE",
        )

    # 2. 创建第 22 张表：notification_outbox（业务解耦与可靠分流表）
    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.String(length=36), nullable=False, comment="Outbox 任务全局唯一标识（UUID）"),
        sa.Column("event_seq_id", sa.Integer(), nullable=False, comment="关联的底层领域审计事件序号（外键，全局唯一防重复）"),
        sa.Column("event_type", sa.String(length=80), nullable=False, comment="冗余领域事件类型（便于分流过滤）"),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}", comment="脱敏后的领域事件载荷（JSON 字符串）"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending", comment="Outbox 处理状态（pending/processing/retry/processed/failed）"),
        sa.Column("locked_by", sa.String(length=64), nullable=True, comment="认领该任务的 Dispatcher 进程/协程标识"),
        sa.Column("locked_at", sa.String(length=40), nullable=True, comment="认领加锁时间戳（UTC ISO 8601）"),
        sa.Column("lease_expires_at", sa.String(length=40), nullable=True, comment="任务租约过期绝对时间戳（UTC ISO 8601，超时可被接管）"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0", comment="已尝试派发次数"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5", comment="最大允许尝试次数（默认 5）"),
        sa.Column("next_retry_at", sa.String(length=40), nullable=False, comment="下次可被扫描认领的时间戳（UTC ISO 8601）"),
        sa.Column("last_error", sa.Text(), nullable=True, comment="最近一次派发异常原因与错误摘要（脱敏截断）"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="Outbox 任务创建时间（UTC ISO 8601，与业务同事务）"),
        sa.Column("updated_at", sa.String(length=40), nullable=False, comment="最后更新时间（UTC ISO 8601）"),
        sa.Column("processed_at", sa.String(length=40), nullable=True, comment="派发成功终态完成时间（UTC ISO 8601）"),
        sa.Column("failed_at", sa.String(length=40), nullable=True, comment="派发达到最大重试上限后的熔断失败时间（UTC ISO 8601）"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'retry', 'processed', 'failed')",
            name="ck_notif_outbox_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1",
            name="ck_notif_outbox_attempts",
        ),
        sa.CheckConstraint(
            "(status != 'processing') OR (locked_by IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_notif_outbox_lock",
        ),
        sa.CheckConstraint(
            "(status != 'processed' OR processed_at IS NOT NULL) AND (status != 'failed' OR failed_at IS NOT NULL)",
            name="ck_notif_outbox_terminal",
        ),
        sa.ForeignKeyConstraint(["event_seq_id"], ["events.seq_id"], name="fk_notif_outbox_event_seq_id_events", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_notification_outbox"),
        sa.UniqueConstraint("event_seq_id", name="uq_notification_outbox_event_seq_id"),
        comment="业务事件到通知派发解耦的可靠事务 Outbox 任务表",
    )
    op.create_index("ix_notif_outbox_claim", "notification_outbox", ["status", "next_retry_at"])
    op.create_index("ix_notif_outbox_lease", "notification_outbox", ["status", "lease_expires_at"])

    # 3. 创建第 23 张表：notifications（通知主台账与展示实体表）
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=36), nullable=False, comment="通知唯一标识（UUID）"),
        sa.Column("project_id", sa.String(length=36), nullable=True, comment="归属项目唯一标识（可空，项目删除时级联清除，NULL 表示全局系统通知）"),
        sa.Column("category", sa.String(length=32), nullable=False, comment="通知业务分类（decision_required/task_blocked/task_completed/execution_failed/system_alert）"),
        sa.Column("priority", sa.String(length=20), nullable=False, server_default="normal", comment="通知优先级（urgent/high/normal/low）"),
        sa.Column("title", sa.String(length=240), nullable=False, comment="通知展示标题（纯文本简述）"),
        sa.Column("body", sa.Text(), nullable=False, server_default="", comment="通知正文内容或聚合更新摘要"),
        sa.Column("link_url", sa.String(length=500), nullable=False, server_default="/", comment="点击跳转的受控站内相对路径（严格以单斜杠开头，拒绝外部或协议相对路径）"),
        sa.Column("target_entity_type", sa.String(length=40), nullable=True, comment="关联目标实体类型（如 decision/task/step/execution）"),
        sa.Column("target_entity_id", sa.String(length=64), nullable=True, comment="关联目标实体唯一标识"),
        sa.Column("aggregation_key", sa.String(length=120), nullable=True, comment="降噪聚合分组键（NULL 表示独立通知不折叠）"),
        sa.Column("aggregated_count", sa.Integer(), nullable=False, server_default="1", comment="当前通知已聚合折叠的事件条数（1-20）"),
        sa.Column("window_started_at", sa.String(length=40), nullable=False, comment="聚合时间窗口起始时间戳（UTC ISO 8601）"),
        sa.Column("is_window_open", sa.Integer(), nullable=False, server_default="1", comment="聚合窗口是否依然开放允许合并（1：开放，0：已关闭，配合唯一索引杜绝并发重复创建）"),
        sa.Column("is_read", sa.Integer(), nullable=False, server_default="0", comment="全局已读状态（0：未读，1：已读）"),
        sa.Column("read_at", sa.String(length=40), nullable=True, comment="用户标记已读时间戳（UTC ISO 8601）"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="通知创建时间（UTC ISO 8601）"),
        sa.Column("updated_at", sa.String(length=40), nullable=False, comment="通知最后更新或聚合时间（UTC ISO 8601）"),
        sa.CheckConstraint(
            "category IN ('decision_required', 'task_blocked', 'task_completed', 'execution_failed', 'system_alert')",
            name="ck_notifications_category",
        ),
        sa.CheckConstraint(
            "priority IN ('urgent', 'high', 'normal', 'low')",
            name="ck_notifications_priority",
        ),
        sa.CheckConstraint(
            "aggregated_count BETWEEN 1 AND 20",
            name="ck_notifications_count",
        ),
        sa.CheckConstraint(
            "is_read IN (0, 1) AND is_window_open IN (0, 1)",
            name="ck_notifications_flags",
        ),
        sa.CheckConstraint(
            "(is_read = 0) OR (read_at IS NOT NULL)",
            name="ck_notifications_read",
        ),
        sa.CheckConstraint(
            "link_url LIKE '/%' AND link_url NOT LIKE '//%' AND instr(link_url, char(92)) = 0 AND instr(link_url, char(0)) = 0 AND instr(link_url, char(9)) = 0 AND instr(link_url, char(10)) = 0 AND instr(link_url, char(13)) = 0",
            name="ck_notifications_link",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_notifications_project_id_projects", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
        comment="单用户全局通知中心主实体表（支持时间窗口聚合与多端同步已读）",
    )
    op.create_index("ix_notifications_cursor", "notifications", [sa.text("created_at DESC"), sa.text("id DESC")])
    op.create_index("ix_notifications_project_cursor", "notifications", ["project_id", sa.text("created_at DESC"), sa.text("id DESC")])
    op.create_index("ix_notifications_unread", "notifications", ["is_read", sa.text("created_at DESC")])
    op.create_index(
        "uq_notifications_open_agg",
        "notifications",
        [sa.text("coalesce(project_id, '')"), "aggregation_key"],
        unique=True,
        sqlite_where=sa.text("is_window_open = 1 AND aggregation_key IS NOT NULL"),
    )

    # 4. 创建第 24 张表：notification_events（原始事件溯源与防重关联表）
    op.create_table(
        "notification_events",
        sa.Column("id", sa.String(length=36), nullable=False, comment="关联记录唯一标识（UUID）"),
        sa.Column("notification_id", sa.String(length=36), nullable=False, comment="关联通知主表唯一标识（外键级联删除）"),
        sa.Column("event_seq_id", sa.Integer(), nullable=False, comment="关联底层领域事件序号（外键，全局唯一防重复关联）"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="关联创建时间戳（UTC ISO 8601）"),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], name="fk_notification_events_notification_id_notifications", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_seq_id"], ["events.seq_id"], name="fk_notification_events_event_seq_id_events", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_notification_events"),
        sa.UniqueConstraint("event_seq_id", name="uq_notification_events_event_seq_id"),
        comment="通知中心与原始领域事件的关联溯源表（唯一键防重复统计）",
    )
    op.create_index("ix_notif_events_notification", "notification_events", ["notification_id"])

    # 5. 创建第 25 张表：notification_deliveries（多设备外呼状态机与租约表）
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.String(length=36), nullable=False, comment="投递任务唯一标识（UUID）"),
        sa.Column("notification_id", sa.String(length=36), nullable=False, comment="关联通知主实体唯一标识（外键级联删除）"),
        sa.Column("subscription_id", sa.String(length=64), nullable=False, comment="目标推送订阅凭据标识（与 device_id 组成复合外键）"),
        sa.Column("device_id", sa.String(length=36), nullable=False, comment="目标设备唯一标识（与 subscription_id 组成复合外键保证设备强一致）"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending", comment="投递状态（pending/processing/retry/delivered/failed/gone）"),
        sa.Column("locked_by", sa.String(length=64), nullable=True, comment="认领该投递任务的 Worker 协程标识"),
        sa.Column("locked_at", sa.String(length=40), nullable=True, comment="Worker 认领加锁时间戳（UTC ISO 8601）"),
        sa.Column("lease_expires_at", sa.String(length=40), nullable=True, comment="投递租约过期绝对时间戳（UTC ISO 8601，超时可被其他 Worker 接管）"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0", comment="已尝试外呼次数"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="4", comment="最大允许外呼次数（1次首发+3次重试，默认 4）"),
        sa.Column("next_retry_at", sa.String(length=40), nullable=False, comment="下次可被扫描尝试外呼的时间戳（UTC ISO 8601）"),
        sa.Column("last_http_status", sa.Integer(), nullable=True, comment="本任务最近一次外呼获得的 HTTP 响应状态码（用于连续 404 判断，非 404 会覆盖清零）"),
        sa.Column("last_error", sa.Text(), nullable=True, comment="最近一次外呼失败的异常摘要（脱敏截断）"),
        sa.Column("delivered_at", sa.String(length=40), nullable=True, comment="网关外呼成功送达时间戳（UTC ISO 8601）"),
        sa.Column("failed_at", sa.String(length=40), nullable=True, comment="外呼终态失败或失效（failed/gone）时间戳（UTC ISO 8601）"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="投递记录创建时间（UTC ISO 8601）"),
        sa.Column("updated_at", sa.String(length=40), nullable=False, comment="最后更新时间（UTC ISO 8601）"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'retry', 'delivered', 'failed', 'gone')",
            name="ck_notif_deliveries_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1",
            name="ck_notif_deliveries_attempts",
        ),
        sa.CheckConstraint(
            "(status != 'processing') OR (locked_by IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_notif_deliveries_lock",
        ),
        sa.CheckConstraint(
            "(status != 'delivered' OR delivered_at IS NOT NULL) AND (status NOT IN ('failed', 'gone') OR failed_at IS NOT NULL)",
            name="ck_notif_deliveries_terminal",
        ),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], name="fk_notif_deliveries_notification_id_notifications", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["subscription_id", "device_id"],
            ["push_subscriptions.subscription_id", "push_subscriptions.device_id"],
            name="fk_notif_deliveries_sub_device",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notification_deliveries"),
        sa.UniqueConstraint("notification_id", "subscription_id", name="uq_notif_deliveries_notif_sub"),
        comment="多设备 Web Push 外呼投递任务状态追踪表（带租约与复合外键约束）",
    )
    op.create_index("ix_notif_deliveries_claim", "notification_deliveries", ["status", "next_retry_at"])
    op.create_index("ix_notif_deliveries_lease", "notification_deliveries", ["status", "lease_expires_at"])
    op.create_index("ix_notif_deliveries_subscription", "notification_deliveries", ["subscription_id", sa.text("created_at DESC")])

    # 6. 创建第 26 张表：notification_changes（持久化变更流表，支持 SSE 断线补流）
    op.create_table(
        "notification_changes",
        sa.Column("change_seq", sa.Integer(), autoincrement=True, nullable=False, comment="全局单调严格递增变更序列号（客户端 SSE Last-Event-ID 游标）"),
        sa.Column("project_id", sa.String(length=36), nullable=True, comment="归属项目唯一标识（可空，项目删除时级联清理，NULL 表示全局系统通知或项目整体清理通知）"),
        sa.Column("change_type", sa.String(length=32), nullable=False, comment="变更类型（created/updated/read/read_all/purged）"),
        sa.Column("notification_id", sa.String(length=36), nullable=True, comment="关联通知唯一标识（可空；有意不设外键以确保通知物理归档清理后变更流仍可保留以便补流）"),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}", comment="脱敏结构化变更快照与未读总数快照（JSON 格式）"),
        sa.Column("created_at", sa.String(length=40), nullable=False, comment="变更产生时间戳（UTC ISO 8601）"),
        sa.CheckConstraint(
            "change_type IN ('created', 'updated', 'read', 'read_all', 'purged')",
            name="ck_notif_changes_type",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_notif_changes_project_id_projects", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("change_seq", name="pk_notification_changes"),
        comment="持久化通知中心变更流表（单调递增游标，支撑 SSE 增量对齐与断线补流）",
    )
    op.create_index("ix_notif_changes_project_seq", "notification_changes", ["project_id", "change_seq"])

    # 7. 全表全字段中文注释元数据同步登记（同步覆盖率 100%）
    sync_comments_to_connection(conn)

    # 8. 迁移后完整性与外键核验
    fk_errors = conn.execute(sa.text("PRAGMA foreign_key_check;")).fetchall()
    if fk_errors:
        raise RuntimeError(f"Alembic 迁移后 PRAGMA foreign_key_check 失败: {fk_errors}")
    integrity = conn.execute(sa.text("PRAGMA integrity_check;")).scalar()
    if str(integrity).lower() != "ok":
        raise RuntimeError(f"Alembic 迁移后 PRAGMA integrity_check 失败: {integrity}")


def downgrade() -> None:
    """逆序回滚 Phase 5 数据库变更。"""
    conn = op.get_bind()

    # 1. 逆序删除 5 张新增表（先删子表，后删父表）
    op.drop_table("notification_changes")
    op.drop_table("notification_deliveries")
    op.drop_table("notification_events")
    op.drop_table("notifications")
    op.drop_table("notification_outbox")

    # 2. 还原 push_subscriptions 表结构（移除新增约束与字段）
    with op.batch_alter_table("push_subscriptions", schema=None, recreate="always") as batch_op:
        batch_op.drop_constraint("fk_push_subscriptions_device_id_device_sessions", type_="foreignkey")
        batch_op.drop_constraint("uq_push_subscriptions_sub_device", type_="unique")
        batch_op.drop_column("vapid_key_fingerprint")

    # 3. 重新同步中文注释元数据
    sync_comments_to_connection(conn)

    # 4. 回滚后完整性检查
    fk_errors = conn.execute(sa.text("PRAGMA foreign_key_check;")).fetchall()
    if fk_errors:
        raise RuntimeError(f"Alembic 回滚后 PRAGMA foreign_key_check 失败: {fk_errors}")
    integrity = conn.execute(sa.text("PRAGMA integrity_check;")).scalar()
    if str(integrity).lower() != "ok":
        raise RuntimeError(f"Alembic 回滚后 PRAGMA integrity_check 失败: {integrity}")

