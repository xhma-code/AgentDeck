"""
Antigravity 本地 Language Server Connect-RPC 通信协议与进程/端口探测桥接器。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.request
from typing import Any, Optional

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore[assignment]

from backend.core.logger import log_structured_error, logger

CSRF_TOKEN_RE = re.compile(r"--csrf_token[\s=]+([0-9a-fA-F-]{8,})")
LS_SERVICE_PREFIX = "/exa.language_server_pb.LanguageServerService"

LS_MODEL_ID_MAP: dict[str, str] = {
    "model_placeholder_m418": "gemini-4-argon-medium",
    "model_placeholder_m417": "gemini-4-argon-high",
    "model_placeholder_m419": "gemini-4-argon-low",
    "model_placeholder_m318": "gemini-3.8-flash-high",
    "model_placeholder_m319": "gemini-3.8-flash-medium",
    "model_placeholder_m320": "gemini-3.8-flash-low",
    "model_placeholder_m400": "claude-opus-5-5-low",
    "model_placeholder_m401": "claude-opus-5-5-medium",
    "model_placeholder_m402": "claude-opus-5-5-high",
    "model_placeholder_m403": "claude-sonnet-5-5-low",
    "model_placeholder_m404": "claude-sonnet-5-5-medium",
    "model_placeholder_m405": "claude-sonnet-5-5-high",
    "model_openai_gpt_oss_120b_medium": "gpt-oss-120b-medium",
}

MODEL_ID_TO_LS_MAP: dict[str, str] = {
    v.lower(): k.upper() for k, v in LS_MODEL_ID_MAP.items()
}


def encode_connect_envelope(body: dict[str, Any]) -> bytes:
    """编码 Connect-RPC 5 字节流式协议帧 (`[0x00][uint32_be_len][json_bytes]`)。"""
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return b"\x00" + len(raw).to_bytes(4, "big") + raw


class AntigravityLanguageServerBridge:
    """
    轻量级 Hub Language Server 桥接器：
    1. 自动嗅探本机运行中的 `language_server` 进程 `--csrf_token` 与本地监听端口。
    2. 支持通过 Connect-RPC 协议直接向桌面端活跃的 Antigravity IDE 会话发送指令与取消任务。
    """

    _cached_hub: Optional[dict[str, Any]] = None
    _last_detect_ts: float = 0.0

    def __init__(self, cache_ttl_sec: float = 15.0) -> None:
        self.cache_ttl_sec = cache_ttl_sec

    @classmethod
    def get_cached_hub(cls) -> Optional[dict[str, Any]]:
        """非阻塞获取已缓存的 Hub LS 连接信息。"""
        return cls._cached_hub

    def detect_hub(self, force: bool = False) -> Optional[dict[str, Any]]:
        """探测本机运行中的 Antigravity Hub Language Server 端口与 CSRF Token。"""
        cls = self.__class__
        now = time.monotonic()
        if not force and cls._last_detect_ts > 0 and (now - cls._last_detect_ts) < self.cache_ttl_sec:
            return cls._cached_hub

        cls._last_detect_ts = now
        candidates = self._scan_ls_processes()
        if not candidates:
            cls._cached_hub = None
            return None

        for pid, csrf_token in candidates:
            ports = self._find_listening_ports(pid)
            for port in ports:
                scheme = self._probe_connect_port(port, csrf_token)
                if scheme:
                    hub_info = {
                        "pid": pid,
                        "port": port,
                        "csrf_token": csrf_token,
                        "scheme": scheme,
                    }
                    cls._cached_hub = hub_info
                    return hub_info

        cls._cached_hub = None
        return None

    @staticmethod
    def _scan_ls_processes() -> list[tuple[int, str]]:
        """扫描进程表提取 (pid, csrf_token)。"""
        results: list[tuple[int, str]] = []
        if psutil:
            try:
                for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                    try:
                        p_name = proc.info.get("name") or ""
                        if "language_server" in p_name.lower():
                            cmdline = " ".join(proc.info.get("cmdline") or [])
                            m = CSRF_TOKEN_RE.search(cmdline)
                            pid = proc.info.get("pid") or 0
                            if pid > 0 and m:
                                results.append((pid, m.group(1)))
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                if results:
                    return results
            except Exception as ps_exc:
                logger.debug(f"psutil 扫描 language_server 失败: {ps_exc}")

        try:
            if sys.platform == "win32":
                ps_bin = shutil.which("powershell.exe") or "powershell"
                ps_cmd = 'Get-CimInstance Win32_Process -Filter "Name LIKE \'%language_server%\'" | Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress'
                proc = subprocess.run([ps_bin, "-NoProfile", "-NonInteractive", "-Command", ps_cmd], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=2.5)
                out = (proc.stdout or "").strip()
                if out:
                    data = json.loads(out)
                    for r in (data if isinstance(data, list) else [data]):
                        if isinstance(r, dict):
                            pid = int(r.get("ProcessId") or 0)
                            m = CSRF_TOKEN_RE.search(str(r.get("CommandLine") or ""))
                            if pid > 0 and m:
                                results.append((pid, m.group(1)))
            else:
                proc = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=2.0)
                for line in (proc.stdout or "").splitlines():
                    if "language_server" in line and "csrf_token" in line:
                        m = CSRF_TOKEN_RE.search(line)
                        parts = line.strip().split(None, 1)
                        if m and parts and parts[0].isdigit():
                            results.append((int(parts[0]), m.group(1)))
        except Exception as exc:
            logger.debug(f"扫描 language_server 进程跳过: {exc}")
        return results

    @staticmethod
    def _find_listening_ports(pid: int) -> list[int]:
        """查找指定 PID 在本机监听的 TCP 端口列表。"""
        ports: list[int] = []
        if psutil:
            try:
                p = psutil.Process(pid)
                for conn in p.net_connections(kind="tcp"):
                    if conn.status == psutil.CONN_LISTEN and conn.laddr:
                        port = getattr(conn.laddr, "port", None)
                        if port and port not in ports:
                            ports.append(port)
                if ports:
                    return ports[:6]
            except Exception as ps_exc:
                logger.debug(f"psutil 查询 PID={pid} 端口异常: {ps_exc}")

        if sys.platform == "win32":
            try:
                proc = subprocess.run(
                    ["netstat", "-ano", "-p", "TCP"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=2.0,
                )
                pid_str = str(pid)
                for line in (proc.stdout or "").splitlines():
                    if "LISTENING" in line:
                        cols = line.split()
                        if len(cols) >= 5 and cols[-1] == pid_str:
                            local_addr = cols[1]
                            if ":" in local_addr:
                                port_str = local_addr.rsplit(":", 1)[-1]
                                if port_str.isdigit():
                                    port_int = int(port_str)
                                    if port_int not in ports:
                                        ports.append(port_int)
            except Exception as exc:
                logger.debug(f"netstat 查询端口失败: {exc}")
        return ports[:6]

    def _probe_connect_port(self, port: int, csrf_token: str) -> Optional[str]:
        """探测指定端口是否响应 Connect-RPC `GetUserStatus`。"""
        for scheme in ("https", "http"):
            ok, _ = self._raw_post(
                scheme=scheme,
                port=port,
                csrf_token=csrf_token,
                method="GetUserStatus",
                body={},
                timeout=0.8,
            )
            if ok:
                return scheme
        return None

    @staticmethod
    def _raw_post(
        scheme: str,
        port: int,
        csrf_token: str,
        method: str,
        body: dict[str, Any],
        timeout: float = 2.0,
    ) -> tuple[bool, Any]:
        url = f"{scheme}://127.0.0.1:{port}{LS_SERVICE_PREFIX}/{method}"
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Connect-Protocol-Version": "1",
                "X-Codeium-Csrf-Token": csrf_token,
            },
        )
        ctx = ssl._create_unverified_context() if scheme == "https" else None
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                status = getattr(resp, "status", 200)
                if 200 <= status < 300:
                    raw_resp = resp.read()
                    return True, raw_resp
        except Exception as exc:
            logger.debug(f"Connect-RPC POST {method} 失败 ({url}): {exc}")
        return False, None

    def send_cascade_message(
        self, cascade_id: str, prompt: str, model_id: str = ""
    ) -> bool:
        """通过 Connect-RPC 直接向运行中的 Antigravity IDE 会话发送用户指令。"""
        hub = self.detect_hub()
        if not hub:
            return False

        payload: dict[str, Any] = {
            "cascadeId": cascade_id,
            "items": [{"text": prompt}],
        }
        if model_id and model_id != "default":
            ls_model = MODEL_ID_TO_LS_MAP.get(model_id.lower(), model_id)
            if ls_model.lower().startswith("model_placeholder_"):
                ls_model = ls_model.upper()
            payload["cascadeConfig"] = {
                "plannerConfig": {
                    "requestedModel": {
                        "modelOrAlias": {
                            "model": ls_model,
                        }
                    },
                }
            }

        ok, _ = self._raw_post(
            scheme=hub["scheme"],
            port=hub["port"],
            csrf_token=hub["csrf_token"],
            method="SendUserCascadeMessage",
            body=payload,
            timeout=2.5,
        )
        if ok:
            logger.info(
                f"已通过 Language Server Connect-RPC (port={hub['port']}) 直接向 IDE 会话 {cascade_id[:8]} 发送指令"
            )
            return True
        log_structured_error(
            problem=f"通过 Connect-RPC 向 IDE 发送指令失败 (ID={cascade_id[:8]})",
            cause="Language Server 无响应或未在监听",
            fix="系统将自动回退至 Fork-to-CLI 安全克隆接力执行。",
        )
        return False

    def cancel_cascade(self, cascade_id: str) -> bool:
        """通过 Connect-RPC 取消运行中的 Antigravity IDE 会话。"""
        hub = self.detect_hub()
        if not hub:
            return False
        ok, _ = self._raw_post(
            scheme=hub["scheme"],
            port=hub["port"],
            csrf_token=hub["csrf_token"],
            method="CancelCascadeInvocation",
            body={"cascadeId": cascade_id},
            timeout=2.0,
        )
        if not ok:
            log_structured_error(
                problem=f"通过 Connect-RPC 取消 IDE 会话失败 (ID={cascade_id[:8]})",
                cause="Language Server 无响应或已离线",
                fix="系统将通过本地强杀进程树兜底处理。",
            )
        return ok

    def get_default_model_id(self) -> str:
        """从 LS Connect-RPC 获取当前服务端推荐或覆盖的默认模型 ID。"""
        if not hasattr(self, "_cached_default_model"):
            self.get_cascade_model_configs()
        def_raw = getattr(self, "_cached_default_model", "")
        if def_raw:
            mapped = LS_MODEL_ID_MAP.get(def_raw.lower())
            if mapped:
                return mapped
            return def_raw
        return ""

    def get_cascade_model_configs(self) -> list[dict[str, Any]]:
        """从 LS Connect-RPC 获取包含实时配额剩余比例的真实模型清单。"""
        now = time.monotonic()
        if hasattr(self, "_cached_models") and hasattr(self, "_cached_models_ts"):
            if now - self._cached_models_ts < 10.0:
                return self._cached_models

        hub = self.detect_hub()
        if not hub:
            self._cached_models = []
            self._cached_models_ts = now
            return []

        ok, raw = self._raw_post(
            scheme=hub["scheme"],
            port=hub["port"],
            csrf_token=hub["csrf_token"],
            method="GetCascadeModelConfigData",
            body={},
            timeout=2.5,
        )
        if not ok or not raw:
            self._cached_models = []
            self._cached_models_ts = now
            return []

        results = []
        try:
            data = json.loads(raw.decode("utf-8"))
            default_override = data.get("defaultOverrideModelConfig") or {}
            moa = default_override.get("modelOrAlias") or {}
            def_mid = moa.get("model") or moa.get("alias") or ""
            self._cached_default_model = def_mid

            sort_labels: list[str] = []
            for sort_group in (data.get("clientModelSorts") or []):
                for g in (sort_group.get("groups") or []):
                    sort_labels.extend(g.get("modelLabels") or [])
            self._cached_sort_labels = sort_labels

            for m in (data.get("clientModelConfigs") or []):
                if m.get("disabled") is True:
                    continue
                label = str(m.get("label") or "")
                mid = (
                    (m.get("modelOrAlias") or {}).get("model")
                    or (m.get("modelOrAlias") or {}).get("alias")
                    or ""
                )
                qi = m.get("quotaInfo") or {}
                rem_frac = qi.get("remainingFraction")
                quota = float(rem_frac) if rem_frac is not None else 1.0

                results.append(
                    {
                        "label": label,
                        "modelId": mid,
                        "tag": str(m.get("tagTitle") or ""),
                        "is_recommended": bool(m.get("isRecommended")),
                        "quota": round(max(0.0, min(1.0, quota)), 4),
                        "reset_time": qi.get("resetTime"),
                    }
                )
        except Exception as exc:
            logger.debug(f"解析 GetCascadeModelConfigData 异常: {exc}")

        if results:
            self._cached_models = results
            self._cached_models_ts = now
        return results
