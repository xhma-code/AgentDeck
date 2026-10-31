# -*- coding: utf-8 -*-
"""
SQLAlchemy 2.x DeclarativeBase 与中文数据字典注释强校验工具。

规范保证：
1. 统一 SQLite 友好的约束命名规则（配合 Alembic batch mode）。
2. 所有新领域表名与字段必须声明中文 comment，并可自动同步至 SQLite `t_table_column_comments`。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import MetaData, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utc_now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).isoformat()


class V2Base(DeclarativeBase):
    """AgentDeck v2 ORM 实体统一基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def collect_v2_table_column_comments(validate_non_empty: bool = True) -> list[tuple[str, str, str, str]]:
    """
    收集 V2Base 下全部表与字段的中文注释元组：(table_name, table_comment, column_name, column_comment)。
    若开启 validate_non_empty，任何缺失中文注释的表或列将立即抛出 ValueError。
    """
    records: list[tuple[str, str, str, str]] = []
    for table in V2Base.metadata.sorted_tables:
        t_comment = (table.comment or "").strip()
        if validate_non_empty and not t_comment:
            raise ValueError(f"数据表 '{table.name}' 缺少必填中文表注释 (table.comment)")
        for col in table.columns:
            c_comment = (col.comment or "").strip()
            if validate_non_empty and not c_comment:
                raise ValueError(f"数据表 '{table.name}' 的字段 '{col.name}' 缺少必填中文字段注释 (column.comment)")
            records.append((table.name, t_comment, col.name, c_comment))
    return records


def sync_v2_comments_to_connection(conn: Connection) -> int:
    """
    确保 `t_table_column_comments` 存在，并将 v2 全部领域表与字段的中文注释幂等同步入库。
    返回写入/更新的字段注释条数。
    """
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS t_table_column_comments (
                table_name TEXT NOT NULL,
                table_comment TEXT NOT NULL,
                column_name TEXT NOT NULL,
                column_comment TEXT NOT NULL,
                PRIMARY KEY (table_name, column_name)
            )
            """
        )
    )
    rows: list[dict[str, Any]] = [
        {
            "table_name": t_name,
            "table_comment": t_comm,
            "column_name": c_name,
            "column_comment": c_comm,
        }
        for t_name, t_comm, c_name, c_comm in collect_v2_table_column_comments(validate_non_empty=True)
    ]
    if rows:
        conn.execute(
            text(
                """
                INSERT OR REPLACE INTO t_table_column_comments
                (table_name, table_comment, column_name, column_comment)
                VALUES (:table_name, :table_comment, :column_name, :column_comment)
                """
            ),
            rows,
        )
    return len(rows)

