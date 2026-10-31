# -*- coding: utf-8 -*-
"""
SQLAlchemy 2.x DeclarativeBase 与全表全字段中文注释强校验同步工具。

规范保证：
1. 统一 SQLite 友好的约束命名规则（配合 Alembic batch mode）。
2. 全部 21 张规范表及每一个字段必须声明非空中文 comment，并自动同步至 `table_column_comments` 表。
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


class Base(DeclarativeBase):
    """AgentDeck ORM 实体统一基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def collect_table_column_comments(validate_non_empty: bool = True) -> list[tuple[str, str, str, str]]:
    """
    收集 Base 下全部 21 张表与所有字段的中文注释元组：(table_name, table_comment, column_name, column_comment)。
    若开启 validate_non_empty，任何缺失中文注释的表或列将立即抛出 ValueError。
    """
    records: list[tuple[str, str, str, str]] = []
    for table in Base.metadata.sorted_tables:
        t_comment = (table.comment or "").strip()
        if validate_non_empty and not t_comment:
            raise ValueError(f"数据表 '{table.name}' 缺少必填中文表注释 (table.comment)")
        for col in table.columns:
            c_comment = (col.comment or "").strip()
            if validate_non_empty and not c_comment:
                raise ValueError(f"数据表 '{table.name}' 的字段 '{col.name}' 缺少必填中文字段注释 (column.comment)")
            records.append((table.name, t_comment, col.name, c_comment))
    return records


def sync_comments_to_connection(conn: Connection) -> int:
    """
    将全部 21 张规范表与字段的中文注释幂等同步写入 `table_column_comments` 表。
    返回写入/更新的字段注释条数。
    """
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS table_column_comments (
                table_name VARCHAR(80) NOT NULL,
                table_comment TEXT NOT NULL,
                column_name VARCHAR(80) NOT NULL,
                column_comment TEXT NOT NULL,
                CONSTRAINT pk_table_column_comments PRIMARY KEY (table_name, column_name)
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
        for t_name, t_comm, c_name, c_comm in collect_table_column_comments(validate_non_empty=True)
    ]
    if rows:
        conn.execute(
            text(
                """
                INSERT OR REPLACE INTO table_column_comments
                (table_name, table_comment, column_name, column_comment)
                VALUES (:table_name, :table_comment, :column_name, :column_comment)
                """
            ),
            rows,
        )
    return len(rows)

