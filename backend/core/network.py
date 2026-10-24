"""
本机网络多链路状态探测引擎（Tailscale、ZeroTier 虚拟专网、物理 Wi-Fi/以太网与本机环回）。
"""

from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore[assignment]

from backend.core.logger import logger

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


def parse_ips(ip_str: Optional[str]) -> list[str]:
    """解析以逗号分隔的 IP 字符串为去重列表。"""
    if not ip_str:
        return []
    result: list[str] = []
    for item in ip_str.split(","):
        cleaned = item.strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def merge_ips(existing_ip_str: Optional[str], new_ip: Optional[str]) -> str:
    """合并新 IP 至现有的逗号分隔字符串中并去重。"""
    ips = parse_ips(existing_ip_str)
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
        # 2. 动态识别本地真实 ZeroTier 虚拟网卡所属子网
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

def is_local_port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.05) -> bool:
    """
    极速检测本地指定端口是否处于侦听状态（<1ms）。
    在启动前或跨服务探活前使用，避免 psutil 全网卡扫描或长时间 HTTP 连接超时。
    """
    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, port)) == 0
    except Exception as exc:
        logger.debug("检测本地端口 %s:%d 侦听状态异常: %s", host, port, exc)
        return False

