# -*- coding: utf-8 -*-
"""
AgentDeck v2 基线管理与健康自检路由 (/api/v2/*)。
与保留中的旧版 /api/v1/* 完全隔离，为 Phase 2 完整项目管理 API 奠定挂载入口。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from backend.routes.deps import verify_request_auth
from backend.v2 import __version__ as v2_version
from backend.v2.config import AppV2Settings
from backend.v2.db.backup import create_sqlite_online_backup
from backend.v2.db.base import V2Base
from backend.v2.db.engine import inspect_sqlite_pragmas

router = APIRouter(prefix="/api/v2", tags=["v2-foundation"])


@router.get("/health")
async def get_v2_health(request: Request) -> dict[str, Any]:
    """v2 架构基线健康探针：巡检 SQLite WAL、外键强约束与核心领域表就绪状态。"""
    engine = request.app.state.v2_engine
    pragmas = await asyncio.to_thread(inspect_sqlite_pragmas, engine)

    def _check_tables() -> tuple[list[str], list[str]]:
        expected = sorted(V2Base.metadata.tables.keys())
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).scalars().all()
            existing_set = set(rows)
        present = [t for t in expected if t in existing_set]
        missing = [t for t in expected if t not in existing_set]
        return present, missing

    present_tables, missing_tables = await asyncio.to_thread(_check_tables)
    schema_ready = len(missing_tables) == 0

    return {
        "ok": True,
        "architecture": "v1.3",
        "phase": "phase-1-foundation",
        "version": v2_version,
        "sqlite": pragmas,
        "schema_ready": schema_ready,
        "present_table_count": len(present_tables),
        "missing_tables": missing_tables,
    }


@router.post("/system/backup")
async def trigger_v2_sqlite_backup(
    request: Request,
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """受鉴权保护的 SQLite 在线一致性热备接口（基于 sqlite3 Backup API）。"""
    settings: AppV2Settings = request.app.state.v2_settings
    res = await asyncio.to_thread(
        create_sqlite_online_backup,
        settings.db_path,
        settings.backup_dir,
        tag="api_manual",
    )
    return {
        "ok": True,
        "backup": {
            "backup_path": res.backup_path,
            "size_bytes": res.size_bytes,
            "integrity_status": res.integrity_status,
            "created_at": res.created_at,
        },
    }

