# -*- coding: utf-8 -*-
"""
OpenCode HTTP Bridge 模块。
负责对接 opencode serve 的本地 HTTP/SSE 桥接客户端（遵循 Spec 与 OpenCode 1.x/2.x 规范）。
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import socket
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

from backend.core.logger import log_structured_error, logger
from backend.core.network import is_local_port_listening

class OpenCodeHttpBridge:
    """
    对接 `opencode serve` 的本地 HTTP/SSE 桥接客户端（遵循 Spec 与 OpenCode 1.x/2.x 规范）。
    支持：
    1. 自动探测 `~/.local/state/opencode/service.json` 获取动态端口与 Basic Auth 密码。
    2. 探测本地服务是否在线（探测 `/api/session` 或 `/session`）。
    3. 调用 `POST /api/session/{id}/prompt` 或 `POST /session/{id}/message` 发送指令。
    4. 调用 `POST /api/session/{id}/permission/{perm_id}/reply` 提交逐条工具审批。
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        password: Optional[str] = None,
        state_dir: Optional[Path] = None,
    ) -> None:
        self._custom_base_url = base_url
        self._custom_password = password
        self.state_dir = state_dir or Path(os.path.expanduser("~/.local/state/opencode"))
        self._service_cache: Optional[dict[str, Any]] = None
        self._service_cache_mtime: float = 0.0
        self._server_running_cache: Optional[bool] = None
        self._server_running_cache_ts: float = 0.0

    def _resolve_connection_info(self) -> tuple[str, str]:
        """动态解析当前的 base_url 与 password（支持环境变量、service.json 与显式参数）。"""
        env_url = os.environ.get("OPENCODE_SERVE_URL")
        env_pwd = os.environ.get("OPENCODE_SERVE_PASSWORD")
        if self._custom_base_url:
            return self._custom_base_url.rstrip("/"), self._custom_password or env_pwd or ""
        if env_url:
            return env_url.rstrip("/"), env_pwd or ""

        service_json_path = self.state_dir / "service.json"
        try:
            if service_json_path.exists():
                mtime = service_json_path.stat().st_mtime
                if self._service_cache is None or mtime != self._service_cache_mtime:
                    with open(service_json_path, "r", encoding="utf-8") as f:
                        self._service_cache = json.load(f)
                    self._service_cache_mtime = mtime
                if self._service_cache and isinstance(self._service_cache, dict):
                    url = str(self._service_cache.get("url") or "").rstrip("/")
                    pwd = str(self._service_cache.get("password") or "")
                    if url:
                        return url, pwd
        except (OSError, json.JSONDecodeError) as exc:
            log_structured_error(
                problem="读取 OpenCode service.json 状态文件失败",
                cause=str(exc),
                fix="系统将回退到默认本地服务端口 (4096)。",
                exc=exc,
            )

        return "http://127.0.0.1:4096", ""

    @property
    def base_url(self) -> str:
        url, _ = self._resolve_connection_info()
        return url

    @property
    def password(self) -> str:
        _, pwd = self._resolve_connection_info()
        return pwd

    def _make_request(
        self,
        path: str,
        data: Optional[bytes] = None,
        method: str = "GET",
    ) -> urllib.request.Request:
        base_url, password = self._resolve_connection_info()
        url = f"{base_url}{path}"
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if password:
            token = base64.b64encode(f"opencode:{password}".encode("utf-8")).decode("utf-8")
            req.add_header("Authorization", f"Basic {token}")
        return req

    def is_server_running(self) -> bool:
        """探测本地 opencode serve HTTP 服务是否在线（带 5s 缓存与端口快速预检）。"""
        now = time.monotonic()
        if self._server_running_cache is not None and (now - self._server_running_cache_ts < 5.0):
            return self._server_running_cache

        base_url, _ = self._resolve_connection_info()
        # 极速 Socket 预检：若端口未开放监听，杜绝 urllib 阻塞 1.6 秒
        try:
            parts = urllib.parse.urlsplit(base_url)
            host = parts.hostname or "127.0.0.1"
            port = parts.port or 4096
            if not is_local_port_listening(port, host=host, timeout=0.03):
                self._server_running_cache = False
                self._server_running_cache_ts = now
                return False
        except Exception as exc:
            logger.debug(f"OpenCode 探活端口解析/预检异常 ({base_url}): {exc}")
            self._server_running_cache = False
            self._server_running_cache_ts = now
            return False

        # 端口已开放时，进一步校验 HTTP 接口可用性（紧凑超时 0.3s）
        is_up = False
        try:
            req = self._make_request("/api/session", method="GET")
            with urllib.request.urlopen(req, timeout=0.3) as resp:
                if resp.status == 200:
                    is_up = True
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        except Exception as exc:
            logger.error("探测 OpenCode 2 API 遇到非网络异常: 服务地址=%s, 异常=%s", base_url, exc)

        if not is_up:
            try:
                req = self._make_request("/session", method="GET")
                with urllib.request.urlopen(req, timeout=0.3) as resp:
                    is_up = (resp.status == 200)
            except (urllib.error.URLError, TimeoutError, OSError):
                is_up = False
            except Exception as exc:
                logger.error("探测 OpenCode 1 API 遇到非网络异常: 服务地址=%s, 异常=%s", base_url, exc)
                is_up = False

        self._server_running_cache = is_up
        self._server_running_cache_ts = now
        return is_up

    def is_alive(self) -> bool:
        """兼容别名：探测本地 opencode serve HTTP 服务是否存活。"""
        return self.is_server_running()

    def create_session(self, title: str = "", directory: str = "") -> Optional[str]:
        """通过 OpenCode 官方 HTTP 接口创建会话（遵循 POST /api/session 规范）。"""
        try:
            payload: dict[str, Any] = {}
            if title:
                payload["title"] = title
            if directory:
                payload["location"] = {"directory": directory}
            data = json.dumps(payload).encode("utf-8")
            req = self._make_request("/api/session", data=data, method="POST")
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                if resp.status in (200, 201):
                    raw = resp.read().decode("utf-8", errors="replace")
                    doc = json.loads(raw) if raw else {}
                    if isinstance(doc, dict) and isinstance(doc.get("data"), dict):
                        return doc["data"].get("id")
        except Exception as exc:
            logger.debug(f"通过 OpenCode HTTP 接口创建会话跳过/失败: {exc}")
        return None

    def send_message(self, session_id: str, prompt: str, model_name: str = "") -> Optional[dict[str, Any]]:
        """发送消息：自适应 OpenCode 2 (/api/session/{id}/prompt) 与 OpenCode 1 (/session/{id}/message)。"""
        # 1. 若为 ses_ 前缀，优先调用 OpenCode 2.x API
        if session_id.startswith("ses"):
            clean_model = (model_name or "").strip()
            if clean_model and clean_model != "default":
                try:
                    model_data = json.dumps({"model": clean_model}).encode("utf-8")
                    req_m = self._make_request(f"/api/session/{session_id}/model", data=model_data, method="POST")
                    with urllib.request.urlopen(req_m, timeout=3.0) as resp:
                        pass
                except Exception as exc:
                    logger.debug(f"OpenCode 2 切换模型跳过/失败 ({session_id[:8]} -> {clean_model}): {exc}")

            try:
                body_v2: dict[str, Any] = {"text": prompt}
                data_v2 = json.dumps(body_v2).encode("utf-8")
                req_v2 = self._make_request(f"/api/session/{session_id}/prompt", data=data_v2, method="POST")
                with urllib.request.urlopen(req_v2, timeout=30.0) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                    return json.loads(raw) if raw else {"ok": True}
            except urllib.error.HTTPError as exc:
                err_body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
                log_structured_error(
                    problem=f"调用 OpenCode 2 API 发送消息失败 ({session_id[:8]})",
                    cause=f"{exc} {err_body}".strip(),
                    fix="已自动回退至 opencode run CLI 子进程执行。",
                    exc=exc,
                )
                return None
            except Exception as exc:
                logger.debug(f"OpenCode 2 API 遇到异常，回退 CLI: {exc}")
                return None

        # 2. 若非 ses_ 前缀（如 OpenCode 1.x UUID），尝试兼容 OpenCode 1.x HTTP 接口
        try:
            body_v1: dict[str, Any] = {
                "parts": [{"type": "text", "text": prompt}],
            }
            if model_name and model_name != "default":
                body_v1["model"] = model_name
            data_v1 = json.dumps(body_v1).encode("utf-8")
            req_v1 = self._make_request(f"/session/{session_id}/message", data=data_v1, method="POST")
            with urllib.request.urlopen(req_v1, timeout=30.0) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {"ok": True}
        except urllib.error.HTTPError as exc:
            logger.debug(f"OpenCode 1 API 不适用或返回 HTTP {exc.code} ({session_id[:8]})，平滑降级至 CLI")
            return None
        except Exception as exc:
            logger.debug(f"OpenCode 1 API 尝试跳过 ({session_id[:8]}): {exc}，平滑降级至 CLI")
            return None

    def respond_permission(self, session_id: str, permission_id: str, allow: bool) -> bool:
        """提交工具权限审批（优先 OpenCode 2 API，回退 OpenCode 1）。"""
        # 1. 尝试 OpenCode 2: POST /api/session/{sessionID}/permission/{requestID}/reply
        try:
            payload_v2 = json.dumps({"decision": "once" if allow else "reject"}).encode("utf-8")
            req_v2 = self._make_request(
                f"/api/session/{session_id}/permission/{permission_id}/reply",
                data=payload_v2,
                method="POST",
            )
            with urllib.request.urlopen(req_v2, timeout=3.0) as resp:
                if resp.status in (200, 204):
                    return True
        except urllib.error.HTTPError as exc:
            if exc.code not in (404, 405):
                log_structured_error(
                    problem=f"提交 OpenCode 2 工具权限审批失败 ({permission_id})",
                    cause=str(exc),
                    fix="请确认 opencode 正在运行。",
                    exc=exc,
                )
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        except Exception as exc:
            log_structured_error(
                problem=f"调用 OpenCode 2 API 发送消息非预期异常 ({session_id[:8]})",
                cause=str(exc),
                fix="系统已自动尝试回退模式或 CLI 执行。",
                exc=exc,
            )

        # 2. 回退 OpenCode 1: POST /session/{sessionID}/permissions/{permissionID}
        payload_v1 = json.dumps({"response": "once" if allow else "reject"}).encode("utf-8")
        req_v1 = self._make_request(
            f"/session/{session_id}/permissions/{permission_id}",
            data=payload_v1,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req_v1, timeout=3.0) as resp:
                return resp.status in (200, 204)
        except Exception as exc:
            log_structured_error(
                problem=f"提交 OpenCode HTTP 工具权限审批失败 ({permission_id})",
                cause=str(exc),
                fix="请确认 opencode serve 正在运行。",
                exc=exc,
            )
            return False

    def get_active_sessions(self) -> dict[str, str]:
        """获取当前由 OpenCode 进程运行的活跃会话 ID 及状态字典（对接 /api/session/active）。"""
        try:
            req = self._make_request("/api/session/active", method="GET")
            with urllib.request.urlopen(req, timeout=0.8) as resp:
                if resp.status == 200:
                    raw = resp.read().decode("utf-8", errors="replace")
                    doc = json.loads(raw)
                    data = doc.get("data")
                    if isinstance(data, dict):
                        # data: {"ses_xxx": {"type": "running"}}
                        return {
                            sid: str(info.get("type") or "running")
                            for sid, info in data.items()
                            if isinstance(info, dict)
                        }
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        except Exception as exc:
            log_structured_error(
                problem="获取 OpenCode 活跃会话状态非预期异常",
                cause=str(exc),
                fix="系统已自动跳过本次活跃状态检测。",
                exc=exc,
            )
        return {}


    def abort_session(self, session_id: str) -> bool:
        """调用官方 OpenCode API 中断指定会话正在运行的任务（遵循官方 POST /api/session/:id/interrupt 规范）。"""
        for path in (
            f"/api/session/{session_id}/interrupt",
            f"/api/session/{session_id}/abort",
            f"/session/{session_id}/abort",
        ):
            try:
                req = self._make_request(path, data=b"{}", method="POST")
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    if resp.status in (200, 204):
                        logger.info(f"已通过 OpenCode 官方 HTTP 接口 ({path}) 中断会话 {session_id[:8]}")
                        return True
            except urllib.error.HTTPError as exc:
                if exc.code not in (404, 405):
                    logger.debug(f"OpenCode abort 请求失败 ({path}): {exc}")
            except Exception as exc:
                logger.debug(f"OpenCode abort 异常 ({path}): {exc}")
        return False

    def rename_session(self, session_id: str, new_title: str) -> bool:
        """调用官方 OpenCode API 实时修改会话标题（遵循官方 PATCH /api/session/:id 规范）。"""
        payload_v2 = json.dumps({"title": new_title}).encode("utf-8")
        payload_v1 = json.dumps({"title": new_title, "name": new_title}).encode("utf-8")
        candidates = [
            (f"/api/session/{session_id}", "PATCH", payload_v2),
            (f"/api/session/{session_id}/rename", "POST", payload_v2),
            (f"/session/{session_id}/rename", "POST", payload_v1),
            (f"/session/{session_id}", "PATCH", payload_v2),
        ]
        for path, method, data in candidates:
            try:
                req = self._make_request(path, data=data, method=method)
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    if resp.status in (200, 204):
                        logger.info(f"已通过 OpenCode 官方 HTTP 接口 ({path}) 重命名会话: {session_id[:8]} -> {new_title}")
                        return True
            except urllib.error.HTTPError as exc:
                if exc.code not in (404, 405):
                    logger.debug(f"OpenCode rename 请求失败 ({path}): {exc}")
            except Exception as exc:
                logger.debug(f"OpenCode rename 异常 ({path}): {exc}")
        return False
