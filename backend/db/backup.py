# -*- coding: utf-8 -*-
"""
SQLite 在线一致性热备工具（基于 sqlite3.Connection.backup API）。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.core.logger import log_structured_error, logger


@dataclass(frozen=True)
class BackupResult:
    """数据库在线热备结果摘要。"""

    source_path: str
    backup_path: str
    size_bytes: int
    integrity_status: str
    created_at: str


def create_sqlite_online_backup(
    source_db_path: Path,
    backup_dir: Path,
    *,
    tag: str = "snapshot",
) -> BackupResult:
    """使用 SQLite Backup API 执行在线一致性热备并立即验证完整性。"""
    src_resolved = source_db_path.resolve()
    if not src_resolved.exists():
        diag = log_structured_error(
            problem="无法执行 SQLite 在线备份",
            cause=f"源数据库文件不存在: {src_resolved}",
            fix="请确认数据库初始化路径是否正确。",
        )
        raise FileNotFoundError(diag)

    backup_dir_resolved = backup_dir.resolve()
    backup_dir_resolved.mkdir(parents=True, exist_ok=True)

    safe_tag = "".join(ch if (ch.isalnum() or ch in ("-", "_")) else "_" for ch in tag) or "snapshot"
    now_utc = datetime.now(timezone.utc)
    ts_str = now_utc.strftime("%Y%m%d_%H%M%S_%f")
    dst_path = backup_dir_resolved / f"{safe_tag}_{src_resolved.stem}_{ts_str}.db"

    src_conn: sqlite3.Connection | None = None
    dst_conn: sqlite3.Connection | None = None
    chk_conn: sqlite3.Connection | None = None

    try:
        src_uri = f"file:{src_resolved.as_posix()}?mode=ro"
        src_conn = sqlite3.connect(src_uri, uri=True, timeout=15.0)
        dst_conn = sqlite3.connect(str(dst_path), timeout=15.0)
        src_conn.backup(dst_conn, pages=256, sleep=0.01)
        dst_conn.close()
        dst_conn = None
        src_conn.close()
        src_conn = None

        chk_uri = f"file:{dst_path.as_posix()}?mode=ro"
        chk_conn = sqlite3.connect(chk_uri, uri=True, timeout=10.0)
        row = chk_conn.execute("PRAGMA integrity_check;").fetchone()
        integrity = str(row[0] if row else "unknown")
        chk_conn.close()
        chk_conn = None

        if integrity.lower() != "ok":
            dst_path.unlink(missing_ok=True)
            raise RuntimeError(f"备份完整性检查未通过: {integrity}")

        size_bytes = dst_path.stat().st_size
        logger.info(f"SQLite 在线热备完成并通过完整性校验: {dst_path.name} ({size_bytes} bytes)")
        return BackupResult(
            source_path=src_resolved.as_posix(),
            backup_path=dst_path.as_posix(),
            size_bytes=size_bytes,
            integrity_status=integrity,
            created_at=now_utc.isoformat(),
        )
    except Exception as exc:
        if dst_path.exists():
            try:
                dst_path.unlink(missing_ok=True)
            except OSError:
                pass
        log_structured_error(
            problem=f"SQLite 在线备份异常 ({src_resolved.name})",
            cause=f"{type(exc).__name__}: {exc}",
            fix="请检查磁盘剩余空间、备份目录写入权限及源数据库健康状态。",
            exc=exc,
        )
        raise
    finally:
        for conn in (chk_conn, dst_conn, src_conn):
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def create_online_sqlite_backup(
    source_db_path: Path,
    backup_dir: Path | None = None,
    *,
    label: str = "snapshot",
) -> Path:
    """兼容便捷函数：执行在线热备并返回备份文件 Path。"""
    target_backup_dir = backup_dir or (source_db_path.resolve().parent / "backups")
    res = create_sqlite_online_backup(source_db_path, target_backup_dir, tag=label)
    return Path(res.backup_path)

