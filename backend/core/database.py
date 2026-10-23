"""
AgentDeck 数据库管理与外部 SQLite 安全只读快照引擎。

包含：
1. 本地元数据库 `data/agentdeck.db` 初始化与 CRUD（所有表名与字段均带中文注释）。
2. 提供向前兼容的外部 SQLite 安全只读查询与 DDL/元数据导出。
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
    TMP_SNAPSHOT_DIR,
    safe_query_external_sqlite,
)

META_DB_PATH = DATA_DIR / "agentdeck.db"


def utc_now_iso() -> str:
    """获取当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).isoformat()


class MetaDatabase:
    """AgentDeck 本地元数据库管理器。"""

    def __init__(self, db_path: Path = META_DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        TMP_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        """提供带 WAL 与 busy_timeout 的短连接上下文管理器。"""
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA busy_timeout = 3000;")
            yield conn
            conn.commit()
        except Exception as exc:
            conn.rollback()
            log_structured_error(
                problem="操作 AgentDeck 本地元数据库失败",
                cause=f"SQLite 执行异常: {exc}",
                fix="请检查 data/agentdeck.db 文件权限或磁盘空间。",
                exc=exc,
            )
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """初始化数据库表结构、中文注释注册表与默认工作区收藏。"""
        with self.connect() as conn:
            conn.executescript(SCHEMA_DDL)
            # 兼容已有数据库平滑升级：检查并补充 model_name 字段
            cols = [
                r["name"]
                for r in conn.execute("PRAGMA table_info(t_unified_session_meta)").fetchall()
            ]
            if "model_name" not in cols:
                conn.execute(
                    "ALTER TABLE t_unified_session_meta ADD COLUMN model_name TEXT DEFAULT '';"
                )
            ws_cols = [
                r["name"]
                for r in conn.execute("PRAGMA table_info(t_workspace_bookmark)").fetchall()
            ]
            if "is_hidden" not in ws_cols:
                conn.execute(
                    "ALTER TABLE t_workspace_bookmark ADD COLUMN is_hidden INTEGER DEFAULT 0;"
                )
            conn.executemany(
                """
                INSERT OR REPLACE INTO t_table_column_comments
                (table_name, table_comment, column_name, column_comment)
                VALUES (?, ?, ?, ?)
                """,
                TABLE_COLUMN_COMMENTS,
            )
            # 默认将当前工作区加入收藏夹
            now = utc_now_iso()
            conn.execute(
                """
                INSERT OR IGNORE INTO t_workspace_bookmark
                (workspace_name, workspace_path, default_agent, last_used_at)
                VALUES (?, ?, ?, ?)
                """,
                (PROJECT_ROOT.name, str(PROJECT_ROOT), "claude_code", now),
            )

    def get_session_overrides(self) -> dict[str, dict[str, Any]]:
        """获取所有会话的用户自定义扩展属性（置顶、执行模式、自定义标题）。"""
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM t_unified_session_meta").fetchall()
            return {row["unified_id"]: dict(row) for row in rows}

    def upsert_session_meta(
        self,
        unified_id: str,
        agent_type: str,
        raw_session_id: str,
        project_path: str = "",
        title: str = "",
        is_pinned: Optional[int] = None,
        execution_mode: Optional[str] = None,
        model_name: Optional[str] = None,
        parent_unified_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """更新或插入会话的统一索引扩展元数据。"""
        now = utc_now_iso()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM t_unified_session_meta WHERE unified_id = ?",
                (unified_id,),
            ).fetchone()
            if existing:
                new_pinned = is_pinned if is_pinned is not None else existing["is_pinned"]
                new_mode = execution_mode if execution_mode is not None else existing["execution_mode"]
                new_model = model_name if model_name is not None else (existing["model_name"] or "")
                new_title = title or existing["title"]
                new_path = project_path or existing["project_path"]
                new_parent = parent_unified_id or existing["parent_unified_id"]
                conn.execute(
                    """
                    UPDATE t_unified_session_meta
                    SET project_path = ?, title = ?, is_pinned = ?,
                        execution_mode = ?, model_name = ?, parent_unified_id = ?, updated_at = ?
                    WHERE unified_id = ?
                    """,
                    (new_path, new_title, new_pinned, new_mode, new_model, new_parent, now, unified_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO t_unified_session_meta
                    (unified_id, agent_type, raw_session_id, parent_unified_id,
                     project_path, title, is_pinned, execution_mode, model_name, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        unified_id,
                        agent_type,
                        raw_session_id,
                        parent_unified_id,
                        project_path,
                        title,
                        is_pinned or 0,
                        execution_mode or "ask",
                        model_name or "",
                        now,
                    ),
                )
            row = conn.execute(
                "SELECT * FROM t_unified_session_meta WHERE unified_id = ?",
                (unified_id,),
            ).fetchone()
            return dict(row) if row else {}

    def delete_session_meta(self, unified_id: str) -> bool:
        """删除指定会话在数据库中的扩展元数据。"""
        with self.connect() as conn:
            conn.execute("DELETE FROM t_unified_session_meta WHERE unified_id = ?", (unified_id,))
            return True

    def record_approval_log(
        self,
        unified_id: str,
        tool_call_id: str,
        tool_name: str,
        tool_args_summary: str,
        risk_level: str,
        decision: str,
    ) -> None:
        """记录移动端工具调用审批审计日志。"""
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO t_tool_approval_log
                (unified_id, tool_call_id, tool_name, tool_args_summary, risk_level, decision, decided_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    unified_id,
                    tool_call_id,
                    tool_name,
                    tool_args_summary[:500],
                    risk_level,
                    decision,
                    utc_now_iso(),
                ),
            )

    def list_workspaces(self, include_hidden: bool = False) -> list[dict[str, Any]]:
        """列出移动端新建会话常用工作区收藏夹。"""
        with self.connect() as conn:
            if include_hidden:
                rows = conn.execute(
                    "SELECT * FROM t_workspace_bookmark ORDER BY last_used_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM t_workspace_bookmark WHERE COALESCE(is_hidden, 0) = 0 ORDER BY last_used_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def _normalize_and_dedup_workspace(conn: sqlite3.Connection, path: str) -> str:
        """统一规范化工作区路径并清理同路径不同斜杠/大小写的冗余记录。"""
        norm_path = path.strip().replace("\\", "/").rstrip("/")
        if len(norm_path) >= 2 and norm_path[1] == ":":
            norm_path = norm_path[0].lower() + norm_path[1:]
        conn.execute(
            "DELETE FROM t_workspace_bookmark WHERE lower(replace(workspace_path, '\\', '/')) = ? AND workspace_path != ?",
            (norm_path.lower(), norm_path),
        )
        return norm_path

    def add_workspace(self, name: str, path: str, default_agent: str = "claude_code") -> dict[str, Any]:
        """添加或更新常用工作区目录（强制设为显示状态 is_hidden=0）。"""
        now = utc_now_iso()
        with self.connect() as conn:
            norm_path = self._normalize_and_dedup_workspace(conn, path)
            conn.execute(
                """
                INSERT INTO t_workspace_bookmark (workspace_name, workspace_path, default_agent, is_hidden, last_used_at)
                VALUES (?, ?, ?, 0, ?)
                ON CONFLICT(workspace_path) DO UPDATE SET
                    workspace_name = excluded.workspace_name,
                    default_agent = excluded.default_agent,
                    is_hidden = 0,
                    last_used_at = excluded.last_used_at
                """,
                (name, norm_path, default_agent, now),
            )
            row = conn.execute(
                "SELECT * FROM t_workspace_bookmark WHERE workspace_path = ?", (norm_path,)
            ).fetchone()
            return dict(row) if row else {}

    def hide_workspace(self, path: str) -> dict[str, Any]:
        """将指定目录从侧边栏「项目」列表中移出隐藏（不删除会话数据）。"""
        now = utc_now_iso()
        with self.connect() as conn:
            norm_path = self._normalize_and_dedup_workspace(conn, path)
            folder_name = norm_path.split("/")[-1] if norm_path else "Project"
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
