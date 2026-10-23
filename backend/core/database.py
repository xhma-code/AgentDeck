"""
AgentDeck 数据库管理与外部 SQLite 安全只读快照引擎。

包含：
1. 本地元数据库 `data/agentdeck.db` 初始化与 CRUD（所有表名与字段均带中文注释）。
2. 针对 Windows SQLite WAL 模式的 `<50ms` 短连接只读查询器 + 锁冲突临时快照兜底机制。
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
import time
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Optional

from backend.core.logger import PROJECT_ROOT, log_structured_error, logger

DATA_DIR = PROJECT_ROOT / "data"
TMP_SNAPSHOT_DIR = DATA_DIR / "tmp_snapshots"
META_DB_PATH = DATA_DIR / "agentdeck.db"

# 包含完整中文注释的数据库建表语句
SCHEMA_DDL = """
-- 表名：t_device_session（移动端配对设备与鉴权会话表）
CREATE TABLE IF NOT EXISTS t_device_session (
    device_id TEXT PRIMARY KEY,          -- 设备唯一标识（UUID）
    device_name TEXT NOT NULL,           -- 手机设备名称（如 iPhone / Android User-Agent 摘要）
    auth_token_hash TEXT NOT NULL,       -- 鉴权访问令牌 SHA-256 哈希值
    tailscale_ip TEXT,                   -- 最近连接的 Tailscale 虚拟局域网 IP
    created_at TEXT NOT NULL,            -- 配对创建时间（ISO 8601）
    last_active_at TEXT NOT NULL,        -- 最后活跃时间（ISO 8601）
    is_revoked INTEGER DEFAULT 0         -- 是否已吊销（0：有效，1：已吊销）
);

-- 表名：t_unified_session_meta（多 Agent 会话统一索引与扩展元数据表）
CREATE TABLE IF NOT EXISTS t_unified_session_meta (
    unified_id TEXT PRIMARY KEY,         -- 统一会话主键（格式：{agent_type}:{raw_session_id}）
    agent_type TEXT NOT NULL,            -- Agent 类型（agy_ide / agy_cli / claude_code / opencode）
    raw_session_id TEXT NOT NULL,        -- 原生会话 ID
    parent_unified_id TEXT,              -- 克隆来源会话主键（用于 IDE 会话 Fork 到 CLI 后的溯源）
    project_path TEXT,                   -- 关联的工作区目录绝对路径
    title TEXT,                          -- 会话标题摘要（自动提取首条提问或原生标题）
    is_pinned INTEGER DEFAULT 0,         -- 手机端是否置顶（0：否，1：是）
    execution_mode TEXT DEFAULT 'ask',   -- 手机端接管执行策略（plan / ask / accept_edits / auto）
    model_name TEXT DEFAULT '',          -- 会话当前选定的模型名称（如 claude-sonnet-5-5-high / gemini-4-argon-high）
    updated_at TEXT NOT NULL             -- 最近消息更新时间（ISO 8601）
);

-- 表名：t_tool_approval_log（移动端工具调用审批审计日志表）
CREATE TABLE IF NOT EXISTS t_tool_approval_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT, -- 审计日志自增主键
    unified_id TEXT NOT NULL,                 -- 关联的统一会话主键
    tool_call_id TEXT NOT NULL,               -- 工具调用唯一 ID
    tool_name TEXT NOT NULL,                  -- 工具名称（如 Bash / run_command / Edit）
    tool_args_summary TEXT,                   -- 工具参数摘要（如执行的命令行或修改的文件路径）
    risk_level TEXT DEFAULT 'normal',         -- 风险等级（normal：普通，high：高危需长按确认）
    decision TEXT NOT NULL,                   -- 审批决定（approved：允许，denied：拒绝）
    decided_at TEXT NOT NULL                  -- 审批操作时间（ISO 8601）
);

