"""
针对 Windows SQLite WAL 模式的 <50ms 短连接只读查询器 + 锁冲突临时快照兜底机制。
"""

from __future__ import annotations

import shutil
import sqlite3
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from backend.core.logger import PROJECT_ROOT, log_structured_error, logger

DATA_DIR = PROJECT_ROOT / "data"
TMP_SNAPSHOT_DIR = DATA_DIR / "tmp_snapshots"

# 外部 SQLite 查询轻量级短效缓存：(db_path_str, mtime_ns, sql, params) -> (cached_epoch, rows)
_ext_query_cache: dict[tuple[str, int, str, tuple[Any, ...]], tuple[float, list[dict[str, Any]]]] = {}


def safe_query_external_sqlite(
    db_path: Path,
    sql: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    """
    安全只读查询外部 Agent（如 Antigravity IDE/CLI、OpenCode）的 SQLite 数据库。

    设计保障：
    1. 短连接即查即关（<50ms），绝不长驻连接阻塞桌面端进程的 WAL Checkpoint。
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
