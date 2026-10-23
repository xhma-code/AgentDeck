"""
OpenCode (`~/.local/share/opencode/opencode.db` & `opencode serve`) 适配器。

核心能力：
1. 兼容 OpenCode 1.x 与 2.x（SQLite 短连接读取 `session_v2` / `session`、`session_message` / `message` / `part`）。
2. 自动发现 `~/.local/state/opencode/service.json` 动态端口与 Basic Auth 凭据，对接 `opencode serve` 的 HTTP 接口支持实时消息与审批。
"""

from __future__ import annotations

import base64
import copy
import json
import os
import re
import shutil
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.adapters.base import (
    BaseAgentAdapter,
    BoundedLRUCache,
    ExecutionMode,
    SessionTokenUsage,
    UnifiedMessageItem,
    UnifiedSession,
    UnifiedToolCall,
    classify_tool_risk,
    count_diff_lines,
    extract_tool_file_path,
    extract_tool_summary_and_diff,
    resolve_model_context_limit,
    truncate_large_output,
)
from backend.core.database import safe_query_external_sqlite, utc_now_iso
from backend.core.logger import log_structured_error, logger


def build_opencode_unified_tool_call(
    tool_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    raw_output: str,
    call_status: str = "DONE",
) -> UnifiedToolCall:
    """统一提取并构造 OpenCode 的标准化工具调用对象，复用于 1.x 与 2.x 双协议。"""
    summary, diff_txt = extract_tool_summary_and_diff(tool_name, tool_input)
    add_c, del_c = count_diff_lines(diff_txt)
    f_path = extract_tool_file_path(tool_input)
    trunc_out, is_trunc, full_len = truncate_large_output(str(raw_output))
    risk = classify_tool_risk(tool_name, summary)
    return UnifiedToolCall(
        tool_id=tool_id,
        name=tool_name,
        args_summary=summary,
        diff_text=diff_txt,
        diff_add_count=add_c,
        diff_del_count=del_c,
        output=trunc_out,
        is_truncated=is_trunc,
        full_output_len=full_len,
        risk_level=risk,
        file_path=f_path,
        status=call_status,
    )


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
        """探测本地 opencode serve HTTP 服务是否在线（兼容 OpenCode 2 与 1）。"""
        # 1. 优先探测 OpenCode 2 的 /api/session
        try:
            req = self._make_request("/api/session", method="GET")
            with urllib.request.urlopen(req, timeout=0.8) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        except Exception as exc:
            logger.error("探测 OpenCode 2 API 遇到非网络异常: 服务地址=%s, 异常=%s, 处理=尝试回退探测 v1", self.base_url, exc)

        # 2. 回退探测 OpenCode 1 的 /session
        try:
            req = self._make_request("/session", method="GET")
            with urllib.request.urlopen(req, timeout=0.8) as resp:
                return resp.status == 200
        except (urllib.error.URLError, TimeoutError, OSError):
            return False
        except Exception as exc:
            logger.error("探测 OpenCode 1 API 遇到非网络异常: 服务地址=%s, 异常=%s, 处理=判定服务未就绪", self.base_url, exc)
            return False

    def is_alive(self) -> bool:
        """兼容别名：探测本地 opencode serve HTTP 服务是否存活。"""
        return self.is_server_running()

    def send_message(self, session_id: str, prompt: str, model_name: str = "") -> Optional[dict[str, Any]]:
        """发送消息（优先使用 OpenCode 2 /api/session/{id}/prompt，回退 OpenCode 1 /session/{id}/message）。"""
        # 优先尝试 OpenCode 2 API
        try:
            body_v2: dict[str, Any] = {"text": prompt}
            data_v2 = json.dumps(body_v2).encode("utf-8")
            req_v2 = self._make_request(f"/api/session/{session_id}/prompt", data=data_v2, method="POST")
            with urllib.request.urlopen(req_v2, timeout=30.0) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {"ok": True}
        except urllib.error.HTTPError as exc:
            if exc.code not in (404, 405):
                log_structured_error(
                    problem=f"调用 OpenCode 2 API 发送消息失败 ({session_id[:8]})",
                    cause=str(exc),
                    fix="已自动尝试回退模式或 CLI 执行。",
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

        # 回退 OpenCode 1 API
        body_v1: dict[str, Any] = {
            "parts": [{"type": "text", "text": prompt}],
        }
        if model_name and model_name != "default":
            body_v1["model"] = model_name
        data_v1 = json.dumps(body_v1).encode("utf-8")
        req_v1 = self._make_request(f"/session/{session_id}/message", data=data_v1, method="POST")
        try:
            with urllib.request.urlopen(req_v1, timeout=30.0) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {"ok": True}
        except Exception as exc:
            log_structured_error(
                problem=f"调用 opencode serve HTTP 接口发送消息失败 ({session_id[:8]})",
                cause=str(exc),
                fix="已自动回退至 opencode run CLI 子进程执行。",
                exc=exc,
            )
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
                problem=f"调用 OpenCode 2 API 发送消息非预期异常 ({session_id[:8]})",
                cause=str(exc),
                fix="系统已自动尝试回退模式或 CLI 执行。",
                exc=exc,
            )
        return {}


