# -*- coding: utf-8 -*-
"""
远程移动设备扫码配对与访问令牌鉴权守卫（基于规范表 device_sessions）。

安全边界：
1. 60 秒一次性 `pair_code` 扫码置换长期设备 Token（`agd_` 前缀，仅 SHA-256 哈希落库）。
2. 本机 `127.0.0.1` / `::1` 访问 Web 管理端免密；非本机必须通过有效设备 Token 或已配对 VPN IP 校验。
3. 严格与 MCP Project-scoped Token（`agmcp_`）隔离，互不混用。
"""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from typing import Optional
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.core.logger import logger
from backend.core.network import is_trusted_vpn_network, merge_ips, parse_ips
from backend.db.base import utc_now_iso
from backend.db.models.system import DeviceSessionModel


class DevicePairingAuthManager:
    """基于 SQLAlchemy `device_sessions` 表的远程移动设备扫码配对与访问令牌管理器。"""

    def __init__(self, session_factory: sessionmaker[Session], pair_code_ttl_sec: int = 60) -> None:
        self.session_factory = session_factory
        self.pair_code_ttl_sec = pair_code_ttl_sec
        self._pending_codes: dict[str, float] = {}
        self._active_code: str = self.generate_pair_code(ttl_sec=self.pair_code_ttl_sec)

    @property
    def bootstrap_pair_code(self) -> str:
        """获取当前有效的一次性配对码（若已过期或已使用则自动滚动刷新）。"""
        now = time.time()
        exp = self._pending_codes.get(self._active_code, 0.0)
        if not self._active_code or exp <= now:
            self._active_code = self.generate_pair_code(ttl_sec=self.pair_code_ttl_sec)
        return self._active_code

    @property
    def bootstrap_pair_code_expires_at(self) -> float:
        """获取当前有效配对码的绝对过期时间戳 (Unix Epoch 秒)。"""
        _ = self.bootstrap_pair_code
        return self._pending_codes.get(self._active_code, time.time() + self.pair_code_ttl_sec)

    def generate_pair_code(self, ttl_sec: Optional[int] = None, invalidate_prev: bool = False) -> str:
        """生成一次性短配对码（首次使用后立即销毁）。"""
        now = time.time()
        expired = [k for k, exp in self._pending_codes.items() if exp < now]
        for k in expired:
            self._pending_codes.pop(k, None)

        if invalidate_prev and hasattr(self, "_active_code") and self._active_code:
            self._pending_codes.pop(self._active_code, None)

        code = secrets.token_urlsafe(12)
        self._pending_codes[code] = now + (ttl_sec or self.pair_code_ttl_sec)
        self._active_code = code
        return code

    def refresh_pair_code(self, ttl_sec: Optional[int] = None) -> tuple[str, float]:
        """主动滚动刷新一次性配对码（立即销毁前序旧码）。"""
        new_code = self.generate_pair_code(ttl_sec=ttl_sec, invalidate_prev=True)
        exp = self._pending_codes.get(new_code, time.time() + (ttl_sec or self.pair_code_ttl_sec))
        return new_code, exp

    @staticmethod
    def _hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    def exchange_pair_code(
        self,
        pair_code: str,
        device_name: str = "Mobile Browser",
        client_ip: str = "",
    ) -> Optional[dict[str, str]]:
        """用一次性配对码置换长期设备 Session Token（一次性消费，防重放）。"""
        now = time.time()
        exp = self._pending_codes.get(pair_code)
        if exp is None or exp < now:
            self._pending_codes.pop(pair_code, None)
            return None

        self._pending_codes.pop(pair_code, None)
        self._active_code = self.generate_pair_code(ttl_sec=self.pair_code_ttl_sec)

        device_id = str(uuid.uuid4())
        raw_token = f"agd_{secrets.token_urlsafe(32)}"
        token_hash = self._hash_token(raw_token)
        iso_now = utc_now_iso()

        with self.session_factory() as session:
            record = DeviceSessionModel(
                device_id=device_id,
                device_name=(device_name or "Mobile Browser")[:120],
                auth_token_hash=token_hash,
                tailscale_ip=client_ip or "",
                created_at=iso_now,
                last_active_at=iso_now,
                is_revoked=0,
            )
            session.add(record)
            session.commit()

        logger.info(f"新移动设备配对成功: {device_name} (IP: {client_ip}, DeviceID: {device_id[:8]})")
        return {
            "device_id": device_id,
            "token": raw_token,
            "device_name": device_name,
        }

    def verify_token(self, raw_token: Optional[str], client_ip: str = "") -> bool:
        """校验访问 Token 是否有效，若有效则更新最后活跃时间并合并漫游 IP。"""
        if not raw_token:
            return False
        token_hash = self._hash_token(raw_token)
        with self.session_factory() as session:
            stmt = select(DeviceSessionModel).where(
                DeviceSessionModel.auth_token_hash == token_hash,
                DeviceSessionModel.is_revoked == 0,
            )
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                return False

            row.tailscale_ip = merge_ips(row.tailscale_ip, client_ip)
            row.last_active_at = utc_now_iso()
            session.commit()
            return True

    def issue_token_for_trusted_vpn_ip(self, client_ip: str, device_name: str = "PWA Standalone") -> Optional[str]:
        """为已在数据库完成扫码配对且未吊销的受信任 VPN IP 设备颁发/续订合法访问 Token。"""
        if not client_ip or not is_trusted_vpn_network(client_ip):
            return None

        with self.session_factory() as session:
            stmt = (
                select(DeviceSessionModel)
                .where(DeviceSessionModel.is_revoked == 0)
                .order_by(DeviceSessionModel.created_at.desc())
            )
            rows = list(session.execute(stmt).scalars().all())
            if not rows:
                return None

            target_row = None
            for r in rows:
                if client_ip in parse_ips(r.tailscale_ip):
                    target_row = r
                    break
            if target_row is None:
                return None

            raw_token = f"agd_{secrets.token_urlsafe(32)}"
            target_row.auth_token_hash = self._hash_token(raw_token)
            target_row.last_active_at = utc_now_iso()
            target_row.tailscale_ip = merge_ips(target_row.tailscale_ip, client_ip)
            session.commit()
            logger.info(f"受信任虚拟专网设备 PWA 沙盒无缝自愈授权: {target_row.device_name} (IP: {client_ip})")
            return raw_token

    def is_paired_trusted_vpn_ip(self, client_ip: str) -> bool:
        """检查某个虚拟专网（Tailscale 或 ZeroTier）节点 IP 是否已完成过扫码配对且未被吊销。"""
        if not client_ip or not is_trusted_vpn_network(client_ip):
            return False

        with self.session_factory() as session:
            stmt = (
                select(DeviceSessionModel)
                .where(DeviceSessionModel.is_revoked == 0)
                .order_by(DeviceSessionModel.created_at.desc())
            )
            rows = list(session.execute(stmt).scalars().all())
            for r in rows:
                if client_ip in parse_ips(r.tailscale_ip):
                    return True
            return False

    def list_devices(self) -> list[dict[str, object]]:
        """列出全部已配对设备记录。"""
        with self.session_factory() as session:
            stmt = select(DeviceSessionModel).order_by(DeviceSessionModel.last_active_at.desc())
            rows = list(session.execute(stmt).scalars().all())
            return [
                {
                    "device_id": r.device_id,
                    "device_name": r.device_name,
                    "tailscale_ip": r.tailscale_ip,
                    "created_at": r.created_at,
                    "last_active_at": r.last_active_at,
                    "is_revoked": bool(r.is_revoked),
                }
                for r in rows
            ]

    def revoke_device(self, device_id: str) -> bool:
        """吊销指定设备访问权限。"""
        with self.session_factory() as session:
            row = session.get(DeviceSessionModel, device_id)
            if row is None:
                return False
            row.is_revoked = 1
            session.commit()
            return True

    @staticmethod
    def is_trusted_origin(origin: Optional[str], allowed_hosts: list[str]) -> bool:
        """校验 Origin 头，防止跨站劫持。"""
        if not origin:
            return True
        parsed = urlparse(origin)
        host = (parsed.hostname or "").lower()
        if host in ("127.0.0.1", "localhost") or host.endswith(".ts.net"):
            return True
        if host in [h.lower() for h in allowed_hosts]:
            return True
        if is_trusted_vpn_network(host):
            return True
        return False

