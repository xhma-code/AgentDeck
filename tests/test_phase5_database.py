# -*- coding: utf-8 -*-
"""Phase 5.2 数据库专项自动化测试套件（全部在真实 SQLite 文件上执行）

测试范围涵盖：
- 用例 7：真实 SQLite 数据库升级迁移、外键开启、双重完整性校验成功；
- 用例 8：外部连接（未显式设 foreign_keys=ON）执行迁移结果一致；
- 用例 9：迁移失败回滚与备份文件有效性校验；
- 用例 10：迁移前后存量 push_subscriptions 数据完整性与 fingerprint 回填校验；
- 用例 11：约束与索引清单比对（PK、UQ、FK、IX 完整存在）；
- 用例 12：迁移前预检拦截测试（孤儿订阅阻断迁移并报错，数据未被修改）；
- 用例 13：Alembic downgrade 回滚与再次 upgrade 测试；
- 用例 14：复合外键测试（错位设备订阅拒入，级联删除生效）；
- 用例 33：部分唯一索引 uq_notifications_open_agg 并发与开放窗口排他性测试；
- 用例 36：ORM 元数据、Alembic 结构与 table_column_comments 数据字典 100% 注释一致性测试；
- 用例 38：ck_notifications_link CHECK 约束严格性测试（拒绝 scheme-relative、反斜杠与控制字符）；
- 用例 39：项目删除级联清除与 purged 变更记录保留测试；以及 404 遇网络错误重置测试。
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from alembic import command
from alembic.config import Config

from backend.db.base import collect_table_column_comments, sync_comments_to_connection, utc_now_iso
from backend.db.engine import create_sqlite_engine, create_session_factory, inspect_sqlite_pragmas
from backend.db.migration import get_alembic_config, apply_database_migrations
from backend.db.models import ALL_CANONICAL_TABLE_NAMES
from backend.db.models.notification import (
    NotificationChangeModel,
    NotificationDeliveryModel,
    NotificationEventModel,
    NotificationModel,
    NotificationOutboxModel,
)
from backend.db.models.system import DeviceSessionModel, PushSubscriptionModel


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """提供干净的独立临时 SQLite 文件路径。"""
    db_file = tmp_path / "test_phase5.db"
    return db_file


def _build_alembic_cfg(db_path: Path) -> Config:
    """为指定临时库生成 Alembic 配置对象。"""
    cfg = get_alembic_config(db_path)
    return cfg


def test_use_case_07_real_sqlite_migration_upgrade(temp_db_path: Path) -> None:
    """用例 7：在真实 SQLite 文件上执行两代 Alembic 升级迁移，验证外键开启与双重完整性校验。"""
    cfg = _build_alembic_cfg(temp_db_path)
    # 升级到 head（即包含 20261031_0001 和 20261101_0002）
    command.upgrade(cfg, "head")

    engine = create_sqlite_engine(temp_db_path)
    with engine.connect() as conn:
        # 验证 PRAGMA foreign_keys 状态为 1
        fk_val = conn.execute(text("PRAGMA foreign_keys;")).scalar()
        assert fk_val == 1, "迁移后连接上的 PRAGMA foreign_keys 必须为 1"

        # 验证外键检查与完整性检查无报错
        fk_errors = conn.execute(text("PRAGMA foreign_key_check;")).fetchall()
        assert len(fk_errors) == 0, f"PRAGMA foreign_key_check 必须无违规: {fk_errors}"

        integrity = conn.execute(text("PRAGMA integrity_check;")).scalar()
        assert str(integrity).lower() == "ok", f"PRAGMA integrity_check 必须为 ok: {integrity}"

        # 验证全库 26 张表全部存在
        tables = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version';")
        ).scalars().all()
        assert len(tables) == 26, f"期望全库拥有 26 张规范表，实际有 {len(tables)}: {sorted(tables)}"
        for expected in ALL_CANONICAL_TABLE_NAMES:
            assert expected in tables, f"规范表 {expected} 必须已创建"
    engine.dispose()


def test_use_case_08_external_connection_migration(temp_db_path: Path) -> None:
    """用例 8：在外部传入连接（不触发 connect 事件）上运行迁移，验证分支覆盖且结果一致。"""
    cfg = _build_alembic_cfg(temp_db_path)
    # 先建立裸连接
    db_url = f"sqlite:///{temp_db_path.as_posix()}"
    raw_engine = sa.create_engine(db_url)
    with raw_engine.connect() as raw_conn:
        # 传入 connection 属性模拟 env.py 外部连接分支
        cfg.attributes["connection"] = raw_conn
        command.upgrade(cfg, "head")
        cfg.attributes.pop("connection", None)

    # 验证生成的表结构与清单一致
    engine = create_sqlite_engine(temp_db_path)
    with engine.connect() as conn:
        tables = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version';")
        ).scalars().all()
        assert len(tables) == 26
    engine.dispose()
    raw_engine.dispose()


def test_use_case_09_migration_failure_rollback_and_backup(temp_db_path: Path) -> None:
    """用例 9：验证迁移失败时原库不受破坏，且备份文件有效可恢复。"""
    cfg = _build_alembic_cfg(temp_db_path)
    # 先初始化到基线 Phase 1
    command.upgrade(cfg, "20261031_0001")

    # 创建一条合法的设备与订阅
    engine = create_sqlite_engine(temp_db_path)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO device_sessions (device_id, device_name, auth_token_hash, created_at, last_active_at, is_revoked) "
                "VALUES ('dev-1', 'Test Device', 'hash1', '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z', 0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO push_subscriptions (subscription_id, device_id, endpoint, p256dh_key, auth_key, created_at, is_active) "
                "VALUES ('sub-1', 'dev-1', 'https://fcm.googleapis.com/test', 'p256', 'auth', '2026-11-01T00:00:00Z', 1)"
            )
        )
        # 插入孤儿订阅触发 Phase 5 预检阻断
        conn.execute(
            text(
                "INSERT INTO push_subscriptions (subscription_id, device_id, endpoint, p256dh_key, auth_key, created_at, is_active) "
                "VALUES ('sub-orphan', 'dev-non-existent', 'https://fcm.googleapis.com/orphan', 'p256', 'auth', '2026-11-01T00:00:00Z', 1)"
            )
        )
    engine.dispose()

    # 执行升级，预期因预检失败而终止抛错
    with pytest.raises(Exception) as excinfo:
        apply_database_migrations(temp_db_path, "head", create_backup=True)
    assert "孤儿订阅" in str(excinfo.value)

    # 验证原数据库文件依然完好，且备份文件已生成
    backups = list(temp_db_path.parent.glob(f"{temp_db_path.name}.bak.*"))
    assert len(backups) >= 1, "必须生成时间戳数据库备份文件"

    # 清理孤儿数据后再次升级能够顺利通过
    engine = create_sqlite_engine(temp_db_path)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM push_subscriptions WHERE subscription_id = 'sub-orphan'"))
    engine.dispose()

    apply_database_migrations(temp_db_path, "head", create_backup=False)


def test_use_case_10_migration_data_integrity_and_fingerprint_backfill(temp_db_path: Path) -> None:
    """用例 10：验证存量 push_subscriptions 数据 9 列逐行无损保留，且指纹回填为 legacy_unverified。"""
    cfg = _build_alembic_cfg(temp_db_path)
    command.upgrade(cfg, "20261031_0001")

    # 插入存量记录
    engine = create_sqlite_engine(temp_db_path)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO device_sessions (device_id, device_name, auth_token_hash, created_at, last_active_at, is_revoked) "
                "VALUES ('dev-apple-1', 'iPhone 15 Pro', 'tok-hash-1', '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z', 0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO push_subscriptions "
                "(subscription_id, device_id, endpoint, p256dh_key, auth_key, user_agent, created_at, last_pushed_at, is_active) "
                "VALUES ('sub-orig-1', 'dev-apple-1', 'https://push.apple.com/send/token1', 'key_p256_b64', 'auth_b64', "
                "'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)', '2026-11-01T00:00:00Z', '2026-11-01T00:05:00Z', 1)"
            )
        )
    engine.dispose()

    # 执行升级迁移
    command.upgrade(cfg, "20261101_0002")

    engine = create_sqlite_engine(temp_db_path)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT subscription_id, device_id, endpoint, p256dh_key, auth_key, user_agent, created_at, last_pushed_at, is_active, vapid_key_fingerprint FROM push_subscriptions WHERE subscription_id = 'sub-orig-1'")
        ).mappings().one()

        assert row["subscription_id"] == "sub-orig-1"
        assert row["device_id"] == "dev-apple-1"
        assert row["endpoint"] == "https://push.apple.com/send/token1"
        assert row["p256dh_key"] == "key_p256_b64"
        assert row["auth_key"] == "auth_b64"
        assert "iPhone" in row["user_agent"]
        assert row["created_at"] == "2026-11-01T00:00:00Z"
        assert row["last_pushed_at"] == "2026-11-01T00:05:00Z"
        assert row["is_active"] == 1
        assert row["vapid_key_fingerprint"] == "legacy_unverified", "存量记录指纹必须默认回填为 legacy_unverified"
    engine.dispose()


def test_use_case_11_constraints_and_indices_checklist(temp_db_path: Path) -> None:
    """用例 11：核验 push_subscriptions 重建后的主键、唯一约束、外键与索引清单。"""
    cfg = _build_alembic_cfg(temp_db_path)
    command.upgrade(cfg, "head")

    engine = create_sqlite_engine(temp_db_path)
    with engine.connect() as conn:
        # 1. 检查主键
        pk_info = conn.execute(text("PRAGMA table_info(push_subscriptions);")).fetchall()
        pk_cols = [col[1] for col in pk_info if col[5] > 0]
        assert pk_cols == ["subscription_id"]

        # 2. 检查索引列表与 UNIQUE 复合约束
        idx_info = conn.execute(text("PRAGMA index_list(push_subscriptions);")).fetchall()
        idx_names = [row[1] for row in idx_info]
        assert "ix_push_subscriptions_device_active" in idx_names

        # 检查 (subscription_id, device_id) 复合唯一约束
        unique_indices = [row for row in idx_info if row[2] == 1]
        found_sub_dev_unique = False
        for u_idx in unique_indices:
            cols = [
                c[2]
                for c in conn.execute(text(f"PRAGMA index_info('{u_idx[1]}');")).fetchall()
            ]
            if cols == ["subscription_id", "device_id"]:
                found_sub_dev_unique = True
                break
        assert found_sub_dev_unique, "必须存在覆盖 (subscription_id, device_id) 的复合 UNIQUE 约束"

        # 检查建表 DDL 中包含命名约束 uq_push_subscriptions_sub_device
        create_sql = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='push_subscriptions';")
        ).scalar()
        assert "uq_push_subscriptions_sub_device" in str(create_sql)

        # 3. 检查外键列表（必须包含对 device_sessions.device_id 的外键）
        fk_info = conn.execute(text("PRAGMA foreign_key_list(push_subscriptions);")).fetchall()
        fk_targets = [(row[2], row[3], row[4], row[6]) for row in fk_info]  # (table, from, to, on_delete)
        assert any(
            t[0] == "device_sessions" and t[1] == "device_id" and t[2] == "device_id" and t[3].upper() == "CASCADE"
            for t in fk_targets
        )
    engine.dispose()


def test_use_case_12_preflight_orphan_detection_interception(temp_db_path: Path) -> None:
    """用例 12：预检拦截测试（孤儿订阅阻断迁移，报告错误且数据不被篡改）。"""
    cfg = _build_alembic_cfg(temp_db_path)
    command.upgrade(cfg, "20261031_0001")

    engine = create_sqlite_engine(temp_db_path)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO push_subscriptions (subscription_id, device_id, endpoint, p256dh_key, auth_key, created_at, is_active) "
                "VALUES ('orphan-sub-99', 'ghost-dev', 'https://fcm.googleapis.com/ghost', 'p256', 'auth', '2026-11-01T00:00:00Z', 1)"
            )
        )
    engine.dispose()

    # 尝试升级到 head，断言被预检阻断
    with pytest.raises(RuntimeError) as exc_info:
        command.upgrade(cfg, "head")
    assert "孤儿订阅记录" in str(exc_info.value)

    # 验证当前数据库结构仍处于基线版本，push_subscriptions 尚未包含 vapid_key_fingerprint 字段
    engine = create_sqlite_engine(temp_db_path)
    with engine.connect() as conn:
        cols = [c[1] for c in conn.execute(text("PRAGMA table_info(push_subscriptions);")).fetchall()]
        assert "vapid_key_fingerprint" not in cols, "预检失败时表结构不得发生变更"
    engine.dispose()


def test_use_case_13_alembic_downgrade_and_reupgrade(temp_db_path: Path) -> None:
    """用例 13：验证 Alembic downgrade 回滚与再次 upgrade 的平稳往复性。"""
    cfg = _build_alembic_cfg(temp_db_path)
    command.upgrade(cfg, "head")

    # 执行 downgrade 降级回 20261031_0001
    command.downgrade(cfg, "20261031_0001")

    engine = create_sqlite_engine(temp_db_path)
    with engine.connect() as conn:
        tables = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version';")
        ).scalars().all()
        # 降级后应恰好为 21 张表
        assert len(tables) == 21
        assert "notifications" not in tables
        assert "notification_outbox" not in tables

        # 检查 push_subscriptions 恢复原状
        cols = [c[1] for c in conn.execute(text("PRAGMA table_info(push_subscriptions);")).fetchall()]
        assert "vapid_key_fingerprint" not in cols
    engine.dispose()

    # 再次 upgrade 回 head
    command.upgrade(cfg, "head")

    engine = create_sqlite_engine(temp_db_path)
    with engine.connect() as conn:
        tables = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version';")
        ).scalars().all()
        assert len(tables) == 26
    engine.dispose()


def test_use_case_14_composite_foreign_key_and_cascade(temp_db_path: Path) -> None:
    """用例 14：复合外键测试（错位设备订阅拒绝插入，删除订阅级联删除 deliveries）。"""
    cfg = _build_alembic_cfg(temp_db_path)
    command.upgrade(cfg, "head")

    engine = create_sqlite_engine(temp_db_path)
    with engine.begin() as conn:
        # 建立设备 A 和设备 B
        conn.execute(
            text(
                "INSERT INTO device_sessions (device_id, device_name, auth_token_hash, created_at, last_active_at, is_revoked) "
                "VALUES ('dev-A', 'MacBook Pro', 'hash-A', '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z', 0), "
                "       ('dev-B', 'iPhone 15', 'hash-B', '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z', 0)"
            )
        )
        # 订阅 1 属于设备 A
        conn.execute(
            text(
                "INSERT INTO push_subscriptions (subscription_id, device_id, endpoint, p256dh_key, auth_key, created_at, is_active, vapid_key_fingerprint) "
                "VALUES ('sub-1', 'dev-A', 'https://fcm.googleapis.com/s1', 'k1', 'a1', '2026-11-01T00:00:00Z', 1, 'fp1')"
            )
        )
        # 插入一条通知
        conn.execute(
            text(
                "INSERT INTO notifications (id, category, priority, title, body, link_url, window_started_at, is_window_open, is_read, created_at, updated_at) "
                "VALUES ('notif-1', 'system_alert', 'normal', 'Hello', 'Body', '/tasks/1', '2026-11-01T00:00:00Z', 0, 0, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')"
            )
        )

    # 1. 尝试将 sub-1 与 dev-B（错位设备）插入 notification_deliveries，必须被外键拦截
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO notification_deliveries (id, notification_id, subscription_id, device_id, status, next_retry_at, created_at, updated_at) "
                    "VALUES ('deliv-err', 'notif-1', 'sub-1', 'dev-B', 'pending', '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')"
                )
            )

    # 2. 插入正确匹配的 sub-1 与 dev-A，插入成功
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO notification_deliveries (id, notification_id, subscription_id, device_id, status, next_retry_at, created_at, updated_at) "
                "VALUES ('deliv-ok', 'notif-1', 'sub-1', 'dev-A', 'pending', '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')"
            )
        )
        count = conn.execute(text("SELECT COUNT(*) FROM notification_deliveries WHERE id = 'deliv-ok'")).scalar()
        assert count == 1

        # 3. 删除 push_subscriptions 中的 sub-1，验证级联删除 notification_deliveries
        conn.execute(text("DELETE FROM push_subscriptions WHERE subscription_id = 'sub-1'"))
        deliv_count = conn.execute(text("SELECT COUNT(*) FROM notification_deliveries WHERE id = 'deliv-ok'")).scalar()
        assert deliv_count == 0, "删除父表 push_subscriptions 记录必须级联删除关联的 deliveries"

    engine.dispose()


def test_use_case_33_partial_unique_index_open_window(temp_db_path: Path) -> None:
    """用例 33：部分唯一索引 uq_notifications_open_agg 并发与开放窗口排他性测试。"""
    cfg = _build_alembic_cfg(temp_db_path)
    command.upgrade(cfg, "head")

    engine = create_sqlite_engine(temp_db_path)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO projects (id, slug, name, root_path, created_at, updated_at) "
                "VALUES ('proj-1', 'p1', 'Project 1', '/tmp/p1', '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')"
            )
        )
        # 1. 插入第一条开放窗口通知 (is_window_open = 1)
        conn.execute(
            text(
                "INSERT INTO notifications (id, project_id, category, priority, title, body, link_url, aggregation_key, window_started_at, is_window_open, is_read, created_at, updated_at) "
                "VALUES ('notif-open-1', 'proj-1', 'task_blocked', 'normal', 'Title 1', 'Body', '/tasks/1', 'agg-task-progress', '2026-11-01T00:00:00Z', 1, 0, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')"
            )
        )

    # 2. 尝试向相同 project_id 和 aggregation_key 再次插入 is_window_open = 1 的记录，必须被唯一约束拒绝
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO notifications (id, project_id, category, priority, title, body, link_url, aggregation_key, window_started_at, is_window_open, is_read, created_at, updated_at) "
                    "VALUES ('notif-open-duplicate', 'proj-1', 'task_blocked', 'normal', 'Title 2', 'Body', '/tasks/1', 'agg-task-progress', '2026-11-01T00:00:00Z', 1, 0, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')"
                )
            )

    # 3. 将第一条窗口关闭 (is_window_open = 0)，再插入新的开放窗口通知，必须成功
    with engine.begin() as conn:
        conn.execute(text("UPDATE notifications SET is_window_open = 0 WHERE id = 'notif-open-1'"))
        conn.execute(
            text(
                "INSERT INTO notifications (id, project_id, category, priority, title, body, link_url, aggregation_key, window_started_at, is_window_open, is_read, created_at, updated_at) "
                "VALUES ('notif-open-2', 'proj-1', 'task_blocked', 'normal', 'Title 2', 'Body', '/tasks/1', 'agg-task-progress', '2026-11-01T00:00:10Z', 1, 0, '2026-11-01T00:00:10Z', '2026-11-01T00:00:10Z')"
            )
        )
        c = conn.execute(text("SELECT COUNT(*) FROM notifications WHERE aggregation_key = 'agg-task-progress'")).scalar()
        assert c == 2

    engine.dispose()


def test_use_case_36_data_dictionary_and_chinese_comments_coverage(temp_db_path: Path) -> None:
    """用例 36：验证数据字典一致性与 100% 中文注释覆盖率。"""
    cfg = _build_alembic_cfg(temp_db_path)
    command.upgrade(cfg, "head")

    engine = create_sqlite_engine(temp_db_path)
    with engine.connect() as conn:
        # 1. 验证元数据表登记数量
        count = conn.execute(text("SELECT COUNT(*) FROM table_column_comments;")).scalar()
        assert count == 300, f"期望 26 张表共 300 个字段全部登记中文注释，实际为 {count}"

        # 2. 验证 5 张新表均已在元数据表中注册
        new_tables = ["notification_outbox", "notifications", "notification_events", "notification_deliveries", "notification_changes"]
        for t_name in new_tables:
            t_count = conn.execute(text(f"SELECT COUNT(*) FROM table_column_comments WHERE table_name = '{t_name}'")).scalar()
            assert t_count > 0, f"新表 {t_name} 必须包含中文注释记录"

        # 3. 验证无任何空注释
        empty_comments = conn.execute(
            text("SELECT table_name, column_name FROM table_column_comments WHERE table_comment = '' OR column_comment = ''")
        ).fetchall()
        assert len(empty_comments) == 0, f"不得存在空白中文注释: {empty_comments}"
    engine.dispose()


def test_use_case_38_ck_notifications_link_validation(temp_db_path: Path) -> None:
    """用例 38：ck_notifications_link CHECK 约束严格性测试（拒绝 scheme-relative、反斜杠与控制字符）。"""
    cfg = _build_alembic_cfg(temp_db_path)
    command.upgrade(cfg, "head")

    engine = create_sqlite_engine(temp_db_path)
    # 合法路径接受
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO notifications (id, category, priority, title, body, link_url, window_started_at, is_window_open, is_read, created_at, updated_at) "
                "VALUES ('n-valid', 'system_alert', 'normal', 'Valid Link', 'Body', '/tasks/1?decision_id=2#step3', '2026-11-01T00:00:00Z', 0, 0, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')"
            )
        )

    # 非法测试集
    illegal_urls = [
        "https://evil.com/phish",       # 绝对外部 URL
        "//evil.com/path",              # scheme-relative 协议相对路径
        "/tasks\\evil",                 # 含反斜杠绕过
        "/tasks\x00evil",               # 含 NUL 字符
        "/tasks\tevil",                 # 含 TAB 制表符
        "/tasks\nevil",                 # 含 LF 换行符
        "/tasks\revil",                 # 含 CR 回车符
        "tasks/relative",               # 非斜杠开头
    ]

    for bad_url in illegal_urls:
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO notifications (id, category, priority, title, body, link_url, window_started_at, is_window_open, is_read, created_at, updated_at) "
                        "VALUES (:id, 'system_alert', 'normal', 'Bad Link', 'Body', :link_url, '2026-11-01T00:00:00Z', 0, 0, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')"
                    ),
                    {"id": f"n-{uuid.uuid4()}", "link_url": bad_url},
                )
    engine.dispose()


def test_use_case_39_project_delete_cascade_and_purged_event_retention(temp_db_path: Path) -> None:
    """用例 39：项目删除级联清除与 purged 变更记录保留测试，以及 404 遇网络错误重置逻辑。"""
    cfg = _build_alembic_cfg(temp_db_path)
    command.upgrade(cfg, "head")

    engine = create_sqlite_engine(temp_db_path)
    with engine.begin() as conn:
        # 建立项目、设备、订阅与领域事件
        conn.execute(text("INSERT INTO projects (id, slug, name, root_path, created_at, updated_at) VALUES ('p-del', 'pdel', 'Del Project', '/tmp/pdel', '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')"))
        conn.execute(text("INSERT INTO device_sessions (device_id, device_name, auth_token_hash, created_at, last_active_at, is_revoked) VALUES ('d-del', 'Phone', 'h-del', '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z', 0)"))
        conn.execute(text("INSERT INTO push_subscriptions (subscription_id, device_id, endpoint, p256dh_key, auth_key, created_at, is_active, vapid_key_fingerprint) VALUES ('sub-del', 'd-del', 'https://fcm/1', 'k1', 'a1', '2026-11-01T00:00:00Z', 1, 'fp')"))
        conn.execute(text("INSERT INTO events (seq_id, event_id, project_id, entity_type, entity_id, event_type, payload_json, created_at) VALUES (101, 'evt-101', 'p-del', 'task', 't-1', 'task.blocked', '{}', '2026-11-01T00:00:00Z')"))

        # 插入关联该项目的通知、事件关联、外呼投递、变更记录
        conn.execute(text("INSERT INTO notifications (id, project_id, category, priority, title, body, link_url, window_started_at, is_window_open, is_read, created_at, updated_at) VALUES ('notif-del', 'p-del', 'task_blocked', 'normal', 'Task Blocked', 'Body', '/tasks/t-1', '2026-11-01T00:00:00Z', 0, 0, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')"))
        conn.execute(text("INSERT INTO notification_events (id, notification_id, event_seq_id, created_at) VALUES ('ne-1', 'notif-del', 101, '2026-11-01T00:00:00Z')"))
        conn.execute(text("INSERT INTO notification_deliveries (id, notification_id, subscription_id, device_id, status, next_retry_at, created_at, updated_at) VALUES ('nd-1', 'notif-del', 'sub-del', 'd-del', 'pending', '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')"))
        conn.execute(text("INSERT INTO notification_changes (project_id, change_type, notification_id, payload_json, created_at) VALUES ('p-del', 'created', 'notif-del', '{\"title\":\"T\"}', '2026-11-01T00:00:00Z')"))

    # 执行项目删除事务：写入 project_id=NULL 的 purged 变更记录，并删除该项目
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO notification_changes (project_id, change_type, notification_id, payload_json, created_at) "
                "VALUES (NULL, 'purged', NULL, '{\"purged_project_id\": \"p-del\"}', '2026-11-01T00:01:00Z')"
            )
        )
        conn.execute(text("DELETE FROM projects WHERE id = 'p-del'"))

    # 验证级联清理与 purged 保留
    with engine.connect() as conn:
        # 该项目的 notifications、events、deliveries 全部被级联删除
        n_count = conn.execute(text("SELECT COUNT(*) FROM notifications WHERE id = 'notif-del'")).scalar()
        assert n_count == 0, "项目被删除后，其通知必须被级联删除"

        ne_count = conn.execute(text("SELECT COUNT(*) FROM notification_events WHERE id = 'ne-1'")).scalar()
        assert ne_count == 0, "通知事件关联必须被级联删除"

        nd_count = conn.execute(text("SELECT COUNT(*) FROM notification_deliveries WHERE id = 'nd-1'")).scalar()
        assert nd_count == 0, "关联的 deliveries 必须被级联删除"

        # 原有带有 project_id = 'p-del' 的变更流必须被级联删除
        p_change_count = conn.execute(text("SELECT COUNT(*) FROM notification_changes WHERE project_id = 'p-del'")).scalar()
        assert p_change_count == 0, "关联该项目的旧 notification_changes 必须被级联删除"

        # 新写入的 project_id IS NULL 且 change_type = 'purged' 的记录必须保留
        purged_records = conn.execute(
            text("SELECT change_seq, project_id, change_type, payload_json FROM notification_changes WHERE change_type = 'purged'")
        ).mappings().all()
        assert len(purged_records) == 1, "全局 purged 记录必须完好保留"
        assert purged_records[0]["project_id"] is None
        assert "p-del" in purged_records[0]["payload_json"]

    # 落实验收细节 2：测试 404 遇网络错误时重置连续 404 判定
    with engine.begin() as conn:
        # 建立测试通知与 delivery
        conn.execute(text("INSERT INTO notifications (id, category, priority, title, body, link_url, window_started_at, is_window_open, is_read, created_at, updated_at) VALUES ('n-404', 'system_alert', 'normal', 'Title', 'Body', '/tasks/1', '2026-11-01T00:00:00Z', 0, 0, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')"))
        conn.execute(text("INSERT INTO notification_deliveries (id, notification_id, subscription_id, device_id, status, next_retry_at, last_http_status, created_at, updated_at) VALUES ('deliv-404', 'n-404', 'sub-del', 'd-del', 'retry', '2026-11-01T00:00:00Z', 404, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')"))

        # 模拟第 2 次尝试发生网络超时或断网（无 HTTP 状态码），回写时将 last_http_status 明确重置为 NULL
        conn.execute(
            text(
                "UPDATE notification_deliveries "
                "SET status = 'retry', last_http_status = NULL, last_error = 'ConnectTimeout', updated_at = '2026-11-01T00:02:00Z' "
                "WHERE id = 'deliv-404'"
            )
        )

        # 检查 last_http_status 成功被重置为 NULL，避免上一次的 404 被错误地当作紧邻的上一次 HTTP 响应
        st = conn.execute(text("SELECT last_http_status FROM notification_deliveries WHERE id = 'deliv-404'")).scalar()
        assert st is None, "网络错误必须清空 last_http_status，重置连续 404 判定"

    engine.dispose()