-- 表名：t_workspace_bookmark（移动端新建会话常用工作区收藏表）
CREATE TABLE IF NOT EXISTS t_workspace_bookmark (
    workspace_id INTEGER PRIMARY KEY AUTOINCREMENT, -- 工作区收藏自增主键
    workspace_name TEXT NOT NULL,                   -- 工作区显示名称（如 AgentDeck）
    workspace_path TEXT NOT NULL UNIQUE,            -- 工作区本地绝对路径
    default_agent TEXT DEFAULT 'claude_code',       -- 默认启动的 Agent 类型
    is_hidden INTEGER DEFAULT 0,                    -- 是否已从项目列表手动移出隐藏（0：展示，1：隐藏）
    last_used_at TEXT NOT NULL                      -- 最近使用时间（ISO 8601）
);

-- 表名：t_table_column_comments（数据字典中文注释元数据注册表）
CREATE TABLE IF NOT EXISTS t_table_column_comments (
    table_name TEXT NOT NULL,            -- 数据表英文名称
    table_comment TEXT NOT NULL,         -- 数据表中文注释说明
    column_name TEXT NOT NULL,           -- 字段英文名称
    column_comment TEXT NOT NULL,        -- 字段中文注释说明
    PRIMARY KEY (table_name, column_name)
);
"""

# 显式注册表与字段中文注释元数据（便于自省与审计）
TABLE_COLUMN_COMMENTS: list[tuple[str, str, str, str]] = [
    ("t_device_session", "移动端配对设备与鉴权会话表", "device_id", "设备唯一标识（UUID）"),
    ("t_device_session", "移动端配对设备与鉴权会话表", "device_name", "手机设备名称"),
    ("t_device_session", "移动端配对设备与鉴权会话表", "auth_token_hash", "鉴权访问令牌 SHA-256 哈希值"),
    ("t_device_session", "移动端配对设备与鉴权会话表", "tailscale_ip", "最近连接的 Tailscale 虚拟局域网 IP"),
    ("t_device_session", "移动端配对设备与鉴权会话表", "created_at", "配对创建时间（ISO 8601）"),
    ("t_device_session", "移动端配对设备与鉴权会话表", "last_active_at", "最后活跃时间（ISO 8601）"),
    ("t_device_session", "移动端配对设备与鉴权会话表", "is_revoked", "是否已吊销（0：有效，1：已吊销）"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "unified_id", "统一会话主键（{agent_type}:{raw_session_id}）"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "agent_type", "Agent 类型（agy_ide/agy_cli/claude_code/opencode）"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "raw_session_id", "原生会话 ID"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "parent_unified_id", "克隆来源会话主键"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "project_path", "关联的工作区目录绝对路径"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "title", "会话标题摘要"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "is_pinned", "手机端是否置顶（0：否，1：是）"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "execution_mode", "手机端接管执行策略（plan/ask/accept_edits/auto）"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "model_name", "会话当前选定的模型名称"),
    ("t_unified_session_meta", "多 Agent 会话统一索引与扩展元数据表", "updated_at", "最近消息更新时间（ISO 8601）"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "log_id", "审计日志自增主键"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "unified_id", "关联的统一会话主键"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "tool_call_id", "工具调用唯一 ID"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "tool_name", "工具名称"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "tool_args_summary", "工具参数摘要"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "risk_level", "风险等级（normal：普通，high：高危）"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "decision", "审批决定（approved：允许，denied：拒绝）"),
    ("t_tool_approval_log", "移动端工具调用审批审计日志表", "decided_at", "审批操作时间（ISO 8601）"),
    ("t_workspace_bookmark", "移动端新建会话常用工作区收藏表", "workspace_id", "工作区收藏自增主键"),
    ("t_workspace_bookmark", "移动端新建会话常用工作区收藏表", "workspace_name", "工作区显示名称"),
    ("t_workspace_bookmark", "移动端新建会话常用工作区收藏表", "workspace_path", "工作区本地绝对路径"),
    ("t_workspace_bookmark", "移动端新建会话常用工作区收藏表", "default_agent", "默认启动的 Agent 类型"),
    ("t_workspace_bookmark", "移动端新建会话常用工作区收藏表", "is_hidden", "是否已从项目列表手动移出隐藏（0：展示，1：隐藏）"),
    ("t_workspace_bookmark", "移动端新建会话常用工作区收藏表", "last_used_at", "最近使用时间（ISO 8601）"),
]


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


# 外部 SQLite 查询轻量级短效缓存：(db_path_str, mtime_ns, sql, params) -> (cached_epoch, rows)
_ext_query_cache: dict[tuple[str, int, str, tuple[Any, ...]], tuple[float, list[dict[str, Any]]]] = {}


def safe_query_external_sqlite(
    db_path: Path,
    sql: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    """
    安全只读查询外部 Agent（如 Antigravity IDE/CLI、OpenCode）的 SQLite 数据库。

    设计保障（/autoplan Phase 3 Eng 规范）：
    1. 短连接即查即关（<50ms），绝不长驻连接阻塞桌面端 `language_server.exe` 的 WAL Checkpoint。
    2. 基于 mtime_ns 的 1 秒短效内存缓存，过滤高频重复并发只读查询。
    3. 优先使用 SQLite 原生只读 URI (`mode=ro`) 免锁直连，杜绝日常文件拷贝开销。
    4. 遇锁冲突（sqlite3.OperationalError）自动降级至拷贝快照（Auto-Rescue）兜底。
    """
    if not db_path.exists():
        return []

    try:
        st = db_path.stat()
        mtime_ns = st.st_mtime_ns
    except OSError:
        mtime_ns = 0

    cache_key = (str(db_path.resolve()), mtime_ns, sql, params)
    now = time.monotonic()
    cached = _ext_query_cache.get(cache_key)
    if cached and (now - cached[0] < 1.0):
        return [dict(r) for r in cached[1]]

    def _put_cache(rows_data: list[dict[str, Any]]) -> None:
        if len(_ext_query_cache) >= 128:
            oldest_key = next(iter(_ext_query_cache))
            _ext_query_cache.pop(oldest_key, None)
        _ext_query_cache[cache_key] = (now, rows_data)

    # 第一级尝试：只读 URI 模式直连查询（零磁盘拷贝，支持空格/特殊字符安全转义）
    try:
        abs_str = str(db_path.resolve())
        file_url = urllib.request.pathname2url(abs_str)
        uri = f"file:{file_url}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=1.5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout = 1500;")
            conn.execute("PRAGMA query_only = ON;")
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
            _put_cache(rows)
            return rows
        finally:
            conn.close()
    except Exception as primary_err:
        # 降级尝试常规短连接
        try:
            conn = sqlite3.connect(str(db_path), timeout=1.5)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA busy_timeout = 1500;")
                conn.execute("PRAGMA query_only = ON;")
                rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
                _put_cache(rows)
                return rows
            finally:
                conn.close()
        except sqlite3.OperationalError as op_err:
            primary_err = op_err

        # 第二级自动救援（Auto-Rescue）：拷贝临时快照文件读取
        TMP_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        snap_id = f"{db_path.stem}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        snap_db = TMP_SNAPSHOT_DIR / f"{snap_id}.db"
        try:
            shutil.copy2(db_path, snap_db)
            for suffix in ("-wal", "-shm"):
                aux_src = Path(f"{db_path}{suffix}")
                if aux_src.exists():
                    shutil.copy2(aux_src, Path(f"{snap_db}{suffix}"))

            conn = sqlite3.connect(str(snap_db), timeout=2.0)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA query_only = ON;")
                rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
                logger.info(f"【存储繁忙自动救援】已通过临时快照成功读取 {db_path.name}")
                _put_cache(rows)
                return rows
            finally:
                conn.close()
        except Exception as fallback_err:
            log_structured_error(
                problem=f"读取外部 SQLite 数据库失败 ({db_path.name})",
                cause=f"主连接锁冲突 ({primary_err}) 且临时快照读取异常 ({fallback_err})",
                fix="系统将在下次轮询时自动重试；请确认磁盘空间充足且数据库文件未损坏。",
                exc=fallback_err,
                context={"db_path": str(db_path)},
            )
            return []
        finally:
            for suffix in ("", "-wal", "-shm"):
                p = Path(f"{snap_db}{suffix}")
                if p.exists():
                    try:
                        p.unlink()
                    except OSError:
                        pass
    except Exception as exc:
        log_structured_error(
            problem=f"查询外部 SQLite 数据库出现未知异常 ({db_path.name})",
            cause=str(exc),
            fix="请检查 SQL 查询语句与目标 Agent 版本表结构是否兼容。",
            exc=exc,
            context={"db_path": str(db_path), "sql": sql},
        )
        return []
