# -*- coding: utf-8 -*-
"""
AgentDeck 主应用入口与 FastAPI 工厂。
负责中间件装配、生命周期管理、路由挂载与静态资源托管。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.core.awake_lock import WindowsAwakeLock
from backend.core.database import MetaDatabase
from backend.core.logger import PROJECT_ROOT, log_structured_error
from backend.core.network import TailscaleStatus, detect_tailscale_status
from backend.core.tailscale_auth import PairingAuthManager
from backend.engines.process_pool import AgentOrchestrator
from backend.adapters.schema_guard import verify_adapter_schemas
from backend.routes.push import router as push_router
from backend.routes.auth import router as auth_router
from backend.routes.deps import verify_request_auth
from backend.routes.schemas import (
    AddWorkspaceRequest,
    CreateSessionRequest,
    HideWorkspaceRequest,
    PairCodeExchangeRequest,
    SendPromptRequest,
    SessionMetaUpdateRequest,
    ToolApprovalDecisionRequest,
    check_etag_matches,
)
from backend.routes.sessions import router as sessions_router
from backend.routes.static_pwa import router as static_pwa_router
from backend.routes.system import router as system_router
from backend.routes.websocket import router as websocket_router
from backend.routes.workspaces import router as workspaces_router

__all__ = [
    "create_app",
    "PairCodeExchangeRequest",
    "CreateSessionRequest",
    "SendPromptRequest",
    "ToolApprovalDecisionRequest",
    "SessionMetaUpdateRequest",
    "AddWorkspaceRequest",
    "HideWorkspaceRequest",
    "check_etag_matches",
    "verify_request_auth",
]

FRONTEND_DIR = PROJECT_ROOT / "frontend"


def create_app(meta_db: Optional[MetaDatabase] = None) -> FastAPI:
    """工厂函数：创建并配置 AgentDeck FastAPI 应用实例。"""
    db = meta_db or MetaDatabase()
    ts_status: TailscaleStatus = detect_tailscale_status()
    auth_mgr = PairingAuthManager(meta_db=db, pair_code_ttl_sec=600)
    awake_lock = WindowsAwakeLock()
    orchestrator = AgentOrchestrator(meta_db=db)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        awake_lock.acquire()
        verify_adapter_schemas(db)
        await orchestrator.start_background_services()
        yield
        await orchestrator.stop_background_services()
        awake_lock.release()

    app = FastAPI(
        title="AgentDeck — 多 Agent 移动端统一中控台",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # 挂载核心单例到 app.state 供路由与测试访问
    app.state.meta_db = db
    app.state.ts_status = ts_status
    app.state.auth_mgr = auth_mgr
    app.state.awake_lock = awake_lock
    app.state.orchestrator = orchestrator

    # 全局未捕获异常中间件（统一汇聚写入 logs/error.log）
    @app.middleware("http")
    async def unified_error_middleware(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            diag = log_structured_error(
                problem=f"API 请求处理异常 ({request.method} {request.url.path})",
                cause=f"{type(exc).__name__}: {exc}",
                fix="请检查请求参数或查看 logs/error.log 中的详细堆栈。",
                exc=exc,
            )
            return JSONResponse(
                status_code=500,
                content={"ok": False, "error": diag},
            )

    # 注册业务子路由
    app.include_router(auth_router)
    app.include_router(system_router)
    app.include_router(workspaces_router)
    app.include_router(sessions_router)
    app.include_router(websocket_router)
    app.include_router(push_router)

    # 挂载静态资源与 PWA 路由（PWA 路由优先拦截动态 manifest 与首屏注入）
    app.include_router(static_pwa_router)
    class NoCacheStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope):
            response = await super().get_response(path, scope)
            if path.endswith((".js", ".css", ".html", ".json")):
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                response.headers["Pragma"] = "no-cache"
            return response

    if FRONTEND_DIR.exists():
        app.mount("/static", NoCacheStaticFiles(directory=str(FRONTEND_DIR)), name="static")

    return app
