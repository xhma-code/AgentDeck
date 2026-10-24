# -*- coding: utf-8 -*-
"""
移动端 PWA 前端静态资源托管与 HTML 首页渲染。
"""

from __future__ import annotations

from typing import Optional

import re
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from backend.core.logger import PROJECT_ROOT
from backend.routes.system import calc_frontend_fingerprint

router = APIRouter(tags=["static_pwa"])
FRONTEND_DIR = PROJECT_ROOT / "frontend"


@router.get("/apple-touch-icon.png")
@router.get("/apple-touch-icon-precomposed.png")
@router.get("/apple-touch-icon-180x180.png")
@router.get("/apple-touch-icon-180x180-precomposed.png")
@router.get("/apple-touch-icon-152x152.png")
@router.get("/apple-touch-icon-152x152-precomposed.png")
@router.get("/apple-touch-icon-120x120.png")
@router.get("/apple-touch-icon-120x120-precomposed.png")
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


@router.get("/favicon.ico")
async def serve_favicon() -> Response:
    fav_file = FRONTEND_DIR / "favicon.ico"
    if fav_file.exists():
        return FileResponse(fav_file)
    icon_file = FRONTEND_DIR / "favicon-32x32.png"
    if icon_file.exists():
        return FileResponse(icon_file, media_type="image/png")
    return JSONResponse({"error": "未找到网站图标文件"}, status_code=404)


@router.get("/sw.js")
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


@router.get("/manifest.webmanifest")
@router.get("/static/manifest.json")
async def serve_manifest(request: Request, token: Optional[str] = None) -> Response:
    t = token or request.cookies.get("agentdeck_token") or ""
    start_url = f"/?token={t}" if t else "/"
    manifest_data = {
        "name": "AgentDeck — 多 Agent 移动中控台",
        "short_name": "AgentDeck",
        "description": "通过 Tailscale / ZeroTier 虚拟网络同步查看与操控 Antigravity IDE、Antigravity CLI、Claude Code 与 OpenCode 会话",
        "start_url": start_url,
        "display": "standalone",
        "background_color": "#090d16",
        "theme_color": "#090d16",
        "orientation": "portrait-primary",
        "icons": [
            {
                "src": "/static/apple-touch-icon.png?v=20261023_diamond_v4",
                "sizes": "180x180",
                "type": "image/png",
            },
            {
                "src": "/static/icon-192.png?v=20261023_diamond_v4",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable",
            },
            {
                "src": "/static/icon-512.png?v=20261023_diamond_v4",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable",
            },
        ],
    }
    return JSONResponse(
        content=manifest_data,
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@router.get("/", response_class=HTMLResponse)
@router.get("/index.html", response_class=HTMLResponse)
async def serve_pwa_index(request: Request) -> HTMLResponse:
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        content = index_file.read_text(encoding="utf-8")
        fp = calc_frontend_fingerprint()
        meta_tag = f'<meta name="agentdeck-build" content="{fp}">'
        if '<meta name="agentdeck-build"' in content:
            content = re.sub(r'<meta name="agentdeck-build"[^>]*>', meta_tag, content)
        elif "</head>" in content:
            content = content.replace("</head>", f"  {meta_tag}\n</head>", 1)
        content = re.sub(r'(/static/app\.js\?v=)[^"\']+', rf'\g<1>{fp}', content)
        content = re.sub(r'(/static/styles\.css\?v=)[^"\']+', rf'\g<1>{fp}', content)

        # 检查 token 参数或 Cookie，支持动态 manifest 引用与 Cookie 保活
        url_token = request.query_params.get("token") or request.cookies.get("agentdeck_token")
        if url_token:
            content = re.sub(
                r'<link rel="manifest"[^>]*>',
                f'<link rel="manifest" href="/manifest.webmanifest?token={url_token}">',
                content,
            )

        resp = HTMLResponse(
            content=content,
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "Content-Disposition": "inline",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
            },
        )
        if url_token:
            resp.set_cookie(
                key="agentdeck_token",
                value=url_token,
                max_age=315360000,
                path="/",
                httponly=False,
                samesite="lax",
            )
        return resp
    return HTMLResponse("<h1>AgentDeck 正在初始化前端资源...</h1>", status_code=503)

