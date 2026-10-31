# -*- coding: utf-8 -*-
"""
AgentDeck 主应用入口与 FastAPI 工厂（Clean-Slate v1.3 架构）。
负责中间件装配、21 张规范表初始化、应用服务挂载、统一异常处理与 React 前端静态资源托管。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api.router import api_v1_router
from backend.application.artifact_service import ArtifactService
from backend.application.decision_service import DecisionService
from backend.application.event_service import EventService
from backend.application.execution_service import ExecutionService
from backend.application.planning_service import PlanningService
from backend.application.project_service import ProjectService
from backend.application.runtime_registry_service import RuntimeRegistryService
from backend.application.session_service import SessionService
from backend.config import AppSettings, get_settings
from backend.core.awake_lock import WindowsAwakeLock
from backend.core.logger import PROJECT_ROOT, log_structured_error
from backend.core.network import TailscaleStatus, detect_tailscale_status
from backend.core.push_service import WebPushService
from backend.db.base import Base, sync_comments_to_connection
from backend.db.engine import create_session_factory, create_sqlite_engine
import backend.db.models  # noqa: F401 确保全部 21 张 ORM 模型注册到 Base.metadata
from backend.domain.exceptions import DomainError
from backend.security.auth_guard import DevicePairingAuthManager

FRONTEND_DIR = PROJECT_ROOT / "frontend"
FRONTEND_DIST_DIR = FRONTEND_DIR / "dist"
FRONTEND_PUBLIC_DIR = FRONTEND_DIR / "public"


def create_app(
    settings: Optional[AppSettings] = None,
    *,
    auto_init_schema: bool = True,
) -> FastAPI:
    """创建并配置 AgentDeck Clean-Slate FastAPI 应用实例。"""
    cfg = settings or get_settings()
    engine = create_sqlite_engine(
        db_path=cfg.db_path,
        busy_timeout_ms=cfg.sqlite_busy_timeout_ms,
    )
    session_factory = create_session_factory(engine)

    if auto_init_schema:
        Base.metadata.create_all(bind=engine)
        with engine.begin() as conn:
            sync_comments_to_connection(conn)

    project_service = ProjectService(session_factory)
    planning_service = PlanningService(session_factory)
    runtime_registry_service = RuntimeRegistryService(session_factory)
    session_service = SessionService(session_factory)
    execution_service = ExecutionService(session_factory)
    decision_service = DecisionService(session_factory)
    artifact_service = ArtifactService(session_factory)
    event_service = EventService(session_factory)

    if auto_init_schema:
        runtime_registry_service.ensure_seed_data()

    ts_status: TailscaleStatus = detect_tailscale_status()
    auth_mgr = DevicePairingAuthManager(session_factory=session_factory, pair_code_ttl_sec=60)
    push_service = WebPushService(
        session_factory=session_factory,
        keys_file=cfg.db_path.parent / "vapid_keys.json",
    )
    awake_lock = WindowsAwakeLock()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        awake_lock.acquire()
        try:
            yield
        finally:
            engine.dispose()
            awake_lock.release()

    app = FastAPI(
        title="AgentDeck — 多项目 AI 研发异步编排驾驶舱",
        version="1.3.0",
        lifespan=lifespan,
    )

    safe_extra_origins = [
        o.strip() for o in cfg.cors_extra_origins if o and o.strip() and o.strip() != "*"
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=safe_extra_origins,
        allow_origin_regex=cfg.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # 挂载服务单例至 app.state
    app.state.settings = cfg
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.project_service = project_service
    app.state.planning_service = planning_service
    app.state.runtime_registry_service = runtime_registry_service
    app.state.session_service = session_service
    app.state.execution_service = execution_service
    app.state.decision_service = decision_service
    app.state.artifact_service = artifact_service
    app.state.event_service = event_service
    app.state.ts_status = ts_status
    app.state.auth_mgr = auth_mgr
    app.state.push_service = push_service
    app.state.awake_lock = awake_lock

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
                "context": exc.context,
            },
        )

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

    app.include_router(api_v1_router)

    if (FRONTEND_DIST_DIR / "assets").exists():
        app.mount(
            "/assets",
            StaticFiles(directory=str(FRONTEND_DIST_DIR / "assets")),
            name="frontend_assets",
        )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa_or_static(full_path: str):
        clean_rel = (full_path or "").strip("/")
        if clean_rel.startswith("api/"):
            return JSONResponse(status_code=404, content={"ok": False, "error": "API 端点不存在"})

        for base_dir in (FRONTEND_DIST_DIR, FRONTEND_PUBLIC_DIR, FRONTEND_DIR):
            if clean_rel and base_dir.exists():
                candidate = (base_dir / clean_rel).resolve()
                if candidate.is_relative_to(base_dir.resolve()) and candidate.is_file():
                    return FileResponse(str(candidate))

        dist_index = FRONTEND_DIST_DIR / "index.html"
        if dist_index.is_file():
            return FileResponse(str(dist_index))

        dev_index = FRONTEND_DIR / "index.html"
        if dev_index.is_file():
            return FileResponse(str(dev_index))

        return HTMLResponse(
            content="<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>AgentDeck</title></head>"
            "<body><h1>AgentDeck Clean-Slate API Running</h1></body></html>",
            status_code=200,
        )

    return app
