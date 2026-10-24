# -*- coding: utf-8 -*-
"""
设备配对与鉴权路由模块。
包含 pair_code 交换、二维码 SVG 渲染与动态刷新。
"""

from __future__ import annotations

import io
import time
from collections import OrderedDict
from typing import Any

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from backend.routes.deps import (
    get_auth_mgr,
    get_orchestrator,
    get_ts_status,
    verify_request_auth,
)
from backend.routes.schemas import PairCodeExchangeRequest

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_qr_svg_cache: OrderedDict[str, bytes] = OrderedDict()


@router.post("/exchange")
async def exchange_pair_code(req: PairCodeExchangeRequest, request: Request) -> dict[str, Any]:
    """用二维码中的一次性 pair_code 换取长期访问 Token。"""
    auth_mgr = get_auth_mgr(request)
    orchestrator = get_orchestrator(request)

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
    await orchestrator.broadcast_event(
        "DEVICE_PAIRED",
        {
            "device_name": req.device_name,
            "client_ip": client_ip,
            "paired_at": time.time(),
        },
    )
    return {"ok": True, **res}


@router.post("/refresh_pair_code")
async def refresh_bootstrap_pair_code(
    request: Request,
    _auth: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """主动刷新一次性配对码。"""
    auth_mgr = get_auth_mgr(request)
    new_code, expires_at = auth_mgr.refresh_pair_code()
    _qr_svg_cache.clear()
    return {"ok": True, "pair_code": new_code, "pair_code_expires_at": expires_at}


@router.get("/qr.svg")
async def get_local_qr_svg(
    request: Request,
    net: str = Query(default=""),
    _auth: bool = Depends(verify_request_auth),
) -> Any:
    """本地离线生成聚合智能配对二维码 SVG 图像（带 LRU 内存缓存）。"""
    auth_mgr = get_auth_mgr(request)
    ts_status = get_ts_status(request)

    port = request.url.port or 8765
    lan_ip = getattr(ts_status, "lan_ip", "") or ""
    zt_ip = getattr(ts_status, "zerotier_ip", "") or ""
    ts_ip = ts_status.tailscale_ip if (ts_status.is_online and ts_status.tailscale_ip) else ""

    if net in ("lan", "wifi") and lan_ip:
        host_for_qr = lan_ip
    elif net == "zerotier" and zt_ip:
        host_for_qr = zt_ip
    elif net == "tailscale" and ts_ip:
        host_for_qr = ts_ip
    else:
        # 近场扫码默认策略：若物理局域网 Wi-Fi IP 可用，优先使用局域网直连，避免手机未配置虚拟专网时扫码卡死
        host_for_qr = lan_ip or ts_ip or zt_ip or "127.0.0.1"

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
