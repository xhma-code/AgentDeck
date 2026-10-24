# -*- coding: utf-8 -*-
"""
AgentDeck 数据库管理与外部 SQLite 安全只读快照引擎。

包含：
1. 本地元数据库 `data/agentdeck.db` 初始化与 CRUD（所有表名与字段均带中文注释）。
2. 针对 Windows SQLite WAL 模式的 `<50ms` 短连接只读查询器 + 锁冲突临时快照兜底机制。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Optional

from backend.core.logger import PROJECT_ROOT, log_structured_error, logger
from backend.core.schema import SCHEMA_DDL, TABLE_COLUMN_COMMENTS
from backend.core.sqlite_snapshot import (
    DATA_DIR,
    META_DB_PATH,
    TMP_SNAPSHOT_DIR,
    safe_query_external_sqlite,
)

__all__ = [
    "DATA_DIR",
    "TMP_SNAPSHOT_DIR",
    "META_DB_PATH",
    "SCHEMA_DDL",
    "TABLE_COLUMN_COMMENTS",
    "utc_now_iso",
    "MetaDatabase",
    "safe_query_external_sqlite",
]


def utc_now_iso() -> str:
    """获取当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).isoformat()


class MetaDatabase:
    """管理 AgentDeck 内部 SQLite 数据库 (agentdeck.db) 的数据访问对象 (DAO)。"""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or META_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        """提供开启 WAL 模式、自动提交事务和字典行映射的数据库上下文连接。"""
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        except Exception as exc:
            conn.rollback()
            log_structured_error(
                problem=f"内部数据库操作异常 ({self.db_path.name})",
                cause=str(exc),
                fix="系统已回滚事务；请检查数据库文件完整性与磁盘写入权限。",
                exc=exc,
            )
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """执行全量 DDL 脚本并初始化数据表中文注释。"""
        with self.connect() as conn:
            conn.executescript(SCHEMA_DDL)
            conn.executemany(
                """
                INSERT OR REPLACE INTO t_table_column_comments
                (table_name, table_comment, column_name, column_comment)
                VALUES (?, ?, ?, ?)
                """,
                TABLE_COLUMN_COMMENTS,
            )

            # 自动迁移检查：确保 t_unified_session_meta 包含 last_viewed_at 列
            cols = [r[1] for r in conn.execute("PRAGMA table_info(t_unified_session_meta)").fetchall()]
            if "last_viewed_at" not in cols:
                conn.execute("ALTER TABLE t_unified_session_meta ADD COLUMN last_viewed_at TEXT;")

            # 首次初始化默认置入 AgentDeck 自身作为工作区
            agentdeck_root = str(PROJECT_ROOT.resolve()).replace("\\", "/")
            conn.execute(
                """
                INSERT OR IGNORE INTO t_workspace_bookmark
                (workspace_name, workspace_path, default_agent, last_used_at)
                VALUES (?, ?, 'antigravity', ?)
                """,
                ("AgentDeck", agentdeck_root, utc_now_iso()),
            )

    def get_session_overrides(self) -> dict[str, dict[str, Any]]:
        """获取所有会话的用户自定义覆盖项（如置顶、执行策略、模型选择等）。"""
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM t_unified_session_meta").fetchall()
            return {r["unified_id"]: dict(r) for r in rows}

    def upsert_session_meta(
        self,
        unified_id: str,
        agent_type: str,
        raw_session_id: str,
        parent_unified_id: Optional[str] = None,
        project_path: Optional[str] = None,
        title: Optional[str] = None,
        is_pinned: Optional[int] = None,
        execution_mode: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> dict[str, Any]:
        """插入或更新会话元数据（支持置顶状态、接管执行模式、模型指定）。"""
        now = utc_now_iso()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM t_unified_session_meta WHERE unified_id = ?",
                (unified_id,),
            ).fetchone()

            if existing:
                upd_parent = parent_unified_id if parent_unified_id is not None else existing["parent_unified_id"]
                upd_path = project_path if project_path is not None else existing["project_path"]
                upd_title = title.strip() if (title is not None and title.strip()) else existing["title"]
                upd_pin = is_pinned if is_pinned is not None else existing["is_pinned"]
                upd_mode = execution_mode if execution_mode is not None else existing["execution_mode"]
                upd_model = model_name if model_name is not None else existing["model_name"]

                conn.execute(
                    """
                    UPDATE t_unified_session_meta
                    SET parent_unified_id = ?, project_path = ?, title = ?,
                        is_pinned = ?, execution_mode = ?, model_name = ?, updated_at = ?
                    WHERE unified_id = ?
                    """,
                    (upd_parent, upd_path, upd_title, upd_pin, upd_mode, upd_model, now, unified_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO t_unified_session_meta (
                        unified_id, agent_type, raw_session_id, parent_unified_id,
                        project_path, title, is_pinned, execution_mode, model_name, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        unified_id, agent_type, raw_session_id, parent_unified_id,
                        project_path, title, is_pinned or 0, execution_mode or "ask",
                        model_name or "", now,
                    ),
                )

            row = conn.execute(
                "SELECT * FROM t_unified_session_meta WHERE unified_id = ?",
                (unified_id,),
            ).fetchone()
            return dict(row) if row else {}

    def mark_session_viewed(self, unified_id: str, viewed_at: Optional[str] = None) -> str:
        """记录会话在任意客户端被查看的时间戳，实现多端同步已读。"""
        now = viewed_at or utc_now_iso()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT unified_id FROM t_unified_session_meta WHERE unified_id = ?",
                (unified_id,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE t_unified_session_meta SET last_viewed_at = ? WHERE unified_id = ?",
                    (now, unified_id),
                )
            else:
                agent_type = unified_id.split(":", 1)[0] if ":" in unified_id else "claude_code"
                raw_id = unified_id.split(":", 1)[1] if ":" in unified_id else unified_id
                conn.execute(
                    """
                    INSERT INTO t_unified_session_meta (
                        unified_id, agent_type, raw_session_id, last_viewed_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (unified_id, agent_type, raw_id, now, now),
                )
        return now

    def delete_session_meta(self, unified_id: str) -> bool:
        """从元数据库中彻底删除会话的自定义索引记录（用于被删除或过期的外部会话清理）。"""
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM t_unified_session_meta WHERE unified_id = ?",
                (unified_id,),
            )
            return cursor.rowcount > 0

    def record_approval_log(
        self,
        unified_id: str,
        tool_call_id: str,
        tool_name: str,
        tool_args_summary: str,
        risk_level: str,
        decision: str,
    ) -> int:
        """记录手机端工具审批决策日志。"""
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO t_tool_approval_log (
                    unified_id, tool_call_id, tool_name,
                    tool_args_summary, risk_level, decision, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    unified_id, tool_call_id, tool_name,
                    tool_args_summary, risk_level, decision, utc_now_iso(),
                ),
            )
            return cur.lastrowid or 0

    def list_workspaces(self, include_hidden: bool = False) -> list[dict[str, Any]]:
        """获取工作区收藏列表，按最近使用时间倒序排列。"""
        sql = "SELECT * FROM t_workspace_bookmark"
        params: tuple[Any, ...] = ()
        if not include_hidden:
            sql += " WHERE is_hidden = 0"
        sql += " ORDER BY is_hidden ASC, last_used_at DESC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def _normalize_and_dedup_workspace(self, conn: sqlite3.Connection, norm_path: str) -> None:
        """清理历史遗留的大小写或斜杠不一致的重复记录，确保唯一路径只保留一条。"""
        existing = conn.execute("SELECT workspace_id, workspace_path FROM t_workspace_bookmark").fetchall()
        for r in existing:
            if r["workspace_path"].lower() == norm_path.lower() and r["workspace_path"] != norm_path:
                conn.execute("DELETE FROM t_workspace_bookmark WHERE workspace_id = ?", (r["workspace_id"],))

    def add_workspace(
        self,
        workspace_name: str,
        workspace_path: str,
        default_agent: str = "claude_code",
    ) -> dict[str, Any]:
        """新增或激活工作区收藏。"""
        norm_path = str(Path(workspace_path).resolve()).replace("\\", "/")
        now = utc_now_iso()
        with self.connect() as conn:
            self._normalize_and_dedup_workspace(conn, norm_path)
            conn.execute(
                """
                INSERT INTO t_workspace_bookmark (
                    workspace_name, workspace_path, default_agent, is_hidden, last_used_at
                ) VALUES (?, ?, ?, 0, ?)
                ON CONFLICT(workspace_path) DO UPDATE SET
                    workspace_name = excluded.workspace_name,
                    default_agent = excluded.default_agent,
                    is_hidden = 0,
                    last_used_at = excluded.last_used_at
                """,
                (workspace_name, norm_path, default_agent, now),
            )
            row = conn.execute("SELECT * FROM t_workspace_bookmark WHERE workspace_path = ?", (norm_path,)).fetchone()
            return dict(row) if row else {}

    def hide_workspace(self, workspace_path: str) -> dict[str, Any]:
        """将指定目录从侧边栏「项目」列表中移出隐藏（不删除会话数据）。"""
        norm_path = str(Path(workspace_path).resolve()).replace("\\", "/")
        now = utc_now_iso()
        folder_name = norm_path.split("/")[-1] if norm_path else "Project"
        with self.connect() as conn:
            self._normalize_and_dedup_workspace(conn, norm_path)
            conn.execute(
                """
                INSERT INTO t_workspace_bookmark (workspace_name, workspace_path, default_agent, is_hidden, last_used_at)
                VALUES (?, ?, 'claude_code', 1, ?)
                ON CONFLICT(workspace_path) DO UPDATE SET
                    is_hidden = 1,
                    last_used_at = excluded.last_used_at
                """,
                (folder_name, norm_path, now),
            )
            return {"workspace_path": norm_path, "is_hidden": 1}


    def get_schema_comments(self) -> list[dict[str, str]]:
        """返回数据表与字段的中文注释字典（用于系统元数据自检与展示）。"""
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM t_table_column_comments").fetchall()
            return [dict(r) for r in rows]
