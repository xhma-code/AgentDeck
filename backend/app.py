"""
AgentDeck FastAPI 网关与多路复用 WebSocket 路由中枢。

遵循工程与安全规范：
1. 统一异常捕获中间件，所有未捕获异常按 `[错误现象 Problem] + [根因 Cause] + [修复指令 Fix]` 汇聚写入 `logs/error.log`。
2. WebSocket 握手严格校验 `Origin` 头与 Token，连接成功立即下发 `SESSION_STATE_SNAPSHOT` 快照。
3. 静态挂载移动端响应式 PWA 工作台（`frontend/`）。
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.core.workspace import (
    decode_workspace_uri,
    discover_quick_workflows,
    get_git_workspace_diff,
    get_git_workspace_status,
)

from backend.core.database import PROJECT_ROOT, MetaDatabase
from backend.core.logger import log_structured_error, logger
from backend.core.tailscale_auth import (
    PairingAuthManager,
    TailscaleStatus,
    WindowsAwakeLock,
    detect_tailscale_status,
)
from backend.engines.process_pool import AgentOrchestrator

FRONTEND_DIR = PROJECT_ROOT / "frontend"


# --- Pydantic 请求模型 ---
class PairCodeExchangeRequest(BaseModel):
    pair_code: str = Field(..., description="二维码中的一次性配对码")
    device_name: str = Field(default="Mobile PWA", description="手机端设备标识")


class CreateSessionRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    agent_type: str = Field(default="claude_code", description="Agent 类型 (claude_code/agy_cli/opencode)")
    project_path: str = Field(default="", description="工作区目录绝对路径")
    title: str = Field(default="", description="初始会话标题")
    execution_mode: str = Field(default="ask", description="初始执行模式 (plan/ask/accept_edits/auto)")
    model_name: str = Field(default="", description="选定模型名称")
    initial_prompt: str = Field(default="", description="可选首条发送指令")


class SendPromptRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    prompt: str = Field(..., description="发送给 Agent 的指令内容")
    execution_mode: Optional[str] = Field(default=None, description="本轮执行模式覆盖")
    model_name: Optional[str] = Field(default=None, description="本轮模型名称覆盖")
    project_path: Optional[str] = Field(default=None, description="工作区路径覆盖")
    auto_fork_ide: bool = Field(default=True, description="若为 Antigravity IDE 会话是否自动安全克隆至 CLI")


class ToolApprovalDecisionRequest(BaseModel):
    decision: str = Field(..., description="approved / approve_session_edits / denied")
    feedback: str = Field(default="", description="拒绝或补充指导意见")


class SessionMetaUpdateRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    is_pinned: Optional[int] = Field(default=None, description="是否置顶 (0/1)")
    execution_mode: Optional[str] = Field(default=None, description="执行策略 (plan/ask/accept_edits/auto)")
    model_name: Optional[str] = Field(default=None, description="当前会话选定的模型名称")
    title: Optional[str] = Field(default=None, description="自定义会话标题")


class AddWorkspaceRequest(BaseModel):
    workspace_name: str = Field(..., description="工作区名称")
    workspace_path: str = Field(..., description="工作区绝对路径")
    default_agent: str = Field(default="claude_code", description="默认 Agent 类型")


class HideWorkspaceRequest(BaseModel):
    workspace_path: str = Field(..., description="要从项目列表移出隐藏的目录路径")


def create_app(meta_db: Optional[MetaDatabase] = None) -> FastAPI:
    """工厂函数：创建并配置 AgentDeck FastAPI 应用实例。"""
    db = meta_db or MetaDatabase()
    ts_status: TailscaleStatus = detect_tailscale_status()
    auth_mgr = PairingAuthManager(meta_db=db)
    awake_lock = WindowsAwakeLock()
    orchestrator = AgentOrchestrator(meta_db=db)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        awake_lock.acquire()
        await orchestrator.start_background_services()
        yield
        await orchestrator.stop_background_services()
        awake_lock.release()

    app = FastAPI(
        title="AgentDeck — 多 Agent 移动端统一中控台",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # 挂载核心单例到 app.state 供路由与测试访问
    app.state.meta_db = db
    app.state.ts_status = ts_status
    app.state.auth_mgr = auth_mgr
    app.state.awake_lock = awake_lock
    app.state.orchestrator = orchestrator

    # 全局未捕获异常中间件（强制汇聚写入 logs/error.log）
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

    def verify_request_auth(
        request: Request,
        x_agentdeck_token: Optional[str] = Header(default=None),
        token: Optional[str] = Query(default=None),
    ) -> bool:
        """
        鉴权依赖：
        1. 若请求来自本机环回 `127.0.0.1` / `::1` / `testclient` 或同处私有 Tailscale 网段 (`100.x.y.z`) 且携带有效 Token，予以放行。
        2. 拒绝任何非 Tailscale 且未授权的公网/局域网访问。
        """
        client_host = (request.client.host if request.client else "") or ""
        provided_token = x_agentdeck_token or token

        if provided_token and auth_mgr.verify_token(provided_token, client_ip=client_host):
            return True

        # 本机环回或测试客户端免密直通（方便电脑本地打开面板查看二维码）
        if client_host in ("127.0.0.1", "::1", "localhost", "testclient"):
            return True

        # Tailscale 虚拟局域网内节点（100.x.y.z）：WireGuard 隧道本身已做设备级加密鉴权
        if client_host.startswith("100."):
            return True

        raise HTTPException(
            status_code=401,
            detail="【鉴权拦截】未授权的访问请求（原因：缺少有效配对 Token 且非本机/Tailscale 受信节点）。修复：请使用手机扫描电脑端生成的最新配对二维码。",
        )

    # --- 1. 系统状态与扫码配对接口 ---
    @app.post("/api/v1/auth/exchange")
    async def exchange_pair_code(req: PairCodeExchangeRequest, request: Request) -> dict[str, Any]:
        """用二维码中的一次性 pair_code 换取长期访问 Token。"""
        client_ip = (request.client.host if request.client else "") or ""
        res = auth_mgr.exchange_pair_code(
            pair_code=req.pair_code,
            device_name=req.device_name,
            client_ip=client_ip,
        )
        if not res:
            raise HTTPException(
                status_code=400,
                detail="【配对码失效】该一次性配对码已过期或已被使用（原因：安全防重放机制）。修复：请刷新电脑端页面或使用最新二维码重新扫描。",
            )
        return {"ok": True, **res}

    @app.get("/api/v1/auth/qr.svg")
    async def get_local_qr_svg(
        request: Request, _auth: bool = Depends(verify_request_auth)
    ) -> Any:
        """本地离线生成配对二维码 SVG 图像（不向任何外部二维码 API 发送内网 URL 与 pair_code）。"""
        import io
        import qrcode
        import qrcode.image.svg
        from fastapi.responses import Response

        port = request.url.port or 8765
        host_for_qr = ts_status.tailscale_ip if ts_status.is_online else "127.0.0.1"
        pair_url = f"http://{host_for_qr}:{port}/?pair_code={auth_mgr.bootstrap_pair_code}"
        qr = qrcode.QRCode(border=2, box_size=8)
        qr.add_data(pair_url)
        qr.make(fit=True)
        img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
        buf = io.BytesIO()
        img.save(buf)
        return Response(content=buf.getvalue(), media_type="image/svg+xml")

    @app.get("/api/v1/system/status")
    async def get_system_status(
        request: Request, _auth: bool = Depends(verify_request_auth)
    ) -> dict[str, Any]:
        """返回 Tailscale 隧道状态、4 大 Agent 健康体检表、防休眠锁状态与配对二维码 URL。"""
        port = request.url.port or 8765
        host_for_qr = ts_status.tailscale_ip if ts_status.is_online else "127.0.0.1"
        pair_url = f"http://{host_for_qr}:{port}/?pair_code={auth_mgr.bootstrap_pair_code}"
        return {
            "ok": True,
            "tailscale": {
                "is_online": ts_status.is_online,
                "ip": ts_status.tailscale_ip,
                "dns_name": ts_status.dns_name,
                "hostname": ts_status.hostname,
                "bind_hosts": ts_status.bind_hosts,
                "diagnostic_msg": ts_status.diagnostic_msg,
            },
            "awake_lock_active": awake_lock.is_active,
            "pair_url": pair_url,
            "agents_health": orchestrator.get_startup_health_report(),
            "models_by_agent": orchestrator.get_available_models(),
            "workflows": discover_quick_workflows(),
            "snapshot": orchestrator.get_state_snapshot(),
        }

    @app.get("/api/v1/workflows")
    async def list_quick_workflows(
        project_path: str = Query(default=""),
        _auth: bool = Depends(verify_request_auth),
    ) -> dict[str, Any]:
        """返回 `/` 斜杠快捷指令与项目工作流补全列表。"""
        return {"ok": True, "workflows": discover_quick_workflows(project_path)}

    @app.get("/api/v1/models")
    async def list_available_models(
        _auth: bool = Depends(verify_request_auth),
    ) -> dict[str, Any]:
        """返回 4 大 Agent 支持的模型目录。"""
        return {"ok": True, "models_by_agent": orchestrator.get_available_models()}

    @app.post("/api/v1/system/awake-lock")
    async def toggle_awake_lock(
        enable: bool = Query(...), _auth: bool = Depends(verify_request_auth)
    ) -> dict[str, Any]:
        """手机端远程开启或关闭电脑端 Windows 防休眠锁。"""
        if enable:
            awake_lock.acquire()
        else:
            awake_lock.release()
        return {"ok": True, "awake_lock_active": awake_lock.is_active}

    # --- 2. 会话列表、详情、新建与配置接口 ---
    @app.get("/api/v1/sessions")
    async def list_sessions(
        agent_type: str = Query(default="all"),
        limit: int = Query(default=40, ge=1, le=200),
        _auth: bool = Depends(verify_request_auth),
    ) -> dict[str, Any]:
        """获取聚合后的 4 大 Agent 会话列表。"""
        sessions = orchestrator.list_all_sessions(
            agent_filter=agent_type, limit_per_agent=limit
        )
        return {
            "ok": True,
            "count": len(sessions),
            "pending_approvals": [
                pa.to_dict() for pa in orchestrator.pending_approvals.values()
            ],
            "sessions": sessions,
        }

    @app.post("/api/v1/sessions")
    async def create_new_session(
        req: CreateSessionRequest,
        _auth: bool = Depends(verify_request_auth),
    ) -> dict[str, Any]:
        """在手机端从收藏工作区新建一个 Agent 会话，并可选立即发送首条指令。"""
        raw_id = str(uuid.uuid4())
        agent_type = req.agent_type if req.agent_type != "agy_ide" else "agy_cli"
        unified_id = f"{agent_type}:{raw_id}"
        workdir = req.project_path.strip() or str(PROJECT_ROOT)
        title = req.title.strip() or (req.initial_prompt.strip()[:50] if req.initial_prompt else f"新建 {agent_type} 会话")

        meta = db.upsert_session_meta(
            unified_id=unified_id,
            agent_type=agent_type,
            raw_session_id=raw_id,
            project_path=workdir,
            title=title,
            execution_mode=req.execution_mode,
            model_name=req.model_name.strip() if req.model_name else None,
        )

        if req.initial_prompt.strip():
            await orchestrator.send_prompt_to_session(
                unified_id=unified_id,
                prompt=req.initial_prompt.strip(),
                project_path=workdir,
                execution_mode=req.execution_mode,
                model_name=req.model_name.strip() if req.model_name else None,
            )

        return {"ok": True, "unified_id": unified_id, "meta": meta}

    @app.get("/api/v1/sessions/{unified_id:path}/messages")
    async def get_session_messages(
        unified_id: str,
        limit: int = Query(default=30, ge=1, le=500),
        before_step: Optional[int] = Query(default=None),
        _auth: bool = Depends(verify_request_auth),
    ) -> dict[str, Any]:
        """获取单个会话的结构化对话流（支持首屏分页与向上加载更早历史）、思考块、工具调用 Diff 与挂起审批详情。"""
        detail = orchestrator.get_session_detail(
            unified_id, limit=limit, before_step=before_step
        )
        return {"ok": True, **detail}

    @app.patch("/api/v1/sessions/{unified_id:path}/meta")
    async def update_session_meta(
        unified_id: str,
        req: SessionMetaUpdateRequest,
        _auth: bool = Depends(verify_request_auth),
    ) -> dict[str, Any]:
        """更新会话置顶状态、手机端接管执行策略（plan/ask/accept_edits/auto）或自定义标题。"""
        if ":" not in unified_id:
            raise HTTPException(status_code=400, detail="无效的 unified_id 格式")
        agent_type, raw_id = unified_id.split(":", 1)
        updated = db.upsert_session_meta(
            unified_id=unified_id,
            agent_type=agent_type,
            raw_session_id=raw_id,
            title=req.title or "",
            is_pinned=req.is_pinned,
            execution_mode=req.execution_mode,
            model_name=req.model_name,
        )
        return {"ok": True, "meta": updated}

    # --- 3. 发送指令、工具审批与紧急中断接口 ---
    @app.post("/api/v1/sessions/{unified_id:path}/send")
    async def send_prompt(
        unified_id: str,
        req: SendPromptRequest,
        _auth: bool = Depends(verify_request_auth),
    ) -> dict[str, Any]:
        """在手机端向指定会话发送新指令（支持 Antigravity IDE 会话自动克隆至 CLI 防脑裂）。"""
        if not req.prompt.strip():
            raise HTTPException(status_code=400, detail="指令内容不能为空")
        res = await orchestrator.send_prompt_to_session(
            unified_id=unified_id,
            prompt=req.prompt.strip(),
            project_path=req.project_path,
            execution_mode=req.execution_mode,
            model_name=req.model_name,
            auto_fork_ide=req.auto_fork_ide,
        )
        return res

    @app.post("/api/v1/sessions/{unified_id:path}/approve")
    async def approve_tool_call(
        unified_id: str,
        req: ToolApprovalDecisionRequest,
        _auth: bool = Depends(verify_request_auth),
    ) -> dict[str, Any]:
        """手机端对挂起的工具调用执行「允许」、「本会话允许编辑」或「拒绝」。"""
        return await orchestrator.resolve_tool_approval(
            unified_id=unified_id,
            decision=req.decision,
            feedback=req.feedback,
        )

    @app.post("/api/v1/sessions/{unified_id:path}/abort")
    async def abort_session_execution(
        unified_id: str,
        _auth: bool = Depends(verify_request_auth),
    ) -> dict[str, Any]:
        """手机端点击红色「⏹ 立即中断」按钮：递归强杀 Windows 子进程树并释放会话锁。"""
        await orchestrator.abort_session(unified_id, reason="手机端手动触发紧急中断 (SIGINT)")
        return {"ok": True, "unified_id": unified_id, "status": "aborted"}

    @app.get("/api/v1/sessions/{unified_id:path}/steps/{step_index}/full")
    async def get_full_step_output(
        unified_id: str,
        step_index: int,
        _auth: bool = Depends(verify_request_auth),
    ) -> dict[str, Any]:
        """按需分页/全量拉取被 4KB 截断的单步工具输出，闭环 Spec 中的 4KB 截断按需查看规范。"""
        full_text = orchestrator.get_full_step_output(unified_id, step_index)
        return {
            "ok": True,
            "unified_id": unified_id,
            "step_index": step_index,
            "full_output": full_text,
        }

    # --- 4. 常用工作区收藏夹接口 ---
    @app.get("/api/v1/workspaces")
    async def list_workspaces(_auth: bool = Depends(verify_request_auth)) -> dict[str, Any]:
        return {"ok": True, "workspaces": db.list_workspaces(include_hidden=False)}

    @app.post("/api/v1/workspaces")
    async def add_workspace(
        req: AddWorkspaceRequest, _auth: bool = Depends(verify_request_auth)
    ) -> dict[str, Any]:
        ws = db.add_workspace(req.workspace_name, req.workspace_path, req.default_agent)
        return {"ok": True, "workspace": ws}

    @app.post("/api/v1/workspaces/hide")
    async def hide_workspace(
        req: HideWorkspaceRequest, _auth: bool = Depends(verify_request_auth)
    ) -> dict[str, Any]:
        res = db.hide_workspace(req.workspace_path)
        return {"ok": True, **res}

    @app.get("/api/v1/files/read")
    async def read_local_document(
        path: str = Query(..., description="本地文件绝对路径或 file:// URI"),
        _auth: bool = Depends(verify_request_auth),
    ) -> dict[str, Any]:
        """供手机端全屏阅读器安全读取本地 Markdown 文档（如 implementation_plan.md）或源码文件。"""
        raw_clean = path.split("#")[0].split("?")[0].strip()
        norm_path = decode_workspace_uri(raw_clean)
        if not norm_path:
            raise HTTPException(status_code=400, detail="无效的文件路径")

        file_obj = Path(norm_path)
        if not file_obj.exists() or not file_obj.is_file():
            raise HTTPException(status_code=404, detail=f"文件不存在: {file_obj.name}")

        lower_name = file_obj.name.lower()
        ext = file_obj.suffix.lower()
        blocked_names = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519"}
        blocked_exts = {
            ".exe", ".dll", ".so", ".dylib", ".db", ".sqlite", ".sqlite3",
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".tar", ".gz",
            ".pem", ".key", ".pfx",
        }
        if lower_name in blocked_names or ext in blocked_exts:
            raise HTTPException(status_code=403, detail=f"暂不支持直接文本预览该类型文件 ({file_obj.name})")

        max_bytes = 256 * 1024
        stat = file_obj.stat()
        raw_bytes = file_obj.read_bytes()
        is_truncated = len(raw_bytes) > max_bytes
        if is_truncated:
            raw_bytes = raw_bytes[:max_bytes]

        content = raw_bytes.decode("utf-8", errors="replace")
        if is_truncated:
            content += f"\n\n> [!NOTE]\n> 文档较大（共 {stat.st_size} 字节），手机端已截取前 256KB 展示。"

        return {
            "ok": True,
            "path": norm_path,
            "filename": file_obj.name,
            "ext": ext,
            "is_markdown": ext in (".md", ".markdown", ".mdx", ".txt"),
            "size_bytes": stat.st_size,
            "content": content,
        }

    @app.get("/api/v1/git/status")
    async def read_git_status(
        project_path: str = Query(..., description="项目工作区路径"),
        _auth: bool = Depends(verify_request_auth),
    ) -> dict[str, Any]:
        """供手机端轻量查看当前项目分支与未提交文件变更列表（纯只读）。"""
        return get_git_workspace_status(project_path)

    @app.get("/api/v1/git/diff")
    async def read_git_diff(
        project_path: str = Query(..., description="项目工作区路径"),
        file_path: str = Query(default="", description="可选：指定单文件相对路径"),
        _auth: bool = Depends(verify_request_auth),
    ) -> dict[str, Any]:
        """供手机端轻量查看单文件或全局未提交 Unified Diff（纯只读）。"""
        res = get_git_workspace_diff(project_path, file_path)
        if not res.get("ok"):
            raise HTTPException(status_code=400, detail=res.get("error") or "获取 Diff 失败")
        return res

    # --- 5. 多路复用 WebSocket（含 Origin 校验与断线重连快照恢复）---
    @app.websocket("/ws/stream")
    async def websocket_stream(
        websocket: WebSocket,
        since_seq: int = Query(default=0),
        token: Optional[str] = Query(default=None),
    ) -> None:
        origin = websocket.headers.get("origin")
        if not auth_mgr.is_trusted_origin(origin, ts_status.bind_hosts):
            log_structured_error(
                problem="拒绝可疑的跨站 WebSocket 握手请求",
                cause=f"Origin 头不匹配受信 Tailscale/本地主机: {origin}",
                fix="请直接通过本机或 Tailscale 局域网地址访问。",
            )
            await websocket.close(code=1008)
            return

        client_host = (websocket.client.host if websocket.client else "") or ""
        is_trusted_net = (
            client_host in ("127.0.0.1", "::1", "localhost", "testclient")
            or client_host.startswith("100.")
            or auth_mgr.verify_token(token, client_ip=client_host)
        )
        if not is_trusted_net:
            await websocket.close(code=1008)
            return

        await websocket.accept()

        # 连接或重连成功的第一时间下发 SESSION_STATE_SNAPSHOT 快照帧，确保挂起的工具审批 0 丢失
        snapshot = orchestrator.get_state_snapshot(since_seq_id=since_seq)
        await websocket.send_json(
            {
                "seq_id": snapshot["current_seq_id"],
                "type": "SESSION_STATE_SNAPSHOT",
                "payload": snapshot,
            }
        )

        async def push_to_client(envelope: dict[str, Any]) -> None:
            await websocket.send_json(envelope)

        orchestrator.register_ws_subscriber(push_to_client)
        try:
            while True:
                data = await websocket.receive_json()
                action = data.get("action")
                if action == "ping":
                    await websocket.send_json({"type": "PONG", "seq_id": orchestrator._seq_counter})
                elif action == "sync":
                    last_seq = int(data.get("since_seq") or 0)
                    snap = orchestrator.get_state_snapshot(since_seq_id=last_seq)
                    await websocket.send_json(
                        {
                            "seq_id": snap["current_seq_id"],
                            "type": "SESSION_STATE_SNAPSHOT",
                            "payload": snap,
                        }
                    )
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            log_structured_error(
                problem="WebSocket 实时流会话异常断开",
                cause=str(exc),
                fix="客户端将自动触发指数退避重连。",
                exc=exc,
            )
        finally:
            orchestrator.unregister_ws_subscriber(push_to_client)

    # --- 6. 移动端 PWA 前端静态服务挂载 ---
    if FRONTEND_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/apple-touch-icon.png")
    @app.get("/apple-touch-icon-precomposed.png")
    async def serve_apple_touch_icon() -> Any:
        icon_file = FRONTEND_DIR / "apple-touch-icon.png"
        if icon_file.exists():
            return FileResponse(icon_file, media_type="image/png")
        return JSONResponse({"error": "未找到指定苹果快捷图标"}, status_code=404)

    @app.get("/favicon.ico")
    async def serve_favicon() -> Any:
        fav_file = FRONTEND_DIR / "favicon.ico"
        if fav_file.exists():
            return FileResponse(fav_file)
        icon_file = FRONTEND_DIR / "favicon-32x32.png"
        if icon_file.exists():
            return FileResponse(icon_file, media_type="image/png")
        return JSONResponse({"error": "未找到网站图标文件"}, status_code=404)

    @app.get("/", response_class=HTMLResponse)
    async def serve_pwa_index() -> Any:
        index_file = FRONTEND_DIR / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return HTMLResponse("<h1>AgentDeck 正在初始化前端资源...</h1>")

    return app
