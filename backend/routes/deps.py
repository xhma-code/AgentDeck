# -*- coding: utf-8 -*-
"""
FastAPI 路由依赖注入与安全鉴权拦截器。
"""

from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException, Query, Request

from backend.core.awake_lock import WindowsAwakeLock
from backend.core.database import MetaDatabase
from backend.core.network import TailscaleStatus
from backend.core.tailscale_auth import PairingAuthManager
from backend.engines.process_pool import AgentOrchestrator


def get_meta_db(request: Request) -> MetaDatabase:
    return request.app.state.meta_db


def get_auth_mgr(request: Request) -> PairingAuthManager:
    return request.app.state.auth_mgr


def get_ts_status(request: Request) -> TailscaleStatus:
    return request.app.state.ts_status


def get_awake_lock(request: Request) -> WindowsAwakeLock:
    return request.app.state.awake_lock


def get_orchestrator(request: Request) -> AgentOrchestrator:
    return request.app.state.orchestrator


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
    auth_mgr: PairingAuthManager = request.app.state.auth_mgr
    ts_status: TailscaleStatus = request.app.state.ts_status

    client_host = (request.client.host if request.client else "") or ""
    provided_token = x_agentdeck_token or token

    if provided_token and auth_mgr.verify_token(provided_token, client_ip=client_host):
        return True

    # 本机环回或本机物理/虚拟网卡免密直通
    bind_hosts = getattr(ts_status, "bind_hosts", []) or []
    if client_host in ("127.0.0.1", "::1", "localhost", "testclient") or (client_host in bind_hosts):
        return True

    # Tailscale / ZeroTier 虚拟私网内节点
    if auth_mgr.is_paired_trusted_vpn_ip(client_host):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True
        if provided_token and auth_mgr.verify_token(provided_token, client_ip=client_host):
            return True

    raise HTTPException(
        status_code=401,
        detail="【鉴权拦截】未授权的访问请求（原因：缺少有效配对 Token 且非本机/Tailscale/ZeroTier 受信节点）。修复：请使用手机扫描电脑端生成的最新配对二维码。",
    )
