"""
Tailscale 虚拟局域网自动发现、60秒一次性配对码鉴权、纯 Python 二维码生成与 Windows 防休眠锁。

遵循 `/autoplan` 安全与体验规范：
1. 强制仅绑定本机 Tailscale 网卡 IP (`100.x.y.z`) 与 `127.0.0.1`，严禁绑定 `0.0.0.0`。
2. 二维码 URL 仅携带 60 秒有效的一次性 `pair_code`，手机端扫码后置换长期 Token。
3. 内置 Windows `SetThreadExecutionState` 防休眠锁，防止人离开电脑后 PC 休眠断联。
"""

from __future__ import annotations

import ctypes
import hashlib
import ipaddress
import json
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore[assignment]

from backend.core.database import MetaDatabase, utc_now_iso
from backend.core.logger import log_structured_error, logger

# Tailscale CGNAT 规范私网段 (100.64.0.0/10)
TAILSCALE_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")


@dataclass
class TailscaleStatus:
    """本机网络多链路状态信息（Tailscale / Wi-Fi 局域网 / ZeroTier 隧道）。"""

    is_online: bool
    tailscale_ip: str
    dns_name: str
    hostname: str
    bind_hosts: list[str]
    diagnostic_msg: str
    lan_ip: str = ""
    zerotier_ip: str = ""


def _parse_ips(ip_str: Optional[str]) -> list[str]:
    """解析以逗号分隔的 IP 字符串为去重列表。"""
    if not ip_str:
        return []
    result: list[str] = []
    for item in ip_str.split(","):
        cleaned = item.strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _merge_ips(existing_ip_str: Optional[str], new_ip: Optional[str]) -> str:
    """合并新 IP 至现有的逗号分隔字符串中并去重。"""
    ips = _parse_ips(existing_ip_str)
    if new_ip:
        cleaned_new = new_ip.strip()
        if cleaned_new and cleaned_new not in ips:
            ips.append(cleaned_new)
    return ",".join(ips)


def detect_zerotier_ip() -> str:
    """探测本机活跃的 ZeroTier 虚拟局域网 IPv4 地址。"""
    if not psutil:
        return ""
    try:
        for nic, addrs in psutil.net_if_addrs().items():
            nic_lower = nic.lower()
            if "zerotier" in nic_lower or nic_lower.startswith("zt"):
                for a in addrs:
                    if (
                        getattr(a.family, "name", "") == "AF_INET"
                        and not a.address.startswith(("127.", "169.254."))
                    ):
                        return a.address
    except Exception as e:
        logger.debug("探测 ZeroTier IP 发生异常: %s", e)
    return ""


def detect_zerotier_network() -> Optional[ipaddress.IPv4Network]:
    """探测本机活跃的 ZeroTier 虚拟网卡子网，供对等鉴权校验。"""
    if not psutil:
        return None
    try:
        for nic, addrs in psutil.net_if_addrs().items():
            nic_lower = nic.lower()
            if "zerotier" in nic_lower or nic_lower.startswith("zt"):
                for a in addrs:
                    if (
                        getattr(a.family, "name", "") == "AF_INET"
                        and not a.address.startswith(("127.", "169.254."))
                    ):
                        netmask = getattr(a, "netmask", None) or "255.255.255.0"
                        return ipaddress.IPv4Network(f"{a.address}/{netmask}", strict=False)
    except Exception as e:
        logger.debug("探测 ZeroTier 网络发生异常: %s", e)
    return None


def is_trusted_vpn_network(ip_str: str) -> bool:
    """判断 IP 是否属于 Tailscale (100.64.0.0/10) 或动态识别的 ZeroTier 虚拟专网网卡网段。"""
    if not ip_str:
        return False
    try:
        ip_obj = ipaddress.ip_address(ip_str)
        # 1. Tailscale CGNAT 规范私网段 (100.64.0.0/10)
        if ip_obj in TAILSCALE_CGNAT_NET:
            return True
        # 2. 动态识别本地真实 ZeroTier 虚拟网卡所属子网（杜绝硬编码 10.0.0.0/8 导致普通 10.x 局域网被误放行）
        zt_net = detect_zerotier_network()
        if zt_net and ip_obj in zt_net:
            return True
        return False
    except ValueError:
        return False


