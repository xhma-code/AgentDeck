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
from backend.v2.api.router import router as v2_router
from backend.v2.config import AppV2Settings, get_v2_settings
from backend.v2.db.engine import create_session_factory, create_sqlite_v2_engine
from backend.v2.domain.exceptions import DomainError
import backend.v2.db.models  # noqa: F401 确保 ORM 元数据完整注册

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


def create_app(
    meta_db: Optional[MetaDatabase] = None,
    v2_settings: Optional[AppV2Settings] = None,
) -> FastAPI:
    """工厂函数：创建并配置 AgentDeck FastAPI 应用实例。"""
    db = meta_db or MetaDatabase()
    settings = v2_settings or get_v2_settings()
    # 若测试传入了自定义 meta_db.db_path，使 v2 引擎指向同一数据库文件以保持一致隔离
    target_db_path = db.db_path if meta_db is not None else settings.db_path
    v2_engine = create_sqlite_v2_engine(
        db_path=target_db_path,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
    )
    v2_session_factory = create_session_factory(v2_engine)

    ts_status: TailscaleStatus = detect_tailscale_status()
    auth_mgr = PairingAuthManager(meta_db=db, pair_code_ttl_sec=600)
    awake_lock = WindowsAwakeLock()
    orchestrator = AgentOrchestrator(meta_db=db)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        awake_lock.acquire()
        verify_adapter_schemas(db)
        await orchestrator.start_background_services()
        try:
            yield
        finally:
            await orchestrator.stop_background_services()
            v2_engine.dispose()
            awake_lock.release()

    app = FastAPI(
        title="AgentDeck — 多项目 AI 研发异步编排驾驶舱",
        version="2.2.0",
        lifespan=lifespan,
    )

    # 安全收敛：禁止 allow_origins=["*"] 与 allow_credentials=True 的危险组合，
    # 仅放行受控本机、Tailscale/ZeroTier/私网段及显式配置的非通配 Origin
    safe_extra_origins = [
        o.strip() for o in settings.cors_extra_origins if o and o.strip() and o.strip() != "*"
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=safe_extra_origins,
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # 挂载核心单例到 app.state 供路由与测试访问
    app.state.meta_db = db
    app.state.v2_settings = settings
    app.state.v2_engine = v2_engine
    app.state.v2_session_factory = v2_session_factory
    app.state.ts_status = ts_status
    app.state.auth_mgr = auth_mgr
    app.state.awake_lock = awake_lock
    app.state.orchestrator = orchestrator

    @app.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "ok": False,
                "error_code": exc.error_code,
                "error": exc.diagnostic,
                "problem": exc.problem,
                "cause": exc.cause,
                "fix": exc.fix,
            },
        )

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

    # 注册 v2 新架构路由（/api/v2/*）与保留期 v1 旧业务路由
    app.include_router(v2_router)
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
