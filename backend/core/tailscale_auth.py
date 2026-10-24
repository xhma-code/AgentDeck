"""
Tailscale 虚拟局域网自动发现、60秒一次性配对码鉴权、纯 Python 二维码生成与 Windows 防休眠锁。

遵循安全与体验规范：
1. 强制仅绑定本机 Tailscale 网卡 IP (`100.x.y.z`) 与 `127.0.0.1`，严禁绑定 `0.0.0.0`。
2. 二维码 URL 仅携带 60 秒有效的一次性 `pair_code`，手机端扫码后置换长期 Token。
3. 内置 Windows `SetThreadExecutionState` 防休眠锁，防止人离开电脑后 PC 休眠断联。
"""

from __future__ import annotations

import hashlib
import ipaddress
import secrets
import time
import uuid
from typing import Optional

from backend.core.awake_lock import WindowsAwakeLock
from backend.core.database import MetaDatabase, utc_now_iso
from backend.core.logger import logger
from backend.core.network import (
    TAILSCALE_CGNAT_NET,
    TailscaleStatus,
    detect_primary_lan_ip,
    detect_tailscale_status,
    detect_zerotier_ip,
    detect_zerotier_network,
    is_trusted_vpn_network,
    merge_ips,
    parse_ips,
)
from backend.core.qr_box import render_terminal_qr_box

# 兼容私有辅助函数旧引用
_parse_ips = parse_ips
_merge_ips = merge_ips