def detect_primary_lan_ip() -> str:
    """探测本机物理局域网 IPv4 地址 (优先匹配 Wi-Fi / WLAN 或物理网卡)，严格排除任何虚拟隧道网卡。"""
    if not psutil:
        return ""
    try:
        virtual_keywords = [
            "tailscale", "zerotier", "vmnet", "vethernet",
            "loopback", "meta", "tun", "tap", "wsl", "hyper-v"
        ]
        wifi_candidates: list[str] = []
        eth_candidates: list[str] = []

        for nic, addrs in psutil.net_if_addrs().items():
            nic_lower = nic.lower()
            if any(bad in nic_lower for bad in virtual_keywords):
                continue
            for a in addrs:
                if (
                    getattr(a.family, "name", "") == "AF_INET"
                    and not a.address.startswith(("127.", "169.254."))
                    and a.address.startswith(("192.168.", "10.", "172."))
                ):
                    # 区分优先权重：优先 WLAN/Wi-Fi，其次有线以太网
                    if any(w in nic_lower for w in ["wlan", "wi-fi", "wifi", "wireless"]):
                        wifi_candidates.append(a.address)
                    else:
                        eth_candidates.append(a.address)

        if wifi_candidates:
            return wifi_candidates[0]
        if eth_candidates:
            return eth_candidates[0]
    except Exception as e:
        logger.debug("探测物理局域网 IP 发生异常: %s", e)
    return ""