def ms_to_iso(ts_ms: Any) -> str:
    """将 OpenCode SQLite 中的毫秒级时间戳转换为 ISO 8601 字符串。"""
    try:
        val = float(ts_ms)
        if val > 1e11:
            val = val / 1000.0
        return datetime.fromtimestamp(val, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return utc_now_iso()


class OpenCodeAdapter(BaseAgentAdapter):
    """OpenCode 本地 SQLite 存储与服务适配器（全面支持 OpenCode 1.x 与 2.x）。"""

    agent_type = "opencode"
    agent_label = "OpenCode"

    @property
    def supported_modes(self) -> set[str]:
        """OpenCode 原生仅支持审批模式与 --auto 全自动模式，无只读规划与独立免审文件编辑模式。"""
        return {
            ExecutionMode.ASK.value,
            ExecutionMode.AUTO.value,
        }

    def __init__(
        self,
        data_dir: Optional[Path] = None,
        serve_url: Optional[str] = None,
        state_dir: Optional[Path] = None,
    ) -> None:
        super().__init__()
        default_data_dir = Path(os.path.expanduser("~/.local/share/opencode"))
        default_state_dir = Path(os.path.expanduser("~/.local/state/opencode"))
        self.data_dir = data_dir or default_data_dir
        if state_dir:
            self.state_dir = state_dir
        elif data_dir and data_dir != default_data_dir:
            # 隔离测试或自定义实例：绑定至独立状态目录，避免穿透读取宿主全局 service.json
            self.state_dir = data_dir / "state"
        else:
            self.state_dir = default_state_dir

        self.db_path = self.data_dir / "opencode.db"
        self.http_bridge = OpenCodeHttpBridge(base_url=serve_url, state_dir=self.state_dir)
        self.cli_bin = (
            shutil.which("opencode")
            or shutil.which("opencode2")
            or os.path.expanduser(r"~\AppData\Roaming\npm\opencode2.cmd")
            or os.path.expanduser(r"~\AppData\Roaming\npm\opencode2.ps1")
            or os.path.expanduser(r"~\AppData\Roaming\npm\opencode.cmd")
            or os.path.expanduser(r"~\AppData\Roaming\npm\opencode.ps1")
        )
        # limit -> (db_sig, list[UnifiedSession])
        self._list_cache: BoundedLRUCache = BoundedLRUCache(maxsize=20)
        # raw_session_id -> (db_sig, list[UnifiedMessageItem])
        self._messages_cache: BoundedLRUCache = BoundedLRUCache(maxsize=40)
        # cache_key -> (0, data)
        self._token_usage_cache: BoundedLRUCache = BoundedLRUCache(maxsize=100)
        self._has_session_v2: Optional[bool] = None

    def _get_db_sig(self) -> tuple[int, int, int, int]:
        """获取 opencode.db 及其 WAL 日志文件的修改时间与大小签名，用于 0ms 缓存校验。"""
        m1, s1, m2, s2 = 0, 0, 0, 0
        try:
            if self.db_path.exists():
                st1 = self.db_path.stat()
                m1, s1 = st1.st_mtime_ns, st1.st_size
            wal_path = Path(f"{self.db_path}-wal")
            if wal_path.exists():
                st2 = wal_path.stat()
                m2, s2 = st2.st_mtime_ns, st2.st_size
        except OSError:
            pass
        return (m1, s1, m2, s2)

    def _check_has_table(self, table_name: str) -> bool:
        """检查数据库中是否存在指定数据表（安全轻量探测，杜绝抛出 no such table 错误日志）。"""
        if not self.db_path.exists():
            return False
        try:
            rows = safe_query_external_sqlite(
                self.db_path,
                "SELECT count(*) as c FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            return bool(rows and int(rows[0].get("c") or 0) > 0)
        except (sqlite3.Error, OSError) as exc:
            log_structured_error(
                problem=f"检查 OpenCode 表结构失败: {table_name}",
                cause=str(exc),
                fix="请检查 opencode.db 数据库文件权限或完整性。",
                exc=exc,
            )
            return False

    def _check_has_session_v2(self) -> bool:
        """检查数据库是否存在 OpenCode 2.x 的 session_v2 表。"""
        return self._check_has_table("session_v2")

    def is_external_session_active(self, raw_session_id: str) -> bool:
        """轻量探测指定 OpenCode 会话是否处于外部运行/活跃态。"""
        if self.http_bridge.is_server_running():
            active_map = self.http_bridge.get_active_sessions()
            return raw_session_id in active_map
        return False

    def get_active_session_ids(self) -> list[str]:
        """返回当前处于外部原生活跃态的 OpenCode unified_id 列表。"""
        if self.http_bridge.is_server_running():
            active_map = self.http_bridge.get_active_sessions()
            return [f"{self.agent_type}:{sid}" for sid in active_map.keys()]
        return []

    def get_session_transcript_path(self, raw_session_id: str) -> Optional[Path]:
        """返回 OpenCode 会话对应的本地 SQLite 数据库文件路径。"""
        return self.db_path if self.db_path.exists() else None

    def is_available(self) -> tuple[bool, str]:
        if self.db_path.exists():
            cnt = 0
            try:
                has_v2 = self._check_has_session_v2()
                tbl = "session_v2" if has_v2 else "session"
                rows = safe_query_external_sqlite(self.db_path, f"SELECT count(*) as c FROM {tbl}")
                if rows and isinstance(rows, list):
                    cnt = int(rows[0].get("c") or 0)
            except (sqlite3.Error, OSError) as exc:
                log_structured_error(
                    problem="统计 OpenCode 会话数量失败",
                    cause=str(exc),
                    fix="请检查 opencode.db 数据库文件权限或 WAL 锁状态。",
                    exc=exc,
                )
                cnt = 0
            v_tag = "OpenCode 2" if self._check_has_session_v2() else "OpenCode"
            return True, f"{v_tag} 已就绪: 发现 {cnt} 个会话 (SQLite + opencode serve API)"
        if self.cli_bin and Path(str(self.cli_bin)).exists():
            return True, "OpenCode CLI 已安装（尚无本地会话记录）"
        return False, "未检测到 ~/.local/share/opencode/opencode.db"

    def get_watch_paths(self) -> list[Path]:
        paths: list[Path] = []
        if self.data_dir.exists():
            paths.append(self.data_dir)
        if self.state_dir.exists():
            paths.append(self.state_dir)
        return paths

    @classmethod
    def is_meaningful_storage_change(cls, path_str: str, filename: str) -> bool:
        """多态判定变动是否属于 OpenCode 核心存储。"""
        s = path_str.lower().replace("\\", "/")
        n = filename.lower()
        if "opencode" not in s:
            return False
        return n.endswith((".json", ".jsonl", ".sqlite", ".db"))

    def list_sessions(self, limit: int = 50) -> list[UnifiedSession]:
        """从 opencode.db 读取会话列表（自适应 session_v2 与旧版 session，实时感知活跃状态）。"""
        if not self.db_path.exists():
            return []

        active_map: dict[str, str] = {}
        if self.http_bridge.is_server_running():
            active_map = self.http_bridge.get_active_sessions()

        sig = self._get_db_sig()
        cached = self._list_cache.get(limit)
        if cached and cached[0] == sig:
            cached_sessions = [copy.copy(s) for s in cached[1]]
            for s in cached_sessions:
                if s.raw_session_id in active_map:
                    s.status = "running"
                elif s.status == "running" and s.raw_session_id not in active_map:
                    s.status = "idle"
            return cached_sessions

        has_v2 = self._check_has_session_v2()
        has_sm = self._check_has_table("session_message")
        if has_v2:
            count_sub = (
                "(SELECT COUNT(1) FROM session_message m WHERE m.session_id = s.id)"
                if has_sm
                else "0"
            )
            sql = f"""
                SELECT s.*,
                       {count_sub} AS msg_count
                FROM session_v2 s
                WHERE s.time_archived IS NULL
                ORDER BY s.time_updated DESC
                LIMIT ?
            """
        else:
            sql = """
                SELECT s.*,
                       (SELECT COUNT(1) FROM message m WHERE m.session_id = s.id) AS msg_count
                FROM session s
                WHERE s.time_archived IS NULL
                ORDER BY s.time_updated DESC
                LIMIT ?
            """

        rows = safe_query_external_sqlite(self.db_path, sql, (limit,))
        sessions: list[UnifiedSession] = []
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        for r in rows:
            sid = str(r.get("id") or "")
            if not sid:
                continue
            raw_title = re.sub(r"\s+", " ", str(r.get("title") or "").strip())
            # 过滤 OpenCode 内部派生的子代理会话 (@librarian / @oracle subagent)
            if "subagent)" in raw_title.lower():
                continue

            # 标题补全回退逻辑
            if not raw_title or raw_title.startswith("New session"):
                extracted = ""
                if has_v2 and has_sm:
                    first_user_msg = safe_query_external_sqlite(
                        self.db_path,
                        "SELECT data FROM session_message WHERE session_id = ? AND type = 'user' ORDER BY seq ASC LIMIT 1",
                        (sid,),
                    )
                    if first_user_msg:
                        try:
                            m_obj = json.loads(str(first_user_msg[0].get("data") or "{}"))
                            txt = re.sub(r"<system-reminder>.*?</system-reminder>", "", str(m_obj.get("text") or ""), flags=re.DOTALL)
                            txt = re.sub(r"\s+", " ", txt).strip()
                            if txt:
                                extracted = txt[:48]
                        except (json.JSONDecodeError, TypeError, KeyError):
                            pass
                elif not has_v2 and self._check_has_table("part"):
                    first_part = safe_query_external_sqlite(
                        self.db_path,
                        "SELECT data FROM part WHERE session_id = ? ORDER BY time_created ASC LIMIT 3",
                        (sid,),
                    )
                    for fp in first_part:
                        try:
                            p_obj = json.loads(str(fp.get("data") or "{}"))
                            if p_obj.get("type") == "text" and p_obj.get("text"):
                                txt = re.sub(r"<system-reminder>.*?</system-reminder>", "", str(p_obj["text"]), flags=re.DOTALL)
                                txt = re.sub(r"\s+", " ", txt).strip()
                                if txt:
                                    extracted = txt[:48]
                                    break
                        except (json.JSONDecodeError, TypeError, KeyError):
                            continue
                title = extracted or raw_title or f"OpenCode {sid[:8]}"
            else:
                title = raw_title

            directory = str(r.get("directory") or "")
            updated_at = ms_to_iso(r.get("time_updated"))
            msg_count = int(r.get("msg_count") or 0)

            # 活跃状态判定：优先 HTTP /api/session/active，其次 SQLite 兜底
            if sid in active_map:
                status = "running"
            elif has_v2:
                t_updated = int(r.get("time_updated") or 0)
                t_idle = int(r.get("time_idle") or 0) if r.get("time_idle") is not None else 0
                if (t_idle == 0 or t_updated > t_idle) and (now_ms - t_updated < 45000):
                    status = "running"
                else:
                    status = "idle"
            else:
                status = "idle"

            sessions.append(
                UnifiedSession(
                    unified_id=f"{self.agent_type}:{sid}",
                    agent_type=self.agent_type,
                    agent_label=self.agent_label,
                    raw_session_id=sid,
                    title=title,
                    preview=title,
                    project_path=directory,
                    status=status,
                    step_count=msg_count,
                    updated_at=updated_at,
                )
            )
        self._list_cache[limit] = (sig, sessions)
        return [copy.copy(s) for s in sessions]

    def _parse_session_message_rows(
        self,
        raw_session_id: str,
        sm_rows: list[dict[str, Any]],
        step_index_map: dict[str, int],
    ) -> list[UnifiedMessageItem]:
        """解析 OpenCode 2.x 的 session_message 单表行数据（包含 reasoning、tool 与 text）。"""
        items: list[UnifiedMessageItem] = []
        for r in sm_rows:
            mid = str(r.get("id") or "")
            idx = step_index_map.get(mid, 1)
            mtype = str(r.get("type") or "")
            created_at = ms_to_iso(r.get("time_created"))

            try:
                data = json.loads(str(r.get("data") or "{}"))
            except json.JSONDecodeError:
                data = {}

            if mtype == "user":
                raw_text = str(data.get("text") or "").strip()
                cleaned_user = re.sub(r"<system-reminder>.*?</system-reminder>", "", raw_text, flags=re.DOTALL).strip()
                items.append(
                    UnifiedMessageItem(
                        item_id=f"{raw_session_id}:msg:{mid}",
                        step_index=idx,
                        role="user",
                        created_at=created_at,
                        content=cleaned_user or raw_text,
                    )
                )
            elif mtype == "assistant":
                contents = data.get("content") or []
                text_chunks: list[str] = []
                thinking_chunks: list[str] = []
                tool_calls: list[UnifiedToolCall] = []

                for block in contents:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        t = str(block.get("text") or "").strip()
                        if t:
                            text_chunks.append(t)
                    elif btype in ("reasoning", "thinking"):
                        th = str(block.get("text") or block.get("reasoning") or "").strip()
                        if th:
                            thinking_chunks.append(th)
                    elif btype in ("tool", "tool_use"):
                        tname = str(block.get("name") or block.get("tool") or "tool")
                        tool_id = str(block.get("id") or f"oc_{mid}_{idx}")
                        state = block.get("state") if isinstance(block.get("state"), dict) else {}
                        tinput = state.get("input") or block.get("input") or {}
                        state_content = state.get("content") or []

                        out_pieces: list[str] = []
                        if isinstance(state_content, list):
                            for sc in state_content:
                                if isinstance(sc, dict) and sc.get("text"):
                                    out_pieces.append(str(sc["text"]))
                        elif isinstance(state_content, str):
                            out_pieces.append(state_content)
                        if state.get("error"):
                            out_pieces.append(str(state["error"]))

                        raw_output = "\n".join(out_pieces)
                        st_val = str(state.get("status") or "")
                        call_status = "DONE" if st_val == "completed" else ("FAILED" if st_val == "error" else "RUNNING")
                        tool_calls.append(
                            build_opencode_unified_tool_call(
                                tool_id=tool_id,
                                tool_name=tname,
                                tool_input=tinput,
                                raw_output=raw_output,
                                call_status=call_status,
                            )
                        )

                items.append(
                    UnifiedMessageItem(
                        item_id=f"{raw_session_id}:msg:{mid}",
                        step_index=idx,
                        role="assistant",
                        created_at=created_at,
                        content="\n\n".join(text_chunks).strip(),
                        thinking="\n\n".join(thinking_chunks).strip(),
                        tool_calls=tool_calls,
                    )
                )

        return items

    def _parse_messages_from_rows(
        self,
        raw_session_id: str,
        msg_rows: list[dict[str, Any]],
        step_index_map: dict[str, int],
    ) -> list[UnifiedMessageItem]:
        """解析 OpenCode 1.x 的 message + part 双表行数据。"""
        if not msg_rows:
            return []
        msg_ids = [str(r.get("id") or "") for r in msg_rows if r.get("id")]
        if not msg_ids:
            return []
        placeholders = ",".join(["?"] * len(msg_ids))
        part_rows = safe_query_external_sqlite(
            self.db_path,
            f"""
            SELECT id, message_id, time_created, data
            FROM part
            WHERE session_id = ? AND message_id IN ({placeholders})
            ORDER BY time_created ASC
            """,
            (raw_session_id, *msg_ids),
        )

        parts_by_msg: dict[str, list[dict[str, Any]]] = {}
        for pr in part_rows:
            mid = str(pr.get("message_id") or "")
            try:
                pdata = json.loads(str(pr.get("data") or "{}"))
            except json.JSONDecodeError:
                continue
            pdata["_part_id"] = pr.get("id")
            parts_by_msg.setdefault(mid, []).append(pdata)

        items: list[UnifiedMessageItem] = []
        for mr in msg_rows:
            mid = str(mr.get("id") or "")
            idx = step_index_map.get(mid, 1)
            try:
                mdata = json.loads(str(mr.get("data") or "{}"))
            except json.JSONDecodeError:
                mdata = {}

            role = str(mdata.get("role") or "assistant")
            created_at = ms_to_iso(mr.get("time_created"))
            msg_parts = parts_by_msg.get(mid, [])

            text_chunks: list[str] = []
            thinking_chunks: list[str] = []
            tool_calls: list[UnifiedToolCall] = []

            for p in msg_parts:
                ptype = str(p.get("type") or "")
                if ptype == "text":
                    t = str(p.get("text") or "").strip()
                    if t:
                        text_chunks.append(t)
                elif ptype in ("reasoning", "thinking"):
                    th = str(p.get("text") or p.get("reasoning") or "").strip()
                    if th:
                        thinking_chunks.append(th)
                elif ptype in ("tool", "tool-invocation", "tool_use"):
                    tname = str(p.get("tool") or p.get("name") or "tool")
                    state = p.get("state") if isinstance(p.get("state"), dict) else p
                    tinput = state.get("input") or p.get("input") or {}
                    toutput = state.get("output") or p.get("output") or ""
                    tool_calls.append(
                        build_opencode_unified_tool_call(
                            tool_id=str(p.get("callID") or p.get("_part_id") or f"oc_tc_{idx}"),
                            tool_name=tname,
                            tool_input=tinput,
                            raw_output=str(toutput),
                        )
                    )

            if text_chunks or thinking_chunks or tool_calls:
                joined_content = "\n\n".join(text_chunks).strip()
                actual_role = "user" if role == "user" else "assistant"

                if actual_role == "user" and "<system-reminder>" in joined_content:
                    cleaned_user = re.sub(
                        r"<system-reminder>.*?</system-reminder>", "", joined_content, flags=re.DOTALL
                    ).strip()
                    if not cleaned_user and joined_content.startswith("<system-reminder>"):
                        items.append(
                            UnifiedMessageItem(
                                item_id=f"{raw_session_id}:msg:{mid}",
                                step_index=idx,
                                role="assistant",
                                created_at=created_at,
                                content="",
                                tool_calls=[
                                    UnifiedToolCall(
                                        tool_id=f"oc_sys_{idx}",
                                        name="TaskNotification",
                                        args_summary="后台子任务已完成",
                                        output=truncate_large_output(joined_content)[0],
                                        status="DONE",
                                    )
                                ],
                            )
                        )
                        continue
                    joined_content = cleaned_user

                items.append(
                    UnifiedMessageItem(
                        item_id=f"{raw_session_id}:msg:{mid}",
                        step_index=idx,
                        role=actual_role,
                        created_at=created_at,
                        content=joined_content,
                        thinking="\n\n".join(thinking_chunks),
                        tool_calls=tool_calls,
                    )
                )

        return items

    def _query_all_messages_meta(self, raw_session_id: str) -> tuple[bool, list[dict[str, Any]]]:
        """优先拉取 session_message 表，不存在则回退至 message 表。返回 (is_v2, rows)。"""
        # 1. 尝试 session_message 表 (OpenCode 2)
        if self._check_has_table("session_message"):
            try:
                sm_rows = safe_query_external_sqlite(
                    self.db_path,
                    """
                    SELECT id, type, seq, time_created, time_updated, data
                    FROM session_message
                    WHERE session_id = ?
                    ORDER BY seq ASC
                    """,
                    (raw_session_id,),
                )
                if sm_rows:
                    return True, sm_rows
            except (sqlite3.Error, OSError) as exc:
                logger.debug(f"尝试读取 OpenCode 2 session_message 失败，准备回退: {exc}")

        # 2. 回退 message 表 (OpenCode 1)
        try:
            m_rows = safe_query_external_sqlite(
                self.db_path,
                "SELECT id, time_created, data FROM message WHERE session_id = ? ORDER BY time_created ASC",
                (raw_session_id,),
            )
            return False, m_rows
        except (sqlite3.Error, OSError) as exc:
            log_structured_error(
                problem=f"查询 OpenCode 消息失败: {raw_session_id[:8]}",
                cause=str(exc),
                fix="请检查 opencode.db 数据库文件权限或完整性。",
                exc=exc,
            )
            return False, []

    def get_session_messages_page(
        self,
        raw_session_id: str,
        limit: int = 30,
        before_step: Optional[int] = None,
        since_step: Optional[int] = None,
    ) -> tuple[list[UnifiedMessageItem], int, bool]:
        """分页获取会话消息（自适应 OpenCode 2 与 1）。"""
        if not self.db_path.exists():
            return [], 0, False

        is_v2, all_meta = self._query_all_messages_meta(raw_session_id)
        if not all_meta:
            return [], 0, False

        total_count = len(all_meta)
        step_index_map = {str(r.get("id")): idx for idx, r in enumerate(all_meta, start=1)}

        if since_step is not None:
            filtered = [r for r in all_meta if step_index_map.get(str(r.get("id")), 0) >= since_step]
            if is_v2:
                page_items = self._parse_session_message_rows(raw_session_id, filtered, step_index_map)
            else:
                page_items = self._parse_messages_from_rows(raw_session_id, filtered, step_index_map)
            return page_items, total_count, False

        if before_step is not None:
            filtered = [r for r in all_meta if step_index_map.get(str(r.get("id")), 0) < before_step]
        else:
            filtered = all_meta

        page_rows = filtered[-limit:] if limit > 0 else filtered
        has_more = len(filtered) > len(page_rows)

        if is_v2:
            page_items = self._parse_session_message_rows(raw_session_id, page_rows, step_index_map)
        else:
            page_items = self._parse_messages_from_rows(raw_session_id, page_rows, step_index_map)
        return page_items, total_count, has_more

    def get_session_messages(
        self, raw_session_id: str, limit: int = 120
    ) -> list[UnifiedMessageItem]:
        """关联查询会话消息（自适应 session_message 与 message+part，带 WAL 签名缓存）。"""
        if not self.db_path.exists():
            return []

        sig = self._get_db_sig()
        cached = self._messages_cache.get(raw_session_id)
        if cached and cached[0] == sig:
            all_cached = cached[1]
            return all_cached[-limit:] if limit > 0 else list(all_cached)

        is_v2, all_meta = self._query_all_messages_meta(raw_session_id)
        if not all_meta:
            self._messages_cache[raw_session_id] = (sig, [])
            return []

        step_index_map = {str(r.get("id")): idx for idx, r in enumerate(all_meta, start=1)}
        target_rows = all_meta[-limit:] if limit > 0 else all_meta

        if is_v2:
            items = self._parse_session_message_rows(raw_session_id, target_rows, step_index_map)
        else:
            items = self._parse_messages_from_rows(raw_session_id, target_rows, step_index_map)

        if limit <= 0 or limit >= len(all_meta):
            self._messages_cache[raw_session_id] = (sig, items)
        return items

    def get_full_step_output(self, raw_session_id: str, step_index: int) -> str:
        """按需读取 OpenCode 指定消息步骤的完整未截断工具输出。"""
        if not self.db_path.exists():
            return ""

        is_v2, all_meta = self._query_all_messages_meta(raw_session_id)
        if step_index < 1 or step_index > len(all_meta):
            return ""

        target_row = all_meta[step_index - 1]
        target_mid = str(target_row.get("id") or "")

        if is_v2:
            try:
                data = json.loads(str(target_row.get("data") or "{}"))
                contents = data.get("content") or []
                full_chunks: list[str] = []
                for block in contents:
                    if isinstance(block, dict) and block.get("type") in ("tool", "tool_use"):
                        state = block.get("state") or {}
                        state_content = state.get("content") or []
                        if isinstance(state_content, list):
                            for sc in state_content:
                                if isinstance(sc, dict) and sc.get("text"):
                                    full_chunks.append(str(sc["text"]))
                        elif isinstance(state_content, str):
                            full_chunks.append(state_content)
                        if state.get("error"):
                            full_chunks.append(str(state["error"]))
                return "\n\n".join(full_chunks)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as parse_err:
                logger.error("解析 OpenCode 2 消息工具内容失败: 消息ID=%s, 异常=%s, 处理=回退至 OpenCode 1 消息解析", target_mid, parse_err)
                return ""

        # OpenCode 1 回退
        part_rows = safe_query_external_sqlite(
            self.db_path,
            "SELECT data FROM part WHERE session_id = ? AND message_id = ? ORDER BY time_created ASC",
            (raw_session_id, target_mid),
        )
        full_chunks = []
        for pr in part_rows:
            raw_data = str(pr.get("data") or "")
            try:
                p = json.loads(raw_data)
                state = p.get("state") if isinstance(p.get("state"), dict) else p
                out = state.get("output") or p.get("output") or p.get("text") or ""
                if out:
                    full_chunks.append(str(out))
            except json.JSONDecodeError:
                full_chunks.append(raw_data)
        return "\n\n".join(full_chunks)

    def build_cli_command(
        self,
        raw_session_id: str,
        prompt: str,
        execution_mode: str,
        model_name: str = "",
        approved_tools: Optional[list[str]] = None,
    ) -> list[str]:
        """为 OpenCode 组装命令行（支持 run 与 --auto）。"""
        clean_model = (model_name or "").strip()
        if clean_model == "default":
            clean_model = ""
        bin_path = str(self.cli_bin or "opencode")
        if bin_path.endswith(".ps1"):
            cmd = ["pwsh", "-NoProfile", "-File", bin_path]
        else:
            cmd = [bin_path]
        cmd.extend(["run", "--session", raw_session_id, "--prompt", prompt])
        if clean_model:
            cmd.extend(["--model", clean_model])
        if execution_mode in ("accept_edits", "auto"):
            cmd.append("--auto")
        return cmd

    def respond_permission(
        self,
        raw_session_id: str,
        tool_call_id: str,
        decision: str,
    ) -> bool:
        """通过本地 HTTP Bridge 响应待审批工具调用的允许/拒绝。"""
        if self.http_bridge.is_server_running():
            return self.http_bridge.respond_permission(
                session_id=raw_session_id,
                permission_id=tool_call_id,
                allow=(decision == "approved"),
            )
        return False

    def extract_stream_tool_blocks(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """从 OpenCode 流式事件中提取工具调用块。"""
        tool_blocks: list[dict[str, Any]] = []
        ev_type = str(event.get("type") or "")
        if ev_type in ("tool_use", "permission_request", "tool_call"):
            tool_blocks.append(event)
        elif isinstance(event.get("part"), dict):
            part = event["part"]
            if part.get("type") == "tool":
                tool_blocks.append(
                    {
                        "id": str(part.get("id") or part.get("call_id") or ""),
                        "name": str(part.get("tool") or part.get("name") or ""),
                        "input": part.get("args") or part.get("input") or {},
                    }
                )
        return tool_blocks

    def get_session_token_usage(
        self, raw_session_id: str, model_name: str = ""
    ) -> dict[str, Any]:
        """提取当前会话 token 统计（优先读取 OpenCode 2.x session_v2 高精度字段，回退消息聚合）。"""
        limit = resolve_model_context_limit(model_name)
        if not self.db_path.exists():
            return SessionTokenUsage(context_limit=limit).to_dict()

        sig = self._get_db_sig()
        cache_key = f"{raw_session_id}:{sig}"
        cached = self._token_usage_cache.get(cache_key)
        if cached:
            res = dict(cached[1])
            res["context_limit"] = limit
            res["context_percent"] = (
                round((res["context_tokens"] / limit) * 100, 1) if limit > 0 else 0.0
            )
            return res

        # 1. 优先读取 OpenCode 2 的 session_v2 统计
        if self._check_has_session_v2():
            v2_rows = safe_query_external_sqlite(
                self.db_path,
                """
                SELECT tokens_input, tokens_output, tokens_reasoning, tokens_cache_read, cost
                FROM session_v2 WHERE id = ?
                """,
                (raw_session_id,),
            )
            if v2_rows:
                r = v2_rows[0]
                in_tok = int(r.get("tokens_input") or 0)
                out_tok = int(r.get("tokens_output") or 0)
                cr_tok = int(r.get("tokens_cache_read") or 0)
                if in_tok > 0 or out_tok > 0 or cr_tok > 0:
                    latest_context = in_tok + cr_tok
                    pct = round((latest_context / limit) * 100, 1) if limit > 0 else 0.0
                    data = {
                        "context_tokens": latest_context,
                        "context_limit": limit,
                        "context_percent": pct,
                        "total_tokens": in_tok + out_tok,
                        "last_output_tokens": out_tok,
                        "cache_read_tokens": cr_tok,
                    }
                    self._token_usage_cache[cache_key] = (0, data)
                    return data

        # 2. 回退：从 session_message 或 message 提取
        rows = safe_query_external_sqlite(
            self.db_path,
            "SELECT data FROM message WHERE session_id = ? ORDER BY time_created ASC",
            (raw_session_id,),
        )
        latest_context = 0
        latest_output = 0
        total_tokens = 0
        cache_read = 0

        for r in rows:
            raw_data = r.get("data")
            if not raw_data:
                continue
            try:
                d = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
                tok = d.get("tokens")
                if isinstance(tok, dict):
                    in_tok = tok.get("input") or 0
                    out_tok = tok.get("output") or 0
                    tot = tok.get("total") or (in_tok + out_tok)
                    cr = (tok.get("cache") or {}).get("read") or 0
                    if tot > 0 or in_tok > 0:
                        latest_context = in_tok + cr if (in_tok + cr) > 0 else tot
                        latest_output = out_tok
                        total_tokens += tot
                        cache_read += cr
            except (json.JSONDecodeError, TypeError, KeyError):
                continue

        pct = round((latest_context / limit) * 100, 1) if limit > 0 else 0.0
        data = {
            "context_tokens": latest_context,
            "context_limit": limit,
            "context_percent": pct,
            "total_tokens": total_tokens,
            "last_output_tokens": latest_output,
            "cache_read_tokens": cache_read,
        }
        self._token_usage_cache[cache_key] = (0, data)
        return data

