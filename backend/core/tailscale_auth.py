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
import json
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from backend.core.database import MetaDatabase, utc_now_iso
from backend.core.logger import log_structured_error, logger


@dataclass
class TailscaleStatus:
    """本机 Tailscale 状态信息。"""

    is_online: bool
    tailscale_ip: str
    dns_name: str
    hostname: str
    bind_hosts: list[str]
    diagnostic_msg: str


def detect_tailscale_status() -> TailscaleStatus:
    """
    探测本机 Tailscale 虚拟局域网状态并返回允许绑定的安全网卡地址列表。
    若 Tailscale 未启动，安全回退为仅绑定 `127.0.0.1`（绝不绑定 `0.0.0.0`）。
    """
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
                return TailscaleStatus(
                    is_online=True,
                    tailscale_ip=ipv4,
                    dns_name=dns_name,
                    hostname=hostname,
                    bind_hosts=[ipv4, "127.0.0.1"],
                    diagnostic_msg=f"Tailscale 已就绪: {ipv4}" + (f" ({dns_name})" if dns_name else ""),
                )
    except Exception as exc:
        log_structured_error(
            problem="未检测到活跃的 Tailscale 虚拟网卡",
            cause=f"调用 tailscale status --json 失败 ({exc})",
            fix="请确保已安装并运行 'tailscale up' 登录；当前已安全降级为仅本机 127.0.0.1 访问。",
            exc=exc,
        )

    return TailscaleStatus(
        is_online=False,
        tailscale_ip="127.0.0.1",
        dns_name="localhost",
        hostname="LocalPC",
        bind_hosts=["127.0.0.1"],
        diagnostic_msg="【网络受限】未检测到活跃 Tailscale，当前仅绑定 127.0.0.1（执行 'tailscale up' 后重启可开启手机远程扫码）",
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
        """获取当前有效的 60 秒一次性配对码（若已过期或已使用则自动滚动刷新）。"""
        now = time.time()
        exp = self._pending_codes.get(self._active_code, 0.0)
        if not self._active_code or exp <= now:
            self._active_code = self.generate_pair_code(ttl_sec=self.pair_code_ttl_sec)
        return self._active_code

    def generate_pair_code(self, ttl_sec: Optional[int] = None) -> str:
        """生成一次性短配对码（默认 60 秒有效，首次使用后立即销毁）。"""
        now = time.time()
        # 清理过期配对码
        expired = [k for k, exp in self._pending_codes.items() if exp < now]
        for k in expired:
            self._pending_codes.pop(k, None)

        code = secrets.token_urlsafe(12)
        self._pending_codes[code] = now + (ttl_sec or self.pair_code_ttl_sec)
        self._active_code = code
        return code

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
        """校验访问 Token 是否有效，若有效则更新最后活跃时间。"""
        if not raw_token:
            return False
        token_hash = self._hash_token(raw_token)
        with self.meta_db.connect() as conn:
            row = conn.execute(
                """
                SELECT device_id FROM t_device_session
                WHERE auth_token_hash = ? AND is_revoked = 0
                """,
                (token_hash,),
            ).fetchone()
            if not row:
                return False
            conn.execute(
                """
                UPDATE t_device_session
                SET last_active_at = ?, tailscale_ip = COALESCE(NULLIF(?, ''), tailscale_ip)
                WHERE device_id = ?
                """,
                (utc_now_iso(), client_ip, row["device_id"]),
            )
            return True

    def is_paired_tailscale_ip(self, client_ip: str) -> bool:
        """检查某个 Tailscale 节点 IP 是否已完成过扫码配对且未被吊销。"""
        if not client_ip or not client_ip.startswith("100."):
            return False
        with self.meta_db.connect() as conn:
            row = conn.execute(
                """
                SELECT device_id FROM t_device_session
                WHERE tailscale_ip = ? AND is_revoked = 0
                LIMIT 1
                """,
                (client_ip,),
            ).fetchone()
            return bool(row)

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
        # Tailscale CGNAT 网段 100.64.0.0/10
        if host.startswith("100."):
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
