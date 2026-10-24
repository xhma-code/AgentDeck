# -*- coding: utf-8 -*-
"""
系统状态、健康检查探针与配置接口。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request

from backend.core.logger import PROJECT_ROOT, logger
from backend.core.workspace import discover_quick_workflows
from backend.routes.deps import (
    get_auth_mgr,
    get_awake_lock,
    get_orchestrator,
    get_ts_status,
    verify_request_auth,
)

router = APIRouter(tags=["system"])
FRONTEND_DIR = PROJECT_ROOT / "frontend"
_fp_cache: tuple[float, str] = (0.0, "2.0.0")


def calc_frontend_fingerprint() -> str:
    """基于前端静态核心文件修改时间生成版本指纹（带1秒内存缓存）。"""
    global _fp_cache
    now = time.time()
    if now - _fp_cache[0] < 1.0:
        return _fp_cache[1]
    try:
        mtimes: list[str] = []
        for fname in ("index.html", "app.js", "styles.css"):
            fpath = FRONTEND_DIR / fname
            if fpath.exists():
                mtimes.append(str(int(fpath.stat().st_mtime)))
        js_dir = FRONTEND_DIR / "js"
        if js_dir.exists():
            for jf in sorted(js_dir.glob("*.js")):
                mtimes.append(str(int(jf.stat().st_mtime)))
        res = "-".join(mtimes) if mtimes else "2.0.0"
        _fp_cache = (now, res)
        return res
    except Exception as exc:
        logger.debug(f"计算前端静态核心文件修改时间指纹异常: {exc}")
        return "2.0.0"


@router.get("/healthz")
@router.get("/api/v1/health")
async def get_health_check(request: Request) -> dict[str, Any]:
    """轻量级非鉴权健康检查探针。"""
    ts_status = get_ts_status(request)
    return {
        "ok": True,
        "status": "healthy",
        "version": "2.0.0",
        "frontend_version": calc_frontend_fingerprint(),
        "tailscale_online": ts_status.is_online,
    }


@router.get("/api/v1/version")
async def get_app_version() -> dict[str, Any]:
    """返回服务端核心版本及当前前端静态资源指纹。"""
    return {
        "ok": True,
        "version": "2.0.0",
        "frontend_version": calc_frontend_fingerprint(),
    }


@router.get("/api/v1/system/status")
async def get_system_status(
    request: Request, _auth: bool = Depends(verify_request_auth)
) -> dict[str, Any]:
    """返回 Tailscale 隧道状态、4 大 Agent 健康体检表、防休眠锁状态与配对二维码 URL。"""
    ts_status = get_ts_status(request)
    auth_mgr = get_auth_mgr(request)
    awake_lock = get_awake_lock(request)
    orchestrator = get_orchestrator(request)

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
    primary_pair_url = ts_valid_pair or zt_pair_url or lan_pair_url or pair_url

    client_host = (request.client.host if request.client else "") or ""
    provided_token = request.headers.get("x-agentdeck-token") or request.query_params.get("token")
    has_valid_token = bool(provided_token and auth_mgr.verify_token(provided_token, client_ip=client_host))

    trusted_client_token: Optional[str] = None
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
        "workflows": discover_quick_workflows(),
        **overview,
    }


@router.get("/api/v1/models")
async def list_available_models(
    request: Request,
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """返回 4 大 Agent 支持的模型目录。"""
    orchestrator = get_orchestrator(request)
    return {"ok": True, "models_by_agent": orchestrator.get_available_models()}


@router.post("/api/v1/system/awake-lock")
async def toggle_awake_lock(
    request: Request,
    enable: bool = Query(...),
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """手机端远程开启或关闭电脑端 Windows 防休眠锁。"""
    awake_lock = get_awake_lock(request)
    if enable:
        awake_lock.acquire()
    else:
        awake_lock.release()
    return {"ok": True, "awake_lock_active": awake_lock.is_active}
