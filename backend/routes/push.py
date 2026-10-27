# -*- coding: utf-8 -*-
"""
AgentDeck iOS PWA Web Push 与 Schema 漂移探测路由。
"""

from __future__ import annotations

from typing import Any, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from backend.core.database import MetaDatabase
from backend.core.push_service import get_push_service
from backend.routes.deps import get_meta_db, verify_request_auth

router = APIRouter(prefix="/api/v1", tags=["Push & Schema"])


class PushSubscriptionKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscriptionPayload(BaseModel):
    endpoint: str
    keys: PushSubscriptionKeys


class PushSubscribeRequest(BaseModel):
    subscription: PushSubscriptionPayload
    user_agent: Optional[str] = Field(default="")
    device_id: Optional[str] = Field(default="ios-pwa")


class TestPushRequest(BaseModel):
    title: Optional[str] = Field(default="AgentDeck 提醒")
    body: Optional[str] = Field(default="这是一条来自桌面 AgentDeck 的测试推送！")


@router.get("/push/vapid-public-key")
async def get_vapid_public_key() -> dict[str, Any]:
    """获取供客户端 pushManager.subscribe 使用的 VAPID URL-safe Base64 公钥。"""
    svc = get_push_service()
    return {"public_key": svc.get_public_key()}


@router.post("/push/subscribe")
async def subscribe_push(
    payload: PushSubscribeRequest,
    request: Request,
    _: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """注册或刷新 iPhone PWA 的 Web Push 订阅凭据。"""
    svc = get_push_service()
    ua = payload.user_agent or request.headers.get("user-agent", "iPhone PWA")
    sub_dict = payload.subscription.model_dump()
    result = svc.subscribe(
        device_id=payload.device_id or "ios-pwa",
        subscription_info=sub_dict,
        user_agent=ua,
    )
    return result


@router.post("/push/test")
async def send_test_push(
    payload: TestPushRequest,
    _: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """向所有当前已配对的 iPhone 发送一条即时测试推送。"""
    svc = get_push_service()
    sent = svc.send_notification(
        title=payload.title or "AgentDeck 提醒",
        body=payload.body or "测试推送",
        high_priority=True,
    )
    return {"ok": True, "sent_count": sent}


@router.get("/schema/drift-logs")
async def get_drift_logs(
    limit: int = 50,
    db: MetaDatabase = Depends(get_meta_db),
    _: bool = Depends(verify_request_auth),
) -> dict[str, Any]:
    """查询最近的底层 Agent 协议漂移观测流水。"""
    logs = db.get_schema_drift_logs(limit=limit)
    return {"drift_logs": logs}