class PairingAuthManager:
    """
    设备扫码配对与访问令牌鉴权管理器。
    支持：
    1. 生成 60 秒有效的一次性短配对码（One-Time Pair Code）。
    2. 置换长期设备访问 Token（SHA-256 哈希落库至 `t_device_session`）。
    3. 校验 HTTP Request / WebSocket 握手的 Token 与 Origin 安全边界。
    """

    def __init__(self, meta_db: MetaDatabase, pair_code_ttl_sec: int = 60) -> None:
        self.meta_db = meta_db
        self.pair_code_ttl_sec = pair_code_ttl_sec
        # pair_code -> expires_at_epoch
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
        _ = self.bootstrap_pair_code  # 触发惰性刷新
        return self._pending_codes.get(self._active_code, time.time() + self.pair_code_ttl_sec)

    def generate_pair_code(self, ttl_sec: Optional[int] = None, invalidate_prev: bool = False) -> str:
        """生成一次性短配对码（默认 10 分钟有效，首次使用后立即销毁）。"""
        now = time.time()
        # 清理已过期的配对码
        expired = [k for k, exp in self._pending_codes.items() if exp < now]
        for k in expired:
            self._pending_codes.pop(k, None)

        if invalidate_prev and hasattr(self, "_active_code") and self._active_code:
            # 主动作废上一任配对码，杜绝历史二维码重放
            self._pending_codes.pop(self._active_code, None)

        code = secrets.token_urlsafe(12)
        self._pending_codes[code] = now + (ttl_sec or self.pair_code_ttl_sec)
        self._active_code = code
        return code

    def refresh_pair_code(self, ttl_sec: Optional[int] = None) -> tuple[str, float]:
        """主动滚动刷新一次性配对码（立即销毁前序旧码，返回 (新配对码, 过期时间戳)）。"""
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
        """用一次性配对码置换长期 Session Token（一次性消费，防止二维码截图重放）。"""
        now = time.time()
        exp = self._pending_codes.get(pair_code)
        if exp is None or exp < now:
            self._pending_codes.pop(pair_code, None)
            return None

        # 消费成功后立即移除一次性配对码，并自动生成一个新的 60 秒配对码供下台设备扫码
        self._pending_codes.pop(pair_code, None)
        self._active_code = self.generate_pair_code(ttl_sec=self.pair_code_ttl_sec)

        device_id = str(uuid.uuid4())
        raw_token = f"agd_{secrets.token_urlsafe(32)}"
        token_hash = self._hash_token(raw_token)
        iso_now = utc_now_iso()

        with self.meta_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO t_device_session
                (device_id, device_name, auth_token_hash, tailscale_ip, created_at, last_active_at, is_revoked)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (device_id, device_name[:120], token_hash, client_ip, iso_now, iso_now),
            )

        logger.info(f"新移动设备配对成功: {device_name} (IP: {client_ip}, DeviceID: {device_id[:8]})")
        return {
            "device_id": device_id,
            "token": raw_token,
            "device_name": device_name,
        }

    def verify_token(self, raw_token: Optional[str], client_ip: str = "") -> bool:
        """校验访问 Token 是否有效，若有效则更新最后活跃时间并动态增量合并漫游 IP。"""
        if not raw_token:
            return False
        token_hash = self._hash_token(raw_token)
        with self.meta_db.connect() as conn:
            row = conn.execute(
                """
                SELECT device_id, tailscale_ip FROM t_device_session
                WHERE auth_token_hash = ? AND is_revoked = 0
                """,
                (token_hash,),
            ).fetchone()
            if not row:
                return False

            # 多网络漫游（Tailscale / ZeroTier / Wi-Fi）智能合并 IP，避免相互覆盖
            merged_ips = _merge_ips(row["tailscale_ip"], client_ip)

            conn.execute(
                """
                UPDATE t_device_session
                SET last_active_at = ?, tailscale_ip = ?
                WHERE device_id = ?
                """,
                (utc_now_iso(), merged_ips, row["device_id"]),
            )
            return True

    def issue_token_for_trusted_vpn_ip(self, client_ip: str, device_name: str = "PWA Standalone") -> Optional[str]:
        """为已在数据库完成扫码配对且未吊销的受信任 VPN IP 设备颁发/续订合法访问 Token。"""
        if not client_ip or not is_trusted_vpn_network(client_ip):
            return None

        with self.meta_db.connect() as conn:
            rows = conn.execute(
                """
                SELECT device_id, device_name, tailscale_ip FROM t_device_session
                WHERE is_revoked = 0
                ORDER BY created_at DESC
                """
            ).fetchall()
            if not rows:
                return None

            # 精确匹配当前 client_ip 所在的已授权设备记录，严禁盲目兜底覆写以防多设备互踢
            target_row = None
            for r in rows:
                bound_ips = _parse_ips(r["tailscale_ip"])
                if client_ip in bound_ips:
                    target_row = r
                    break
            if not target_row:
                return None

            device_id = target_row["device_id"]
            raw_token = f"agd_{secrets.token_urlsafe(32)}"
            token_hash = self._hash_token(raw_token)
            iso_now = utc_now_iso()
            new_ips = _merge_ips(target_row["tailscale_ip"], client_ip)

            conn.execute(
                """
                UPDATE t_device_session
                SET auth_token_hash = ?, last_active_at = ?, tailscale_ip = ?
                WHERE device_id = ?
                """,
                (token_hash, iso_now, new_ips, device_id),
            )
            logger.info(f"受信任虚拟专网设备 PWA 沙盒无缝自愈授权: {target_row['device_name']} (IP: {client_ip})")
            return raw_token

    def is_paired_trusted_vpn_ip(self, client_ip: str) -> bool:
        """检查某个虚拟专网（Tailscale 或 ZeroTier）节点 IP 是否已完成过扫码配对且未被吊销。"""
        if not client_ip or not is_trusted_vpn_network(client_ip):
            return False

        with self.meta_db.connect() as conn:
            rows = conn.execute(
                """
                SELECT device_id, tailscale_ip FROM t_device_session
                WHERE is_revoked = 0
                ORDER BY created_at DESC
                """
            ).fetchall()
            if not rows:
                return False
            for r in rows:
                bound_ips = _parse_ips(r["tailscale_ip"])
                if client_ip in bound_ips:
                    return True
            return False

    # 保持历史同名别名，确保完全向后兼容
    is_paired_tailscale_ip = is_paired_trusted_vpn_ip

    @staticmethod
    def is_trusted_origin(origin: Optional[str], allowed_hosts: list[str]) -> bool:
        """校验 WebSocket 握手的 Origin 头，防止跨站 WebSocket 劫持 (CSWSH)。"""
        if not origin:
            return True
        from urllib.parse import urlparse

        parsed = urlparse(origin)
        host = (parsed.hostname or "").lower()
        if host in ("127.0.0.1", "localhost") or host.endswith(".ts.net"):
            return True
        if host in [h.lower() for h in allowed_hosts]:
            return True
        # Tailscale (100.64.0.0/10) 及 ZeroTier 虚拟私网段严格校验
        if is_trusted_vpn_network(host):
            return True
        return False
