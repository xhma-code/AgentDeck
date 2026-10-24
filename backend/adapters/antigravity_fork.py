# -*- coding: utf-8 -*-
"""
Antigravity IDE 到 CLI 会话安全克隆接力（Fork-to-CLI）。
支持透明克隆 SQLite 会话、brain 思考目录与 conversation_summaries.db 索引，
同时支持作为字典与元组多态解构。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Optional

from backend.core.database import safe_query_external_sqlite
from backend.core.logger import log_structured_error, logger


class ForkResult(dict[str, Any]):
    """双模返回结果：既支持字典访问 (`res['ok']`)，也支持元组解构 (`ok, cid = res`)。"""

    def __iter__(self):
        yield self.get("ok", False)
        yield self.get("cli_unified_id") or self.get("message", "")


def fork_ide_session_to_cli(
    raw_session_id: str = "",
    ide_root: Optional[Path] = None,
    cli_root: Optional[Path] = None,
    ide_raw_session_id: Optional[str] = None,
    meta_db: Optional[Any] = None,
) -> ForkResult:
    """将 Antigravity IDE 会话单向安全同步至 CLI。"""
    effective_id = raw_session_id or ide_raw_session_id or ""
    if not effective_id:
        return ForkResult(ok=False, message="缺少 raw_session_id", cli_unified_id="", cli_raw_session_id="")

    src_root = ide_root or Path(os.path.expanduser("~/.gemini/antigravity"))
    dst_root = cli_root or Path(os.path.expanduser("~/.gemini/antigravity-cli"))

    try:
        (dst_root / "conversations").mkdir(parents=True, exist_ok=True)
        (dst_root / "brain").mkdir(parents=True, exist_ok=True)

        src_conv_db = src_root / "conversations" / f"{effective_id}.db"
        dst_conv_db = dst_root / "conversations" / f"{effective_id}.db"
        if src_conv_db.exists():
            shutil.copy2(src_conv_db, dst_conv_db)

        src_brain = src_root / "brain" / effective_id
        dst_brain = dst_root / "brain" / effective_id
        if src_brain.exists():
            shutil.copytree(src_brain, dst_brain, dirs_exist_ok=True)

        src_sum_db = src_root / "conversation_summaries.db"
        dst_sum_db = dst_root / "conversation_summaries.db"
        if src_sum_db.exists() and dst_sum_db.exists():
            rows = safe_query_external_sqlite(
                src_sum_db,
                "SELECT * FROM conversation_summaries WHERE conversation_id = ?",
                (effective_id,),
            )
            if rows:
                row = rows[0]
                cols = list(row.keys())
                placeholders = ", ".join(["?"] * len(cols))
                col_names = ", ".join([f"`{c}`" for c in cols])
                conn = sqlite3.connect(str(dst_sum_db), timeout=3.0)
                try:
                    conn.execute("PRAGMA busy_timeout = 3000;")
                    conn.execute(
                        f"INSERT OR REPLACE INTO conversation_summaries ({col_names}) VALUES ({placeholders})",
                        tuple(row[c] for c in cols),
                    )
                    conn.commit()
                finally:
                    conn.close()

        cli_unified_id = f"agy_cli:{effective_id}"

        # 若提供了 meta_db，自动同步复制元数据关联
        if meta_db and hasattr(meta_db, "upsert_session_meta"):
            try:
                old_meta = meta_db.get_session_overrides().get(f"agy_ide:{effective_id}") or {}
                meta_db.upsert_session_meta(
                    unified_id=cli_unified_id,
                    agent_type="agy_cli",
                    raw_session_id=effective_id,
                    parent_unified_id=f"agy_ide:{effective_id}",
                    project_path=old_meta.get("project_path"),
                    title=old_meta.get("title"),
                    model_name=old_meta.get("model_name"),
                    execution_mode=old_meta.get("execution_mode"),
                )
            except Exception as meta_exc:
                logger.debug("Fork 同步迁移元数据失败: %s", meta_exc)

        logger.info(f"已将 Antigravity IDE 会话 {effective_id[:8]} 安全克隆同步至 AGY CLI 环境")
        return ForkResult(
            ok=True,
            cli_unified_id=cli_unified_id,
            cli_raw_session_id=effective_id,
            forked_from=f"agy_ide:{effective_id}",
            message="克隆成功",
        )
    except Exception as exc:
        msg = log_structured_error(
            problem=f"克隆 Antigravity IDE 会话 ({effective_id[:8]}) 至 CLI 失败",
            cause=str(exc),
            fix="请确认 ~/.gemini/antigravity-cli 目录可写且磁盘空间充足。",
            exc=exc,
        )
        return ForkResult(
            ok=False,
            cli_unified_id="",
            cli_raw_session_id=effective_id,
            message=str(msg),
        )
