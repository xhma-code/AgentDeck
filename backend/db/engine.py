# -*- coding: utf-8 -*-
"""
SQLAlchemy 2.x SQLite 引擎工厂。
强制每次连接启用 WAL 模式、外键强约束 (foreign_keys=ON) 与写锁等待超时 (busy_timeout)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from backend.config import AppSettings, get_settings


def create_sqlite_engine(
    db_path: Optional[Path] = None,
    busy_timeout_ms: Optional[int] = None,
) -> Engine:
    """创建配置好 SQLite WAL 与外键约束的 SQLAlchemy 2.x Engine。"""
    settings: AppSettings = get_settings()
    target_path = (db_path or settings.db_path).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    timeout_ms = busy_timeout_ms if busy_timeout_ms is not None else settings.sqlite_busy_timeout_ms
    timeout_sec = max(1.0, timeout_ms / 1000.0)

    url = f"sqlite:///{target_path.as_posix()}"
    engine = create_engine(
        url,
        connect_args={
            "check_same_thread": False,
            "timeout": timeout_sec,
        },
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode = WAL;")
            cursor.execute("PRAGMA synchronous = NORMAL;")
            cursor.execute("PRAGMA foreign_keys = ON;")
            cursor.execute(f"PRAGMA busy_timeout = {int(timeout_ms)};")
        finally:
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """创建标准 Session 工厂（关闭提交后属性过期，便于领域对象安全读取）。"""
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def inspect_sqlite_pragmas(engine: Engine) -> dict[str, str | int]:
    """只读巡检当前引擎连接上的核心 SQLite PRAGMA 状态。"""
    with engine.connect() as conn:
        journal_mode = str(conn.execute(text("PRAGMA journal_mode;")).scalar() or "").lower()
        foreign_keys = int(conn.execute(text("PRAGMA foreign_keys;")).scalar() or 0)
        busy_timeout = int(conn.execute(text("PRAGMA busy_timeout;")).scalar() or 0)
        return {
            "journal_mode": journal_mode,
            "foreign_keys": foreign_keys,
            "busy_timeout": busy_timeout,
        }

