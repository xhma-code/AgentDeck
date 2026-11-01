# -*- coding: utf-8 -*-
"""
Alembic 迁移运行环境（Clean-Slate 21 张规范表 + SQLite WAL + 外键约束）。
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, event, pool

from backend.config import get_settings
from backend.db.base import Base
import backend.db.models  # noqa: F401 注册全部 21 张 ORM 模型

config = context.config

if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
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


def _cleanup_sqlite_alembic_tmp_tables(connection) -> None:
    """针对 SQLite 在 batch alter 迁移事务异常回滚后可能残留的临时表进行兜底清理。"""
    try:
        raw_conn = connection.connection if hasattr(connection, "connection") else connection
        if hasattr(raw_conn, "dbapi_connection"):
            raw_conn = raw_conn.dbapi_connection
        cur = raw_conn.cursor()
        rows = cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '_alembic_tmp_%';"
        ).fetchall()
        for row in rows:
            t_name = row[0]
            cur.execute(f"DROP TABLE IF EXISTS [{t_name}];")
        raw_conn.commit()
    except Exception:
        pass


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


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
