"""
AgentDeck FastAPI 网关与多路复用 WebSocket 路由中枢。

遵循工程与安全规范：
1. 统一异常捕获中间件，所有未捕获异常按 `[错误现象 Problem] + [根因 Cause] + [修复指令 Fix]` 汇聚写入 `logs/error.log`。
2. WebSocket 握手严格校验 `Origin` 头与 Token，连接成功立即下发 `SESSION_STATE_SNAPSHOT` 快照。
3. 静态挂载移动端响应式 PWA 工作台（`frontend/`）。
"""

from __future__ import annotations

import asyncio
import io
import re
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import qrcode
import qrcode.image.svg

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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.core.workspace import (
    WorkspaceDocumentReader,
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


def check_etag_matches(if_none_match: Optional[str], current_etag: str) -> bool:
    """统一校验客户端 If-None-Match 是否命中服务端 ETag（支持弱验证 W/ 前缀与引号容错）。"""
    if not if_none_match or not current_etag:
        return False
    inm = if_none_match.strip()
    etag = current_etag.strip()
    return inm == etag or inm == etag.strip('"') or inm.strip('"') == etag.strip('"')


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

        # 本机环回或本机物理/虚拟网卡免密直通（方便电脑本地任何地址打开面板查看二维码）
        bind_hosts = getattr(ts_status, "bind_hosts", []) or []
        if client_host in ("127.0.0.1", "::1", "localhost", "testclient") or (client_host in bind_hosts):
            return True

        # Tailscale / ZeroTier 虚拟私网内节点（100.64.0.0/10 及 10.0.0.0/8 等受信网段）：
        # 针对写操作（POST/PATCH/DELETE）强制要求携带有效 Token，杜绝第三方网页跨站 CSRF 借用 IP 免密执行高危操作；
        # 仅对只读 GET/HEAD 请求在已配对虚拟私网 IP 时放行查看
        if auth_mgr.is_paired_trusted_vpn_ip(client_host):
            if request.method in ("GET", "HEAD", "OPTIONS"):
                return True
            if provided_token and auth_mgr.verify_token(provided_token, client_ip=client_host):
                return True

        raise HTTPException(
            status_code=401,
            detail="【鉴权拦截】未授权的访问请求（原因：缺少有效配对 Token 且非本机/Tailscale/ZeroTier 受信节点）。修复：请使用手机扫描电脑端生成的最新配对二维码。",
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
        # 向已连接的所有中控台客户端广播配对成功事件，实现双向握手自动感知
        await orchestrator.broadcast_event(
            "DEVICE_PAIRED",
            {
                "device_name": req.device_name,
                "client_ip": client_ip,
                "paired_at": time.time(),
            },
        )
        return {"ok": True, **res}

    @app.post("/api/v1/auth/refresh_pair_code")
    async def refresh_bootstrap_pair_code(
        _auth: bool = Depends(verify_request_auth),
    ) -> dict[str, Any]:
        """主动刷新一次性配对码（用于电脑端二维码超时过期后点击一键刷新，立即作废前序旧码并清空 SVG 缓存）。"""
        new_code, expires_at = auth_mgr.refresh_pair_code()
        _qr_svg_cache.clear()
        return {"ok": True, "pair_code": new_code, "pair_code_expires_at": expires_at}

    _qr_svg_cache: OrderedDict[str, bytes] = OrderedDict()

    @app.get("/api/v1/auth/qr.svg")
    async def get_local_qr_svg(
        request: Request,
        net: str = Query(default=""),
        _auth: bool = Depends(verify_request_auth),
    ) -> Any:
        """本地离线生成聚合智能配对二维码 SVG 图像（带 LRU 内存缓存，避免重复渲染）。"""
        port = request.url.port or 8765
        lan_ip = getattr(ts_status, "lan_ip", "") or ""
        zt_ip = getattr(ts_status, "zerotier_ip", "") or ""
        ts_ip = ts_status.tailscale_ip if (ts_status.is_online and ts_status.tailscale_ip) else ""

        if net == "lan" and lan_ip:
            host_for_qr = lan_ip
        elif net == "zerotier" and zt_ip:
            host_for_qr = zt_ip
        elif net == "tailscale" and ts_ip:
            host_for_qr = ts_ip
        else:
            # 默认绝对优先 Tailscale（全网漫游），其次 ZeroTier，严禁在有 VPN 时优先显示 Wi-Fi
            host_for_qr = ts_ip or zt_ip or lan_ip or "127.0.0.1"

        pair_url = f"http://{host_for_qr}:{port}/?pair_code={auth_mgr.bootstrap_pair_code}"
        if pair_url in _qr_svg_cache:
            _qr_svg_cache.move_to_end(pair_url)
            return Response(content=_qr_svg_cache[pair_url], media_type="image/svg+xml")

        qr = qrcode.QRCode(border=2, box_size=8)
        qr.add_data(pair_url)
        qr.make(fit=True)
        img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
        buf = io.BytesIO()
        img.save(buf)
        svg_bytes = buf.getvalue()

        _qr_svg_cache[pair_url] = svg_bytes
        if len(_qr_svg_cache) > 20:
            _qr_svg_cache.popitem(last=False)
        return Response(content=svg_bytes, media_type="image/svg+xml")

    _fp_cache: tuple[float, str] = (0.0, "2.0.0")

    def _calc_frontend_fingerprint() -> str:
        """基于前端静态核心文件修改时间生成版本指纹（带1秒内存缓存，避免高频请求频繁 stat 磁盘）。"""
        nonlocal _fp_cache
        now = time.time()
        if now - _fp_cache[0] < 1.0:
            return _fp_cache[1]
        try:
            mtimes = []
            for fname in ("index.html", "app.js", "styles.css"):
                fpath = FRONTEND_DIR / fname
                if fpath.exists():
                    mtimes.append(str(int(fpath.stat().st_mtime)))
            res = "-".join(mtimes) if mtimes else "2.0.0"
            _fp_cache = (now, res)
            return res
        except Exception as exc:
            logger.debug(f"计算前端静态核心文件修改时间指纹异常: {exc}")
            return "2.0.0"

    @app.get("/healthz")
    @app.get("/api/v1/health")
    async def get_health_check() -> dict[str, Any]:
        """轻量级非鉴权健康检查探针（供 Docker、Kubernetes、uptime 监控或 curl 快速连通性测试）。"""
        return {
            "ok": True,
            "status": "healthy",
            "version": "2.0.0",
            "frontend_version": _calc_frontend_fingerprint(),
            "tailscale_online": ts_status.is_online,
        }

    @app.get("/api/v1/version")
    async def get_app_version() -> dict[str, Any]:
        """返回服务端核心版本及当前前端静态资源指纹（非鉴权，便于客户端轮询感知代码改动）"""
        return {
            "ok": True,
            "version": "2.0.0",
            "frontend_version": _calc_frontend_fingerprint(),
        }

    @app.get("/api/v1/system/status")
    async def get_system_status(
        request: Request, _auth: bool = Depends(verify_request_auth)
    ) -> dict[str, Any]:
        """返回 Tailscale 隧道状态、4 大 Agent 健康体检表、防休眠锁状态与配对二维码 URL。"""
        port = request.url.port or 8765
        ts_ip = ts_status.tailscale_ip if (ts_status.is_online and ts_status.tailscale_ip) else ""
        lan_ip = getattr(ts_status, "lan_ip", "") or ""
        zt_ip = getattr(ts_status, "zerotier_ip", "") or ""
        host_for_qr = ts_ip or zt_ip or lan_ip or "127.0.0.1"
        pair_url = f"http://{host_for_qr}:{port}/?pair_code={auth_mgr.bootstrap_pair_code}"
        lan_pair_url = f"http://{lan_ip}:{port}/?pair_code={auth_mgr.bootstrap_pair_code}" if lan_ip else ""
        zt_pair_url = f"http://{zt_ip}:{port}/?pair_code={auth_mgr.bootstrap_pair_code}" if zt_ip else ""
        channels = []
        if ts_status.is_online and ts_status.tailscale_ip:
            channels.append({
                "type": "tailscale",
                "label": "Tailscale",
                "ip": ts_status.tailscale_ip,
                "url": f"http://{ts_status.tailscale_ip}:{port}",
                "pair_url": pair_url,
            })
        if zt_ip:
            channels.append({
                "type": "zerotier",
                "label": "ZeroTier",
                "ip": zt_ip,
                "url": f"http://{zt_ip}:{port}",
                "pair_url": zt_pair_url,
            })
        if lan_ip:
            channels.append({
                "type": "wifi",
                "label": "Wi-Fi 直连",
                "ip": lan_ip,
                "url": f"http://{lan_ip}:{port}",
                "pair_url": lan_pair_url,
            })
        ts_valid_pair = pair_url if ts_status.is_online else ""
        # 默认绝对优先 Tailscale（全网漫游），其次 ZeroTier，严禁在有 VPN 时优先显示 Wi-Fi
        primary_pair_url = ts_valid_pair or zt_pair_url or lan_pair_url or pair_url

        client_host = (request.client.host if request.client else "") or ""
        provided_token = request.headers.get("x-agentdeck-token") or request.query_params.get("token")
        has_valid_token = bool(provided_token and auth_mgr.verify_token(provided_token, client_ip=client_host))

        trusted_client_token: Optional[str] = None
        # PWA 主屏幕独立沙盒自愈保障：仅当客户端尚未持有有效 Token 且属于已配对受信私网时，才下发一次自愈 Token，严禁对已有有效 Token 的设备重复轮转覆盖
        if not has_valid_token and client_host and auth_mgr.is_paired_trusted_vpn_ip(client_host):
            trusted_client_token = auth_mgr.issue_token_for_trusted_vpn_ip(client_host)

        overview = await orchestrator.get_system_overview()

        return {
            "ok": True,
            "trusted_client_token": trusted_client_token,
            "tailscale": {
                "is_online": ts_status.is_online,
                "ip": ts_status.tailscale_ip,
                "dns_name": ts_status.dns_name,
                "hostname": ts_status.hostname,
                "bind_hosts": ts_status.bind_hosts,
                "diagnostic_msg": ts_status.diagnostic_msg,
                "lan_ip": lan_ip,
                "zerotier_ip": zt_ip,
            },
            "awake_lock_active": awake_lock.is_active,
            "pair_url": pair_url,
            "lan_pair_url": lan_pair_url,
            "zt_pair_url": zt_pair_url,
            "primary_pair_url": primary_pair_url,
            "pair_code_expires_at": auth_mgr.bootstrap_pair_code_expires_at,
            "channels": channels,
            **overview,
        }

    @app.get("/api/v1/workflows")
    async def list_quick_workflows(
        project_path: str = Query(default=""),
        _auth: bool = Depends(verify_request_auth),
    ) -> dict[str, Any]:
        """返回 `/` 斜杠快捷指令与项目工作流补全列表。"""
        items = await asyncio.to_thread(discover_quick_workflows, project_path)
        return {"ok": True, "workflows": items}

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
        request: Request,
        agent_type: str = Query(default="all"),
        limit: int = Query(default=40, ge=1, le=200),
        if_none_match: Optional[str] = Header(default=None, alias="if-none-match"),
        _auth: bool = Depends(verify_request_auth),
    ) -> Response:
        """获取聚合后的 4 大 Agent 会话列表（带 ETag 304 协商缓存，大幅减少服务端压力与传输流量）。"""
        etag = orchestrator.get_sessions_etag(agent_filter=agent_type, limit=limit)
        if check_etag_matches(if_none_match, etag):
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})

        sessions = orchestrator.list_all_sessions(
            agent_filter=agent_type, limit_per_agent=limit
        )
        data = {
            "ok": True,
            "count": len(sessions),
            "pending_approvals": [
                pa.to_dict() for pa in orchestrator.pending_approvals.values()
            ],
            "sessions": sessions,
        }
        return JSONResponse(content=data, headers={"ETag": etag, "Cache-Control": "no-cache"})

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
        normalized_mode = orchestrator.normalize_execution_mode(agent_type, req.execution_mode)

        meta = db.upsert_session_meta(
            unified_id=unified_id,
            agent_type=agent_type,
            raw_session_id=raw_id,
            project_path=workdir,
            title=title,
            execution_mode=normalized_mode,
            model_name=req.model_name.strip() if req.model_name else None,
        )
        orchestrator.bump_version()

        if req.initial_prompt.strip():
            await orchestrator.send_prompt_to_session(
                unified_id=unified_id,
                prompt=req.initial_prompt.strip(),
                project_path=workdir,
                execution_mode=normalized_mode,
                model_name=req.model_name.strip() if req.model_name else None,
            )

        return {"ok": True, "unified_id": unified_id, "meta": meta}

    @app.get("/api/v1/sessions/{unified_id:path}/messages")
    async def get_session_messages(
        unified_id: str,
        limit: int = Query(default=30, ge=1, le=500),
        before_step: Optional[int] = Query(default=None),
        since_step: Optional[int] = Query(default=None),
        if_none_match: Optional[str] = Header(default=None, alias="if-none-match"),
        _auth: bool = Depends(verify_request_auth),
    ) -> Response:
        """获取单个会话的结构化对话流（带毫秒级 ETag 304 快速预检、增量更新 since_step 与首屏分页）。"""
        matched, quick_etag = orchestrator.check_session_messages_etag_matches(
            if_none_match=if_none_match,
            unified_id=unified_id,
            limit=limit,
            before_step=before_step,
            since_step=since_step,
        )
        if matched and quick_etag:
            return Response(status_code=304, headers={"ETag": quick_etag, "Cache-Control": "no-cache"})

        detail = orchestrator.get_session_detail(
            unified_id, limit=limit, before_step=before_step, since_step=since_step
        )
        msg_etag = orchestrator.get_session_messages_etag(
            detail,
            unified_id=unified_id,
            limit=limit,
            before_step=before_step,
            since_step=since_step,
        )

        if check_etag_matches(if_none_match, msg_etag):
            return Response(status_code=304, headers={"ETag": msg_etag, "Cache-Control": "no-cache"})

        return JSONResponse(
            content={"ok": True, **detail},
            headers={"ETag": msg_etag, "Cache-Control": "no-cache"},
        )

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
        normalized_mode = (
            orchestrator.normalize_execution_mode(agent_type, req.execution_mode)
            if req.execution_mode is not None
            else None
        )
        updated = db.upsert_session_meta(
            unified_id=unified_id,
            agent_type=agent_type,
            raw_session_id=raw_id,
            title=req.title or "",
            is_pinned=req.is_pinned,
            execution_mode=normalized_mode,
            model_name=req.model_name,
        )
        orchestrator.bump_version()
        return {"ok": True, "meta": updated}

    @app.delete("/api/v1/sessions/{unified_id:path}")
    async def delete_session(
        unified_id: str,
        _auth: bool = Depends(verify_request_auth),
    ) -> dict[str, Any]:
        """删除指定会话在数据库中的索引元数据。"""
        db.delete_session_meta(unified_id)
        orchestrator.bump_version()
        return {"ok": True, "deleted": unified_id}

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
        try:
            return await asyncio.to_thread(WorkspaceDocumentReader.read_document, path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))

    @app.get("/api/v1/git/status")
    async def read_git_status(
        project_path: str = Query(..., description="项目工作区路径"),
        _auth: bool = Depends(verify_request_auth),
    ) -> dict[str, Any]:
        """供手机端轻量查看当前项目分支与未提交文件变更列表（纯只读）。"""
        return await asyncio.to_thread(get_git_workspace_status, project_path)

    @app.get("/api/v1/git/diff")
    async def read_git_diff(
        project_path: str = Query(..., description="项目工作区路径"),
        file_path: str = Query(default="", description="可选：指定单文件相对路径"),
        _auth: bool = Depends(verify_request_auth),
    ) -> dict[str, Any]:
        """供手机端轻量查看单文件或全局未提交 Unified Diff（纯只读）。"""
        res = await asyncio.to_thread(get_git_workspace_diff, project_path, file_path)
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
        bind_hosts = getattr(ts_status, "bind_hosts", []) or []
        is_trusted_net = (
            client_host in ("127.0.0.1", "::1", "localhost", "testclient")
            or (client_host in bind_hosts)
            or auth_mgr.verify_token(token, client_ip=client_host)
            or auth_mgr.is_paired_trusted_vpn_ip(client_host)
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
    @app.get("/apple-touch-icon-180x180.png")
    @app.get("/apple-touch-icon-180x180-precomposed.png")
    @app.get("/apple-touch-icon-152x152.png")
    @app.get("/apple-touch-icon-152x152-precomposed.png")
    @app.get("/apple-touch-icon-120x120.png")
    @app.get("/apple-touch-icon-120x120-precomposed.png")
    async def serve_apple_touch_icon(request: Request) -> Response:
        req_name = Path(request.url.path).name
        target = FRONTEND_DIR / req_name
        icon_file = target if target.exists() and target.is_file() else (FRONTEND_DIR / "apple-touch-icon.png")
        if icon_file.exists():
            return FileResponse(
                icon_file,
                media_type="image/png",
                headers={"Cache-Control": "public, max-age=604800, immutable"},
            )
        return JSONResponse({"error": "未找到指定苹果快捷图标"}, status_code=404)

    @app.get("/favicon.ico")
    async def serve_favicon() -> Response:
        fav_file = FRONTEND_DIR / "favicon.ico"
        if fav_file.exists():
            return FileResponse(fav_file)
        icon_file = FRONTEND_DIR / "favicon-32x32.png"
        if icon_file.exists():
            return FileResponse(icon_file, media_type="image/png")
        return JSONResponse({"error": "未找到网站图标文件"}, status_code=404)

    @app.get("/sw.js")
    async def serve_service_worker() -> Response:
        sw_file = FRONTEND_DIR / "sw.js"
        if sw_file.exists():
            return FileResponse(
                sw_file,
                media_type="application/javascript",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Service-Worker-Allowed": "/",
                },
            )
        return JSONResponse({"error": "未找到 Service Worker 脚本"}, status_code=404)

    @app.get("/", response_class=HTMLResponse)
    @app.get("/index.html", response_class=HTMLResponse)
    async def serve_pwa_index() -> HTMLResponse:
        index_file = FRONTEND_DIR / "index.html"
        if index_file.exists():
            content = index_file.read_text(encoding="utf-8")
            fp = _calc_frontend_fingerprint()
            # 将当前服务端的前端指纹嵌入 meta，以便前端 JS 启动时确立基准版本
            meta_tag = f'<meta name="agentdeck-build" content="{fp}">'
            if "</head>" in content:
                content = content.replace("</head>", f"  {meta_tag}\n</head>", 1)
            # 自动注入版本指纹消除静态脚本与样式的浏览器强缓存
            content = re.sub(r'(/static/app\.js\?v=)[^"\']+', rf'\g<1>{fp}', content)
            content = re.sub(r'(/static/styles\.css\?v=)[^"\']+', rf'\g<1>{fp}', content)
            return HTMLResponse(
                content=content,
                headers={
                    "Content-Type": "text/html; charset=utf-8",
                    "Content-Disposition": "inline",
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                },
            )
        return HTMLResponse("<h1>AgentDeck 正在初始化前端资源...</h1>", status_code=503)

    return app
