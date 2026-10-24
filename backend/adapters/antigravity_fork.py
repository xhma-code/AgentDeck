"""
Antigravity IDE 到 CLI 会话安全克隆接力（Fork-to-CLI）。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path
from typing import Optional

from backend.core.database import safe_query_external_sqlite
from backend.core.logger import log_structured_error, logger


def fork_ide_session_to_cli(
    raw_session_id: str,
    ide_root: Optional[Path] = None,
    cli_root: Optional[Path] = None,
) -> tuple[bool, str]:
    """将 Antigravity IDE 会话单向安全同步至 CLI。"""
    src_root = ide_root or Path(os.path.expanduser("~/.gemini/antigravity"))
    dst_root = cli_root or Path(os.path.expanduser("~/.gemini/antigravity-cli"))

    try:
        (dst_root / "conversations").mkdir(parents=True, exist_ok=True)
        (dst_root / "brain").mkdir(parents=True, exist_ok=True)

        src_conv_db = src_root / "conversations" / f"{raw_session_id}.db"
        dst_conv_db = dst_root / "conversations" / f"{raw_session_id}.db"
        if src_conv_db.exists():
            shutil.copy2(src_conv_db, dst_conv_db)

        src_brain = src_root / "brain" / raw_session_id
        dst_brain = dst_root / "brain" / raw_session_id
        if src_brain.exists():
            shutil.copytree(src_brain, dst_brain, dirs_exist_ok=True)

        src_sum_db = src_root / "conversation_summaries.db"
        dst_sum_db = dst_root / "conversation_summaries.db"
        if src_sum_db.exists() and dst_sum_db.exists():
            rows = safe_query_external_sqlite(
                src_sum_db,
                "SELECT * FROM conversation_summaries WHERE conversation_id = ?",
                (raw_session_id,),
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

        logger.info(f"已将 Antigravity IDE 会话 {raw_session_id[:8]} 安全克隆同步至 AGY CLI 环境")
        return True, f"agy_cli:{raw_session_id}"
    except Exception as exc:
        msg = log_structured_error(
            problem=f"克隆 Antigravity IDE 会话 ({raw_session_id[:8]}) 至 CLI 失败",
            cause=str(exc),
            fix="请确认 ~/.gemini/antigravity-cli 目录可写且磁盘空间充足。",
            exc=exc,
        )
        return False, str(msg)
