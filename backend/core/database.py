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
            if "is_deleted" not in cols:
                conn.execute("ALTER TABLE t_unified_session_meta ADD COLUMN is_deleted INTEGER DEFAULT 0;")
            # 对存量未标记过的历史会话，统一兜底初始化为已读（避免跨端冷启动产生大量虚假待查看）
            conn.execute("UPDATE t_unified_session_meta SET last_viewed_at = updated_at WHERE last_viewed_at IS NULL;")

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
            rows = conn.execute("SELECT * FROM t_unified_session_meta WHERE is_deleted = 0 OR is_deleted IS NULL").fetchall()
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
                upd_raw = raw_session_id if (raw_session_id and raw_session_id.strip()) else existing["raw_session_id"]
                upd_parent = parent_unified_id if parent_unified_id is not None else existing["parent_unified_id"]
                upd_path = project_path if project_path is not None else existing["project_path"]
                upd_title = title.strip() if (title is not None and title.strip()) else existing["title"]
                upd_pin = is_pinned if is_pinned is not None else existing["is_pinned"]
                upd_mode = execution_mode if execution_mode is not None else existing["execution_mode"]
                upd_model = model_name if model_name is not None else existing["model_name"]

                conn.execute(
                    """
                    UPDATE t_unified_session_meta
                    SET raw_session_id = ?, parent_unified_id = ?, project_path = ?, title = ?,
                        is_pinned = ?, execution_mode = ?, model_name = ?, updated_at = ?
                    WHERE unified_id = ?
                    """,
                    (upd_raw, upd_parent, upd_path, upd_title, upd_pin, upd_mode, upd_model, now, unified_id),
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
                        project_path, title, is_pinned or 0, execution_mode or "auto",
                        model_name or "", now,
                    ),
                )

            row = conn.execute(
                "SELECT * FROM t_unified_session_meta WHERE unified_id = ?",
                (unified_id,),
            ).fetchone()
            return dict(row) if row else {}

    def update_raw_session_id(self, unified_id: str, new_raw_id: str) -> None:
        """更新会话关联的底层真实 raw_session_id（如 Antigravity CLI 首轮动态回填）。"""
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                "UPDATE t_unified_session_meta SET raw_session_id = ?, updated_at = ? WHERE unified_id = ?",
                (new_raw_id, now, unified_id),
            )

    def _upsert_session_viewed_time(
        self, conn: sqlite3.Connection, unified_id: str, viewed_at_iso: str, only_if_newer: bool = False
    ) -> bool:
        """内部方法：统一执行单会话已读时间的插入或更新。"""
        row = conn.execute(
            "SELECT unified_id, last_viewed_at FROM t_unified_session_meta WHERE unified_id = ?",
            (unified_id,),
        ).fetchone()
        if row:
            prev = row["last_viewed_at"]
            if only_if_newer and prev and viewed_at_iso <= prev:
                return False
            conn.execute(
                "UPDATE t_unified_session_meta SET last_viewed_at = ? WHERE unified_id = ?",
                (viewed_at_iso, unified_id),
            )
            return True
        else:
            agent_type = unified_id.split(":", 1)[0] if ":" in unified_id else "claude_code"
            raw_id = unified_id.split(":", 1)[1] if ":" in unified_id else unified_id
            conn.execute(
                """
                INSERT INTO t_unified_session_meta (
                    unified_id, agent_type, raw_session_id, last_viewed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (unified_id, agent_type, raw_id, viewed_at_iso, viewed_at_iso),
            )
            return True

    def mark_session_viewed(self, unified_id: str, viewed_at: Optional[str] = None) -> str:
        """记录会话在任意客户端被查看的时间戳，实现多端同步已读。"""
        now = viewed_at or utc_now_iso()
        with self.connect() as conn:
            self._upsert_session_viewed_time(conn, unified_id, now)
        return now

    def mark_all_sessions_viewed(
        self, viewed_at: Optional[str] = None, session_ids: Optional[list[str]] = None
    ) -> str:
        """批量将全部或指定会话标记为已读。"""
        now = viewed_at or utc_now_iso()
        with self.connect() as conn:
            if session_ids:
                for uid in session_ids:
                    self._upsert_session_viewed_time(conn, uid, now)
            conn.execute("UPDATE t_unified_session_meta SET last_viewed_at = ?", (now,))
        return now

    def batch_update_session_viewed_times(self, viewed_dict: dict[str, Any]) -> int:
        """接收客户端批量上报的已读时间字典，增量更新落库。"""
        if not viewed_dict:
            return 0
        updated = 0
        with self.connect() as conn:
            for uid, val in viewed_dict.items():
                if not uid or val is None:
                    continue
                iso_time = str(val).strip()
                if not iso_time:
                    continue
                # 支持数字毫秒时间戳转换
                if isinstance(val, (int, float)) or (isinstance(val, str) and val.isdigit()):
                    try:
                        ms = int(val)
                        iso_time = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()
                    except (ValueError, OverflowError, OSError) as exc:
                        logger.warning(f"会话 {uid} 查看时间戳 ({val}) 解析失败，已跳过: {exc}")
                        continue
                if self._upsert_session_viewed_time(conn, uid, iso_time, only_if_newer=True):
                    updated += 1
        return updated

    def mark_session_deleted(self, unified_id: str) -> bool:
        """将指定会话在元数据库中标记为已删除（墓碑状态，防止底层扫描器重新拾取展示）。"""
        now = utc_now_iso()
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE t_unified_session_meta SET is_deleted = 1, updated_at = ? WHERE unified_id = ?",
                (now, unified_id),
            )
            if cur.rowcount == 0:
                parts = unified_id.split(":", 1)
                atype = parts[0] if len(parts) > 1 else "unknown"
                raw_id = parts[1] if len(parts) > 1 else unified_id
                conn.execute(
                    """
                    INSERT INTO t_unified_session_meta
                    (unified_id, agent_type, raw_session_id, is_deleted, updated_at)
                    VALUES (?, ?, ?, 1, ?)
                    """,
                    (unified_id, atype, raw_id, now),
                )
            return True

    def get_deleted_session_ids(self) -> set[str]:
        """获取所有已被软删除的会话 unified_id 集合。"""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT unified_id FROM t_unified_session_meta WHERE is_deleted = 1"
            ).fetchall()
            return {str(r["unified_id"]) for r in rows}

    def delete_session_meta(self, unified_id: str) -> bool:
        """将指定会话标记为已删除（兼容旧调用，写入墓碑防复现）。"""
        return self.mark_session_deleted(unified_id)

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
