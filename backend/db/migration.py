# -*- coding: utf-8 -*-
"""
Alembic 数据库迁移管理器。
将 Alembic 确立为数据库 Schema 的唯一正式版本来源，支持版本一致性校验、安全备份与自动升级。
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from backend.config import get_settings
from backend.core.logger import log_structured_error, logger
from backend.db.backup import create_sqlite_online_backup

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ALEMBIC_INI_PATH = PROJECT_ROOT / "alembic.ini"


def get_alembic_config(db_path: Optional[Path] = None) -> Config:
    """构建配置好目标数据库连接与脚本路径的 Alembic Config。"""
    cfg = Config(str(ALEMBIC_INI_PATH))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    
    target_db = (db_path or get_settings().db_path).resolve()
    sqlite_url = f"sqlite:///{target_db.as_posix()}"
    cfg.set_main_option("sqlalchemy.url", sqlite_url)
    return cfg


def backup_database_file(db_path: Path) -> Optional[Path]:
    """在执行正式数据库迁移前使用 SQLite Backup API 创建一致性热备并立即校验完整性。"""
    if not db_path.exists() or db_path.stat().st_size == 0:
        return None
    backup_dir = db_path.parent / "backups"
    try:
        res = create_sqlite_online_backup(db_path, backup_dir=backup_dir, tag="migration_pre")
        backup_path = Path(res.backup_path)
        logger.info("数据库迁移前在线热备成功并通过完整性校验: %s", backup_path)
        return backup_path
    except Exception as exc:
        diag = log_structured_error(
            problem="数据库迁移前热备失败，已依据安全策略中止迁移",
            cause=f"{type(exc).__name__}: {exc}",
            fix="请检查备份目录权限、磁盘空间及当前数据库连接状态，迁移操作已安全中断。",
            exc=exc,
        )
        raise RuntimeError(f"数据库迁移前热备失败，中止迁移以保护现有数据: {exc}") from exc


def inspect_migration_status(engine: Engine, db_path: Optional[Path] = None) -> dict[str, Any]:
    """
    检查当前数据库中的 Alembic 版本与目标迁移脚本 Head 的一致性状态。
    """
    alembic_cfg = get_alembic_config(db_path)
    script_dir = ScriptDirectory.from_config(alembic_cfg)
    head_rev = script_dir.get_current_head()

    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        current_rev = context.get_current_revision()

    is_synced = (current_rev == head_rev) and (head_rev is not None)
    return {
        "current_revision": current_rev,
        "head_revision": head_rev,
        "is_synced": is_synced,
        "is_uninitialized": current_rev is None,
    }


def apply_database_migrations(
    db_path: Optional[Path] = None,
    target_revision: str = "head",
    create_backup: bool = True,
) -> None:
    """
    执行 Alembic 数据库迁移至指定目标版本（默认 head）。
    """
    settings = get_settings()
    target_db = (db_path or settings.db_path).resolve()
    target_db.parent.mkdir(parents=True, exist_ok=True)

    if create_backup and target_db.exists():
        backup_database_file(target_db)

    alembic_cfg = get_alembic_config(target_db)
    command.upgrade(alembic_cfg, target_revision)
    logger.info("Alembic 数据库迁移完成，当前目标版本: %s", target_revision)

