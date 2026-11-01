# -*- coding: utf-8 -*-
"""
Alembic 迁移运行环境（Clean-Slate 21 张规范表 + SQLite WAL + 外键约束）。
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, event, pool

from backend.config import get_settings
from backend.core.logger import logger
from backend.db.base import Base
import backend.db.models  # noqa: F401 注册全部 21 张 ORM 模型

try:
    config = context.config
except AttributeError:
    config = None

if config is not None and config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _resolve_sqlalchemy_url() -> str:
    explicit_url = (config.get_main_option("sqlalchemy.url") or "").strip()
    if explicit_url:
        return explicit_url
    return get_settings().sqlalchemy_url


def run_migrations_offline() -> None:
    """离线 SQL 生成模式。"""
    url = _resolve_sqlalchemy_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def _escape_sqlite_identifier(identifier: str) -> str:
    """对 SQLite 标识符进行安全转义（双引号包裹，内部双引号转义加倍）。"""
    return '"' + identifier.replace('"', '""') + '"'


def _cleanup_sqlite_alembic_tmp_tables(connection) -> list[str]:
    """针对 SQLite 在 batch alter 迁移事务回滚后可能残留的临时表进行兜底清理。
    
    返回被成功清理的临时表名列表。清理异常时记录 error 日志，严禁静默吞掉，严禁调用底层 DBAPI commit()。
    """
    if os.environ.get("AGENTDECK_DISABLE_MIGRATION_CLEANUP") == "1":
        return []
    cleaned: list[str] = []
    try:
        raw_conn = connection.connection if hasattr(connection, "connection") else connection
        if hasattr(raw_conn, "dbapi_connection"):
            raw_conn = raw_conn.dbapi_connection
        cur = raw_conn.cursor()
        try:
            # 使用 GLOB 精确匹配 _alembic_tmp_* 前缀，避免 LIKE 将下划线视作单字符通配符误伤其他业务表
            rows = cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name GLOB '_alembic_tmp_*';"
            ).fetchall()
            for row in rows:
                t_name = str(row[0])
                escaped = _escape_sqlite_identifier(t_name)
                cur.execute(f"DROP TABLE IF EXISTS {escaped};")
                cleaned.append(t_name)
        finally:
            cur.close()
        if cleaned:
            logger.info("SQLite 迁移异常回滚后，兜底清理了残留 batch 临时表: %s", cleaned)
    except Exception as exc:
        logger.error("SQLite 迁移异常回滚后清理临时表发生错误: %s", exc, exc_info=True)
    return cleaned


def run_migrations_online() -> None:
    """在线 SQLite 迁移模式（强制开启 WAL 与外键检查）。"""
    provided_conn = config.attributes.get("connection")
    if provided_conn is not None:
        context.configure(
            connection=provided_conn,
            target_metadata=target_metadata,
            render_as_batch=True,
            transactional_ddl=True,
        )
        try:
            with context.begin_transaction():
                context.run_migrations()
        except Exception:
            _cleanup_sqlite_alembic_tmp_tables(provided_conn)
            raise
        return

    url = _resolve_sqlalchemy_url()
    connectable = create_engine(
        url,
        poolclass=pool.NullPool,
        connect_args={"timeout": 15.0},
        future=True,
    )

    @event.listens_for(connectable, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
        cur = dbapi_connection.cursor()
        try:
            cur.execute("PRAGMA journal_mode = WAL;")
            cur.execute("PRAGMA synchronous = NORMAL;")
            cur.execute("PRAGMA foreign_keys = ON;")
            cur.execute("PRAGMA busy_timeout = 15000;")
        finally:
            cur.close()

    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
                transactional_ddl=True,
            )
            try:
                with context.begin_transaction():
                    context.run_migrations()
            except Exception:
                _cleanup_sqlite_alembic_tmp_tables(connection)
                raise
    finally:
        connectable.dispose()


if config is not None:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()
