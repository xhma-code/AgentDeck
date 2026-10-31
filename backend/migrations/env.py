# -*- coding: utf-8 -*-
"""
Alembic 迁移运行环境（SQLite WAL + 外键约束 + 旧表保护过滤器）。
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, event, pool

from backend.v2.config import get_v2_settings
from backend.v2.db.base import V2Base
import backend.v2.db.models  # noqa: F401 注册全部 v2 ORM 模型

config = context.config

if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = V2Base.metadata
MANAGED_V2_TABLES = frozenset(target_metadata.tables.keys())


def _resolve_sqlalchemy_url() -> str:
    explicit_url = (config.get_main_option("sqlalchemy.url") or "").strip()
    if explicit_url:
        return explicit_url
    return get_v2_settings().sqlalchemy_url


def include_object(object_, name, type_, reflected, compare_to):
    """
    保护存量旧表（如 t_device_session / t_push_subscription / t_unified_session_meta）：
    当数据库中反射出不属于 V2Base.metadata 的历史 t_* 表时，Alembic 自动忽略，绝不生成误删脚本。
    """
    if type_ == "table" and reflected and name not in MANAGED_V2_TABLES:
        return False
    return True


def run_migrations_offline() -> None:
    """离线 SQL 生成模式。"""
    url = _resolve_sqlalchemy_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线 SQLite 迁移模式（强制开启 WAL 与外键检查）。"""
    provided_conn = config.attributes.get("connection")
    if provided_conn is not None:
        context.configure(
            connection=provided_conn,
            target_metadata=target_metadata,
            render_as_batch=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()
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
                include_object=include_object,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

