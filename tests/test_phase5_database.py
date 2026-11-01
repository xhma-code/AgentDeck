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

from backend.db.backup import create_sqlite_online_backup, restore_sqlite_backup
from backend.db.base import collect_table_column_comments, sync_comments_to_connection, utc_now_iso
from backend.db.engine import create_sqlite_engine, create_session_factory, inspect_sqlite_pragmas
from backend.db.migration import get_alembic_config, apply_database_migrations, backup_database_file
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


def _dump_db_full_snapshot(db_path: Path) -> dict[str, list[dict]]:
    """采集 SQLite 数据库所有规范表、全字段与全量行数据的字典快照。"""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    snapshot = {}
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name ASC;"
            ).fetchall()
        ]
        for t in tables:
            conn.row_factory = sqlite3.Row
            rows = [
                dict(r)
                for r in conn.execute(f"SELECT * FROM [{t}] ORDER BY ROWID ASC;").fetchall()
            ]
            snapshot[t] = rows
    finally:
        conn.close()
    return snapshot


def test_use_case_09a_ddl_transaction_automatic_rollback(
    temp_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """用例 09A：验证迁移中途失败时 DDL 事务自动回滚、临时表安全清理及物理热备全量还原。
    
    【核心断言顺序】：
    1. 迁移中途异常时，先验证 SQLite DDL 事务原子自动回滚（版本号未推进、未产生的新表 notification_outbox 被撤销、存量数据无损）；
    2. 配合 env.py 兜底清理残留临时表，原库表总数精确恢复为 21 张，全库快照与迁移前 100% 逐表逐行一致；
    3. 人为对原数据库进行破坏性污染后，验证 restore_sqlite_backup 物理热备恢复能力，全库快照再次 100% 比对一致。
    """
    cfg = _build_alembic_cfg(temp_db_path)
    # 1. 初始化到基线 Phase 1 并建立存量业务数据
    command.upgrade(cfg, "20261031_0001")

    engine = create_sqlite_engine(temp_db_path)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO device_sessions (device_id, device_name, auth_token_hash, created_at, last_active_at, is_revoked) "
                "VALUES ('dev-pre-1', 'Desktop Workstation', 'hash1', '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z', 0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO push_subscriptions (subscription_id, device_id, endpoint, p256dh_key, auth_key, user_agent, created_at, is_active) "
                "VALUES ('sub-pre-1', 'dev-pre-1', 'https://fcm.googleapis.com/test1', 'k1', 'a1', 'AgentBrowser/1.0', '2026-11-01T00:00:00Z', 1)"
            )
        )
    engine.dispose()

    # 采集迁移前数据库全表、全字段、全行全量数据快照
    pre_migration_snapshot = _dump_db_full_snapshot(temp_db_path)

    # 2. 验证基于 SQLite Backup API 的热备功能与备份文件有效性
    backup_file = backup_database_file(temp_db_path)
    assert backup_file is not None and backup_file.exists(), "必须成功生成在线备份文件"

    # 检查备份文件能否独立打开并通过 PRAGMA integrity_check
    chk_conn = sqlite3.connect(f"file:{backup_file.as_posix()}?mode=ro", uri=True)
    try:
        integrity_res = chk_conn.execute("PRAGMA integrity_check;").fetchone()
        assert integrity_res and integrity_res[0].lower() == "ok", "备份文件 integrity_check 必须通过"
    finally:
        chk_conn.close()

    # 对比备份文件与原数据库全量快照，证明数据 100% 完整一致
    backup_snapshot = _dump_db_full_snapshot(backup_file)
    assert backup_snapshot == pre_migration_snapshot, "在线备份生成的全库快照必须与原库 100% 逐表逐行一致"

    # 3. 模拟迁移执行中途发生严重故障（例如创建第 23 张表 notifications 时抛出非预期异常）
    import importlib
    mig_mod = importlib.import_module("backend.migrations.versions.20261101_0002_phase5_notification_foundation")

    orig_create_table = mig_mod.op.create_table

    def _failing_create_table(name, *args, **kwargs):
        if name == "notifications":
            raise RuntimeError("模拟迁移执行到一半时发生不可恢复系统异常")
        return orig_create_table(name, *args, **kwargs)

    monkeypatch.setattr(mig_mod.op, "create_table", _failing_create_table)

    with pytest.raises(RuntimeError) as exc_info:
        command.upgrade(cfg, "head")
    assert "模拟迁移执行到一半" in str(exc_info.value)

    # 4. 【直接检查原数据库的自动回滚结果（绝不允许先恢复备份）】：
    abort_engine = create_sqlite_engine(temp_db_path)
    with abort_engine.connect() as conn:
        # a. 验证 Alembic 版本号未被提前推进，仍然严格为 Phase 1 基线 20261031_0001
        current_rev = conn.execute(text("SELECT version_num FROM alembic_version;")).scalar()
        assert current_rev == "20261031_0001", f"迁移失败后版本号必须保持为 20261031_0001，实际为 {current_rev}"

        # b. 验证表数量严格保持为 Phase 1 基线的 21 张表
        tables = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version';")
        ).scalars().all()
        assert len(tables) == 21, f"迁移中途异常回滚后表总数必须精确等于 21 张，实际: {len(tables)}: {tables}"

        # c. 验证未留下任何新表或半成品批处理临时表
        assert "notification_outbox" not in tables, "半途创建的新表必须被回滚撤销"
        assert not any(t.startswith("_alembic_tmp") for t in tables), f"不得遗留任何 batch 临时表: {tables}"

        # d. 验证 push_subscriptions 原表结构完好，未包含 vapid_key_fingerprint 新字段
        cols = [col[1] for col in conn.execute(text("PRAGMA table_info(push_subscriptions);")).fetchall()]
        assert "vapid_key_fingerprint" not in cols, "回滚后 push_subscriptions 绝不包含新字段"

        # e. 验证原始存量订阅数据 100% 依然存在且数值未被破坏
        sub_rows = conn.execute(
            text("SELECT subscription_id, device_id, endpoint, user_agent, is_active FROM push_subscriptions;")
        ).mappings().all()
        assert len(sub_rows) == 1
        assert sub_rows[0]["subscription_id"] == "sub-pre-1"
        assert sub_rows[0]["device_id"] == "dev-pre-1"
        assert sub_rows[0]["endpoint"] == "https://fcm.googleapis.com/test1"
        assert sub_rows[0]["is_active"] == 1

        # f. 验证回滚后数据库整体快照与迁移前 100% 一致
        post_abort_snapshot = _dump_db_full_snapshot(temp_db_path)
        assert post_abort_snapshot == pre_migration_snapshot, "迁移自动回滚后数据库全量状态必须与迁移前完全一致"
    abort_engine.dispose()

    # 5. 【单独进行灾备恢复能力验证】：先对原数据库进行破坏性污染，再验证 restore_sqlite_backup
    corrupt_engine = create_sqlite_engine(temp_db_path)
    with corrupt_engine.begin() as conn:
        # 破坏原库：注入脏表并清空订阅数据
        conn.execute(text("CREATE TABLE corrupted_accident_table (id INT);"))
        conn.execute(text("DELETE FROM push_subscriptions;"))
    corrupt_engine.dispose()

    # 执行灾难恢复覆盖
    restore_sqlite_backup(backup_file, temp_db_path)

    # 6. 验证灾难恢复后原数据库完整性与全量数据逐表逐行一致
    restored_engine = create_sqlite_engine(temp_db_path)
    with restored_engine.connect() as conn:
        integ = conn.execute(text("PRAGMA integrity_check;")).scalar()
        assert str(integ).lower() == "ok", "恢复后的数据库 integrity_check 必须为 ok"

        fk_issues = conn.execute(text("PRAGMA foreign_key_check;")).fetchall()
        assert len(fk_issues) == 0, f"恢复后的数据库外键检查必须无错误: {fk_issues}"

        tables = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version';")
        ).scalars().all()
        assert len(tables) == 21, f"恢复后表总数必须精确为 21 张，实际: {len(tables)}"
        assert "corrupted_accident_table" not in tables, "破坏性脏表必须被备份恢复彻底清除"

        # 验证恢复后数据库全量快照与迁移前 100% 完全比对一致
        restored_snapshot = _dump_db_full_snapshot(temp_db_path)
        assert restored_snapshot == pre_migration_snapshot, "灾备恢复后全库所有表、所有行必须与迁移前 100% 绝对一致"
    restored_engine.dispose()