def detect_tailscale_status() -> TailscaleStatus:
    """
    探测本机网络状态，优先绑定 Tailscale、ZeroTier、物理 Wi-Fi 与本机环回。
    严禁绑定 `0.0.0.0`。
    """
    lan_ip = detect_primary_lan_ip()
    zt_ip = detect_zerotier_ip()
    ts_bin = shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"
    try:
        proc = subprocess.run(
            [ts_bin, "status", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3.0,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout)
            backend_state = data.get("BackendState", "")
            self_node = data.get("Self", {}) or {}
            ips: list[str] = self_node.get("TailscaleIPs", []) or []
            ipv4 = next((ip for ip in ips if "." in ip), ips[0] if ips else "")
            dns_name = (self_node.get("DNSName") or "").rstrip(".")
            hostname = self_node.get("HostName") or "PC"

            if backend_state == "Running" and ipv4:
                hosts = [ipv4, "127.0.0.1"]
                if lan_ip and lan_ip not in hosts:
                    hosts.append(lan_ip)
                if zt_ip and zt_ip not in hosts:
                    hosts.append(zt_ip)
                return TailscaleStatus(
                    is_online=True,
                    tailscale_ip=ipv4,
                    dns_name=dns_name,
                    hostname=hostname,
                    bind_hosts=hosts,
                    diagnostic_msg=f"Tailscale 已就绪: {ipv4}" + (f" ({dns_name})" if dns_name else ""),
                    lan_ip=lan_ip,
                    zerotier_ip=zt_ip,
                )
    except Exception as exc:
        logger.info(
            "未检测到活跃的 Tailscale 虚拟网卡 (%s)，将尝试绑定 ZeroTier、局域网或 127.0.0.1。",
            exc,
        )

    hosts = ["127.0.0.1"]
    if lan_ip and lan_ip not in hosts:
        hosts.append(lan_ip)
    if zt_ip and zt_ip not in hosts:
        hosts.append(zt_ip)
    return TailscaleStatus(
        is_online=False,
        tailscale_ip="127.0.0.1",
        dns_name="localhost",
        hostname="LocalPC",
        bind_hosts=hosts,
        diagnostic_msg="【网络提示】未检测到活跃 Tailscale，已启用 Wi-Fi / ZeroTier 与本机访问",
        lan_ip=lan_ip,
        zerotier_ip=zt_ip,
    )


class WindowsAwakeLock:
    """Windows 防休眠锁（SetThreadExecutionState），防止离开电脑躺沙发时 PC 自动睡眠断网。"""

    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ES_AWAYMODE_REQUIRED = 0x00000040

    def __init__(self) -> None:
        self.is_active: bool = False

    def acquire(self) -> bool:
        """开启防休眠保护（不强制亮屏，仅保持系统与网络唤醒）。"""
        if sys.platform != "win32":
            return False
        try:
            flags = self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED
            res = ctypes.windll.kernel32.SetThreadExecutionState(flags)
            self.is_active = bool(res)
            if self.is_active:
                logger.info("已启用 Windows 防休眠锁（保持后台网络与 Agent 持续运行，允许显示器熄屏）")
            return self.is_active
        except Exception as exc:
            log_structured_error(
                problem="启用 Windows 防休眠锁失败",
                cause=str(exc),
                fix="可在 Windows 电源设置中手动调整睡眠时间。",
                exc=exc,
            )
            return False

    def release(self) -> None:
        """释放防休眠保护，恢复系统默认电源策略。"""
        if sys.platform != "win32" or not self.is_active:
            return
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(self.ES_CONTINUOUS)
            self.is_active = False
            logger.info("已释放 Windows 防休眠锁")
        except Exception as exc:
            log_structured_error(
                problem="释放 Windows 防休眠锁异常",
                cause=str(exc),
                fix="系统将在进程退出后由操作系统自动回收电源锁。",
                exc=exc,
            )


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
        """为已在数据库完成扫码配对且未吊销的受信任 VPN IP 设备颁发/续订合法访问 Token（用于 PWA 添加到主屏幕独立沙盒免配对自愈）。"""
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
            # 原生客户端或同源无 Origin 时放行（后续仍需校验 Token 或本机环回）
            return True
        from urllib.parse import urlparse

        parsed = urlparse(origin)
        host = (parsed.hostname or "").lower()
        if host in ("127.0.0.1", "localhost") or host.endswith(".ts.net"):
            return True
        if host in [h.lower() for h in allowed_hosts]:
            return True
        # Tailscale (100.64.0.0/10) 及 ZeroTier (10.0.0.0/8 等) 虚拟私网段严格校验
        if is_trusted_vpn_network(host):
            return True
        return False


def render_terminal_qr_box(url: str, title: str = "AgentDeck 手机扫码直连") -> str:
    """
    生成终端高亮配对卡片（若环境装有 qrcode 库则输出紧凑半高块字符二维码，
    否则输出醒目的高对比度边框配对指引与网页二维码入口）。
    """
    lines = [
        "╔══════════════════════════════════════════════════════════════════════╗",
        f"║  📱 {title:<61}║",
        "╠══════════════════════════════════════════════════════════════════════╣",
        f"║  直连地址: {url:<58}║",
        "╚══════════════════════════════════════════════════════════════════════╝",
    ]
    try:
        import qrcode  # type: ignore

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        qr_lines: list[str] = []
        for r in range(0, len(matrix), 2):
            row_top = matrix[r]
            row_bot = matrix[r + 1] if r + 1 < len(matrix) else [False] * len(row_top)
            chars: list[str] = []
            for top, bot in zip(row_top, row_bot):
                if top and bot:
                    chars.append("█")
                elif top and not bot:
                    chars.append("▀")
                elif not top and bot:
                    chars.append("▄")
                else:
                    chars.append(" ")
            qr_lines.append("  " + "".join(chars))
        return "\n".join(lines + qr_lines)
    except ImportError:
        return "\n".join(lines)
