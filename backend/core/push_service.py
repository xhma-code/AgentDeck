# -*- coding: utf-8 -*-
"""
AgentDeck iOS PWA 原生 Web Push 推送分发引擎（基于规范表 push_subscriptions）。

特性：
1. 零三方外部 App 依赖，通过 VAPID 签名协议直接对接 Apple APNs (iOS 16.4+ Safari PWA)。
2. 本地自动生成并持久化 EC P-256 密钥对 (data/vapid_keys.json)。
3. 支持高危决策即刻推送、长任务完成通知与 410 失效订阅自动注销。
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

import py_vapid
from cryptography.hazmat.primitives import serialization
from pywebpush import WebPushException, webpush
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.config import get_settings
from backend.core.logger import log_structured_error, logger
from backend.db.base import utc_now_iso
from backend.db.models.system import PushSubscriptionModel

VAPID_CLAIMS_SUBJECT = "mailto:admin@agentdeck.local"


class WebPushService:
    """基于 SQLAlchemy `push_subscriptions` 表的 VAPID 密钥与 iOS PWA Web Push 消息分发服务。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        keys_file: Optional[Path] = None,
    ) -> None:
        self.session_factory = session_factory
        settings = get_settings()
        self.keys_file = keys_file or (settings.resolved_db_path.parent / "vapid_keys.json")
        self.keys_file.parent.mkdir(parents=True, exist_ok=True)
        self._vapid = py_vapid.Vapid()
        self._private_key_pem: str = ""
        self._public_key_b64: str = ""
        self._init_keys()
        self._lock = threading.Lock()
        self._last_push_timestamps: dict[str, float] = {}

    def _init_keys(self) -> None:
        """加载或生成 VAPID 椭圆曲线密钥对。"""
        try:
            if self.keys_file.exists():
                data = json.loads(self.keys_file.read_text(encoding="utf-8"))
                self._private_key_pem = data.get("private_key_pem", "")
                self._public_key_b64 = data.get("public_key_b64", "")
                if self._private_key_pem and self._public_key_b64:
                    return

            self._vapid.generate_keys()
            raw_pub_bytes = self._vapid.public_key.public_bytes(
                encoding=serialization.Encoding.X962,
                format=serialization.PublicFormat.UncompressedPoint,
            )
            self._public_key_b64 = base64.urlsafe_b64encode(raw_pub_bytes).decode("utf-8").rstrip("=")
            self._private_key_pem = self._vapid.private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode("utf-8")

            payload = {
                "private_key_pem": self._private_key_pem,
                "public_key_b64": self._public_key_b64,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            self.keys_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("已生成并持久化全新 VAPID 密钥对至: %s", self.keys_file)
        except Exception as exc:
            log_structured_error(
                problem="初始化 VAPID 密钥对失败",
                cause=str(exc),
                fix="请检查 data/ 目录写入权限与 py_vapid 库依赖状态。",
                exc=exc,
            )
            raise

    def get_public_key(self) -> str:
        """获取供客户端 pushManager.subscribe() 使用的 URL-safe Base64 编码公钥。"""
        return self._public_key_b64

    def subscribe(
        self,
        device_id: str,
        subscription_info: dict[str, Any],
        user_agent: str = "",
    ) -> dict[str, Any]:
        """注册或更新前端传入的 PushSubscription 凭据。"""
        endpoint = str(subscription_info.get("endpoint") or "")
        keys = subscription_info.get("keys") or {}
        p256dh = str(keys.get("p256dh") or "")
        auth = str(keys.get("auth") or "")

        if not endpoint or not p256dh or not auth:
            raise ValueError("PushSubscription 缺少 endpoint / p256dh / auth 核心字段")

        subscription_id = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
        now = utc_now_iso()

        with self.session_factory() as session:
            existing = session.get(PushSubscriptionModel, subscription_id)
            if existing is None:
                existing = PushSubscriptionModel(
                    subscription_id=subscription_id,
                    device_id=device_id or "default",
                    endpoint=endpoint,
                    p256dh_key=p256dh,
                    auth_key=auth,
                    user_agent=(user_agent or "")[:240],
                    created_at=now,
                    last_pushed_at=None,
                    is_active=1,
                )
                session.add(existing)
            else:
                existing.device_id = device_id or existing.device_id
                existing.endpoint = endpoint
                existing.p256dh_key = p256dh
                existing.auth_key = auth
                existing.user_agent = (user_agent or "")[:240]
                existing.is_active = 1
            session.commit()

        logger.info("已成功绑定 PWA Web Push 订阅 (device_id: %s, id: %s)", device_id, subscription_id[:8])
        return {"ok": True, "subscription_id": subscription_id}

    def send_notification(
        self,
        title: str,
        body: str,
        url: str = "/",
        device_id: Optional[str] = None,
        tag: str = "agentdeck-alert",
        high_priority: bool = False,
    ) -> int:
        """向已激活的客户端分发推送通知。"""
        now = time.time()
        if not high_priority:
            with self._lock:
                last_time = self._last_push_timestamps.get(tag, 0.0)
                if now - last_time < 2.0:
                    return 0
                self._last_push_timestamps[tag] = now

        with self.session_factory() as session:
            stmt = select(PushSubscriptionModel).where(PushSubscriptionModel.is_active == 1)
            if device_id:
                stmt = stmt.where(PushSubscriptionModel.device_id == device_id)
            subs = list(session.execute(stmt).scalars().all())
            if not subs:
                return 0

            payload_data = json.dumps(
                {
                    "title": title,
                    "body": body,
                    "url": url,
                    "tag": tag,
                    "timestamp": int(now * 1000),
                },
                ensure_ascii=False,
            )

            sent_count = 0
            iso_now = utc_now_iso()
            for sub in subs:
                sub_info = {
                    "endpoint": sub.endpoint,
                    "keys": {
                        "p256dh": sub.p256dh_key,
                        "auth": sub.auth_key,
                    },
                }
                try:
                    webpush(
                        subscription_info=sub_info,
                        data=payload_data,
                        vapid_private_key=self._private_key_pem,
                        vapid_claims={"sub": VAPID_CLAIMS_SUBJECT},
                        timeout=5,
                    )
                    sub.last_pushed_at = iso_now
                    sent_count += 1
                except WebPushException as exc:
                    status_code = getattr(exc.response, "status_code", None) if hasattr(exc, "response") else None
                    if status_code in (404, 410):
                        logger.warning("Web Push 订阅已失效 (%s)，自动解除激活: %s", status_code, sub.subscription_id[:8])
                        sub.is_active = 0
                    else:
                        logger.warning("向 APNs 发送 Web Push 异常 (%s): %s", status_code, str(exc))
                except Exception as exc:
                    logger.warning("Web Push 推送遇到网络异常: %s", str(exc))

            session.commit()
            return sent_count