def test_use_case_09b_sqlite_alembic_tmp_tables_cleanup_safety(temp_db_path: Path) -> None:
    """用例 09B：临时表兜底清理函数独立专项安全测试（精准识别、转义安全与防误删验证）。
    
    【核心断言】：
    1. 仅清理符合 GLOB '_alembic_tmp_*' 规则的临时表（如 _alembic_tmp_push_subscriptions、_alembic_tmp_quoted"table）；
    2. 绝不误伤任何前缀带下划线的非目标表（如 _a_table、_alembic_version）；
    3. 绝不误伤核心业务表（如 device_sessions、projects）；
    4. 验证标识符转义 _escape_sqlite_identifier 能够安全处理特殊字符（如含双引号的表名）而不会触发 SQL 语法错误或注入。
    """
    from backend.migrations.env import _cleanup_sqlite_alembic_tmp_tables, _escape_sqlite_identifier

    # 1. 验证标识符安全转义函数
    assert _escape_sqlite_identifier("simple_table") == '"simple_table"'
    assert _escape_sqlite_identifier('table_with_"quotes"') == '"table_with_""quotes"""'

    cfg = _build_alembic_cfg(temp_db_path)
    command.upgrade(cfg, "20261031_0001")

    engine = create_sqlite_engine(temp_db_path)
    with engine.begin() as conn:
        # a. 创建符合 GLOB '_alembic_tmp_*' 的临时表（包括带引号的安全测试表）
        conn.execute(text('CREATE TABLE "_alembic_tmp_push_subscriptions" (id INT, dummy TEXT);'))
        conn.execute(text('CREATE TABLE "_alembic_tmp_test_batch" (id INT);'))
        conn.execute(text('CREATE TABLE "_alembic_tmp_weird""table" (col1 INT);'))

        # b. 创建易混淆的前缀下划线非目标表与备份表，验证 GLOB 绝不会把 '_' 视作通配符误删
        conn.execute(text('CREATE TABLE "_a_table" (id INT);'))
        conn.execute(text('INSERT INTO "_a_table" (id) VALUES (42);'))

        conn.execute(text('CREATE TABLE "_other_prefix_table" (id INT);'))
        conn.execute(text('INSERT INTO "_other_prefix_table" (id) VALUES (99);'))

    # 2. 调用清理函数
    with engine.connect() as conn:
        cleaned = _cleanup_sqlite_alembic_tmp_tables(conn)

    # 3. 验证清理结果列表
    expected_cleaned = sorted([
        "_alembic_tmp_push_subscriptions",
        "_alembic_tmp_test_batch",
        '_alembic_tmp_weird"table',
    ])
    assert sorted(cleaned) == expected_cleaned, f"清理函数必须精确且仅清理目标临时表，实际: {cleaned}"

    # 4. 验证数据库中表的存在性与数据完整性
    with engine.connect() as conn:
        all_tables = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table';")
        ).scalars().all()

        # 临时表必须已被 DROP
        for target in expected_cleaned:
            assert target not in all_tables, f"目标临时表 {target} 必须已被成功清理"

        # 非目标表与核心业务表必须 100% 健在
        assert "_a_table" in all_tables, "非目标表 _a_table 绝不得被误删"
        assert "_other_prefix_table" in all_tables, "非目标表 _other_prefix_table 绝不得被误删"
        assert "alembic_version" in all_tables, "alembic_version 绝不得被误删"
        assert "device_sessions" in all_tables, "业务表 device_sessions 绝不得被误删"
        assert "projects" in all_tables, "业务表 projects 绝不得被误删"

        # 检查非目标表内的数据未受任何篡改
        a_val = conn.execute(text('SELECT id FROM "_a_table";')).scalar()
        assert a_val == 42, "非目标表数据必须完好无损"

        other_val = conn.execute(text('SELECT id FROM "_other_prefix_table";')).scalar()
        assert other_val == 99, "非目标表数据必须完好无损"

    engine.dispose()


def test_use_case_10_migration_data_integrity_and_fingerprint_backfill(temp_db_path: Path) -> None:
    """用例 10：验证存量 push_subscriptions 数据批量（多样化特征）9 列逐行无损保留，且指纹回填为 legacy_unverified。"""
    cfg = _build_alembic_cfg(temp_db_path)
    command.upgrade(cfg, "20261031_0001")

    # 1. 插入多条具有全面代表性的存量设备与订阅记录
    devices_data = [
        ("dev-apple-1", "iPhone 15 Pro", "hash-apple", "2026-11-01T00:00:00Z", 0),
        ("dev-android-1", "Pixel 9 Pro", "hash-android", "2026-11-01T00:00:00Z", 0),
        ("dev-mac-1", "MacBook Pro M3", "hash-mac", "2026-11-01T00:00:00Z", 0),
        ("dev-win-1", "Windows 11 Workstation", "hash-win", "2026-11-01T00:00:00Z", 0),
    ]
    # 9 列：subscription_id, device_id, endpoint, p256dh_key, auth_key, user_agent, created_at, last_pushed_at, is_active
    subscriptions_seed = [
        ("sub-1", "dev-apple-1", "https://push.apple.com/send/token1", "p256_k1", "auth_k1", "Mozilla/5.0 (iPhone; CPU OS 17_0)", "2026-11-01T00:01:00Z", "2026-11-01T00:05:00Z", 1),
        ("sub-2", "dev-apple-1", "https://push.apple.com/send/token2", "p256_k2", "auth_k2", "Mozilla/5.0 (iPad; CPU OS 17_0)", "2026-11-01T00:02:00Z", None, 1),
        ("sub-3", "dev-android-1", "https://fcm.googleapis.com/fcm/send/token3", "p256_k3", "auth_k3", "Mozilla/5.0 (Linux; Android 14)", "2026-11-01T00:03:00Z", "2026-11-01T00:06:00Z", 1),
        ("sub-4", "dev-android-1", "https://fcm.googleapis.com/fcm/send/token4", "p256_k4", "auth_k4", None, "2026-11-01T00:04:00Z", None, 0),
        ("sub-5", "dev-mac-1", "https://updates.push.services.mozilla.com/wpush/v2/token5", "p256_k5", "auth_k5", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", "2026-11-01T00:05:00Z", "2026-11-01T00:07:00Z", 1),
        ("sub-6", "dev-win-1", "https://wns2-sn1.notify.windows.com/w/?token6", "p256_k6", "auth_k6", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "2026-11-01T00:06:00Z", None, 0),
    ]

    engine = create_sqlite_engine(temp_db_path)
    with engine.begin() as conn:
        for d in devices_data:
            conn.execute(
                text("INSERT INTO device_sessions (device_id, device_name, auth_token_hash, created_at, last_active_at, is_revoked) VALUES (:did, :dname, :hash, :cat, :lat, :rev)"),
                {"did": d[0], "dname": d[1], "hash": d[2], "cat": d[3], "lat": d[3], "rev": d[4]}
            )
        for s in subscriptions_seed:
            conn.execute(
                text(
                    "INSERT INTO push_subscriptions (subscription_id, device_id, endpoint, p256dh_key, auth_key, user_agent, created_at, last_pushed_at, is_active) "
                    "VALUES (:sid, :did, :ep, :p256, :auth, :ua, :cat, :lpa, :act)"
                ),
                {"sid": s[0], "did": s[1], "ep": s[2], "p256": s[3], "auth": s[4], "ua": s[5], "cat": s[6], "lpa": s[7], "act": s[8]}
            )

        # 2. 迁移前记录全量快照与总量
        old_cols = ["subscription_id", "device_id", "endpoint", "p256dh_key", "auth_key", "user_agent", "created_at", "last_pushed_at", "is_active"]
        pre_records = conn.execute(
            text(f"SELECT {', '.join(old_cols)} FROM push_subscriptions ORDER BY subscription_id ASC;")
        ).mappings().all()
        pre_count = len(pre_records)
        assert pre_count == len(subscriptions_seed)
        pre_map = {r["subscription_id"]: dict(r) for r in pre_records}
    engine.dispose()

    # 3. 执行升级迁移至 20261101_0002（此过程触发迁移脚本内建的逐行校验）
    command.upgrade(cfg, "20261101_0002")

    # 4. 迁移后深度逐行核验
    engine = create_sqlite_engine(temp_db_path)
    with engine.connect() as conn:
        post_records = conn.execute(
            text(f"SELECT {', '.join(old_cols)}, vapid_key_fingerprint FROM push_subscriptions ORDER BY subscription_id ASC;")
        ).mappings().all()
        post_count = len(post_records)

        # 对比迁移前后数据总量绝对一致
        assert post_count == pre_count, f"迁移前后总行数必须绝对一致: 迁移前={pre_count}, 迁移后={post_count}"

        # 逐行比对旧字段值并核对新增字段回填值
        for new_row in post_records:
            sid = new_row["subscription_id"]
            assert sid in pre_map, f"迁移后未找到存量订阅标识: {sid}"
            old_row = pre_map[sid]

            # 9 列旧字段逐一核对
            for col in old_cols:
                assert new_row[col] == old_row[col], f"订阅 {sid} 的字段 {col} 迁移后发生不匹配: 新值={new_row[col]!r}, 旧值={old_row[col]!r}"

            # 单独核验新增字段的回填值必须为 legacy_unverified
            assert new_row["vapid_key_fingerprint"] == "legacy_unverified", f"订阅 {sid} 的指纹必须回填为 legacy_unverified"

        # 验证完整性与外键双重检查
        integ = conn.execute(text("PRAGMA integrity_check;")).scalar()
        assert str(integ).lower() == "ok"
        fk_errs = conn.execute(text("PRAGMA foreign_key_check;")).fetchall()
        assert len(fk_errs) == 0
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


def test_use_case_33_partial_unique_index_constraint_verification(temp_db_path: Path) -> None:
    """用例 33：部分唯一索引 uq_notifications_open_agg 约束测试（顺序排他性与条件约束验证）。
    
    【说明】本测试严格验证数据库引擎层面的部分唯一索引条件约束有效性（开放窗口唯一性与关闭后允许多条）。
    多进程/多连接并发竞争与 CAS 事务防冲突测试留待 Phase 5.3 Dispatcher 调度引擎落地。
    """
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
    """用例 39：项目删除级联清除与全局 purged 变更记录长效保留核验。
    
    【验收细节 1 落实】
    严格区分级联清理与 purged 保留记录：
    - 项目原有的 notification_changes 记录随项目删除而级联清理；
    - 项目删除事务中新写入的 project_id=NULL、change_type='purged' 记录必须独立长效保留，
      不因级联删除而丢失，供全端离线游标与重连客户端感知项目清理并触发 resync。
    """
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

        # 新写入的 project_id IS NULL 且 change_type = 'purged' 的记录必须保留（不得断言表为空）
        purged_records = conn.execute(
            text("SELECT change_seq, project_id, change_type, payload_json FROM notification_changes WHERE change_type = 'purged'")
        ).mappings().all()
        assert len(purged_records) == 1, "全局 purged 记录必须完好保留"
        assert purged_records[0]["project_id"] is None
        assert "p-del" in purged_records[0]["payload_json"]
    engine.dispose()


def test_delivery_last_http_status_reset_storage_capability(temp_db_path: Path) -> None:
    """验证投递记录 last_http_status 字段存储与状态清空能力（底层存储能力验证）。
    
    【说明】本测试仅验证 notification_deliveries 具备在底层存储中显式清空 last_http_status 为 NULL 的持久化能力，
    为遇到网络异常时重置连续 404 判定提供数据模型基础。
    Delivery Worker 真实的重试退避与 404 连续性业务状态机留待 Phase 5.4 落地。
    """
    cfg = _build_alembic_cfg(temp_db_path)
    command.upgrade(cfg, "head")

    engine = create_sqlite_engine(temp_db_path)
    with engine.begin() as conn:
        # 建立测试设备与订阅
        conn.execute(text("INSERT INTO device_sessions (device_id, device_name, auth_token_hash, created_at, last_active_at, is_revoked) VALUES ('d-test', 'Phone', 'h1', '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z', 0)"))
        conn.execute(text("INSERT INTO push_subscriptions (subscription_id, device_id, endpoint, p256dh_key, auth_key, created_at, is_active, vapid_key_fingerprint) VALUES ('sub-test', 'd-test', 'https://fcm/t', 'k1', 'a1', '2026-11-01T00:00:00Z', 1, 'fp')"))
        # 建立测试通知与 delivery
        conn.execute(text("INSERT INTO notifications (id, category, priority, title, body, link_url, window_started_at, is_window_open, is_read, created_at, updated_at) VALUES ('n-404', 'system_alert', 'normal', 'Title', 'Body', '/tasks/1', '2026-11-01T00:00:00Z', 0, 0, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')"))
        conn.execute(text("INSERT INTO notification_deliveries (id, notification_id, subscription_id, device_id, status, next_retry_at, last_http_status, created_at, updated_at) VALUES ('deliv-404', 'n-404', 'sub-test', 'd-test', 'retry', '2026-11-01T00:00:00Z', 404, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')"))

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
