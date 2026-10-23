"""
OpenCode (`~/.local/share/opencode/opencode.db` & `opencode serve`) 适配器。

核心能力：
1. 使用短连接安全读取 `opencode.db` 的 `session`、`message`、`part` 表，毫秒级聚合全部历史会话与结构化步骤。
2. 对接 `opencode serve` 的 HTTP/SSE 接口以支持实时消息流、发送消息与 `/permission` 逐条工具审批。
"""

from __future__ import annotations

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
    UnifiedMessageItem,
    UnifiedSession,
    UnifiedToolCall,
    classify_tool_risk,
    extract_tool_file_path,
    extract_tool_summary_and_diff,
    truncate_large_output,
)
from backend.core.database import safe_query_external_sqlite, utc_now_iso
from backend.core.logger import log_structured_error


class OpenCodeHttpBridge:
    """
    对接 `opencode serve` 的本地 HTTP/SSE 桥接客户端（遵循 Spec `agentdeck_design.md`）。
    支持：
    1. 探测本地 `opencode serve` 端点（默认 `http://127.0.0.1:4096`）。
    2. 调用 `POST /session/{id}/message` 发送指令。
    3. 调用 `POST /session/{id}/permissions/{perm_id}` 执行逐条工具权限审批。
    """

    def __init__(self, base_url: Optional[str] = None) -> None:
        self.base_url = (base_url or os.environ.get("OPENCODE_SERVE_URL") or "http://127.0.0.1:4096").rstrip("/")

    def is_server_running(self) -> bool:
        """探测本地 opencode serve HTTP 服务是否在线。"""
        try:
            req = urllib.request.Request(f"{self.base_url}/session", method="GET")
            with urllib.request.urlopen(req, timeout=0.6) as resp:
                return resp.status == 200
        except Exception:
            return False

    def is_alive(self) -> bool:
        """兼容别名：探测本地 opencode serve HTTP 服务是否存活。"""
        return self.is_server_running()


    def send_message(self, session_id: str, prompt: str, model_name: str = "") -> Optional[dict[str, Any]]:
        """通过 opencode serve 的 POST /session/{id}/message 接口发送消息。"""
        body: dict[str, Any] = {
            "parts": [{"type": "text", "text": prompt}],
        }
        if model_name and model_name != "default":
            body["model"] = model_name
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/session/{session_id}/message",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30.0) as resp:
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
        """通过 opencode serve 的 POST /session/{id}/permissions/{perm_id} 提交工具权限审批。"""
        payload = json.dumps({"response": "once" if allow else "reject"}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/session/{session_id}/permissions/{permission_id}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                return resp.status in (200, 204)
        except Exception as exc:
            log_structured_error(
                problem=f"提交 OpenCode HTTP 工具权限审批失败 ({permission_id})",
                cause=str(exc),
                fix="请确认 opencode serve 正在运行。",
                exc=exc,
            )
            return False


def ms_to_iso(ts_ms: Any) -> str:
    """将 OpenCode SQLite 中的毫秒级时间戳转换为 ISO 8601 字符串。"""
    try:
        val = float(ts_ms)
        if val > 1e11:
            val = val / 1000.0
        return datetime.fromtimestamp(val, tz=timezone.utc).isoformat()
    except Exception:
        return utc_now_iso()


class OpenCodeAdapter(BaseAgentAdapter):
    """OpenCode 本地 SQLite 存储与服务适配器。"""

    agent_type = "opencode"
    agent_label = "OpenCode"

    def __init__(self, data_dir: Optional[Path] = None, serve_url: Optional[str] = None) -> None:
        self.data_dir = data_dir or Path(os.path.expanduser("~/.local/share/opencode"))
        self.db_path = self.data_dir / "opencode.db"
        self.http_bridge = OpenCodeHttpBridge(base_url=serve_url)
        self.cli_bin = (
            shutil.which("opencode")
            or os.path.expanduser(r"~\AppData\Roaming\npm\opencode.cmd")
            or os.path.expanduser(r"~\AppData\Roaming\npm\opencode.ps1")
        )
        # limit -> (db_sig, list[UnifiedSession])
        self._list_cache: dict[int, tuple[tuple[int, int, int, int], list[UnifiedSession]]] = {}
        # raw_session_id -> (db_sig, list[UnifiedMessageItem])
        self._messages_cache: dict[str, tuple[tuple[int, int, int, int], list[UnifiedMessageItem]]] = {}

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

    def is_available(self) -> tuple[bool, str]:
        if self.db_path.exists():
            sessions = self.list_sessions(limit=200)
            return True, f"OpenCode 已就绪: 发现 {len(sessions)} 个会话 (SQLite + opencode serve API)"
        if self.cli_bin and Path(str(self.cli_bin)).exists():
            return True, "OpenCode CLI 已安装（尚无本地会话记录）"
        return False, "未检测到 ~/.local/share/opencode/opencode.db"

    def get_watch_paths(self) -> list[Path]:
        return [self.data_dir] if self.data_dir.exists() else []

    def list_sessions(self, limit: int = 50) -> list[UnifiedSession]:
        """从 opencode.db 的 session 表读取会话列表（带 WAL 签名内存缓存）。"""
        if not self.db_path.exists():
            return []

        sig = self._get_db_sig()
        cached = self._list_cache.get(limit)
        if cached and cached[0] == sig:
            return [copy.copy(s) for s in cached[1]]

        sql = """
            SELECT s.id, s.directory, s.title, s.time_updated,
                   (SELECT COUNT(1) FROM message m WHERE m.session_id = s.id) AS msg_count
            FROM session s
            WHERE s.time_archived IS NULL
            ORDER BY s.time_updated DESC
            LIMIT ?
        """
        rows = safe_query_external_sqlite(self.db_path, sql, (limit,))
        sessions: list[UnifiedSession] = []
        for r in rows:
            sid = str(r.get("id") or "")
            if not sid:
                continue
            raw_title = re.sub(r"\s+", " ", str(r.get("title") or "").strip())
            # 过滤 OpenCode 内部派生的子代理会话 (@librarian / @oracle subagent)
            if "subagent)" in raw_title.lower():
                continue

            if not raw_title or raw_title.startswith("New session -"):
                first_part = safe_query_external_sqlite(
                    self.db_path,
                    "SELECT data FROM part WHERE session_id = ? ORDER BY time_created ASC LIMIT 3",
                    (sid,),
                )
                extracted = ""
                for fp in first_part:
                    try:
                        p_obj = json.loads(str(fp.get("data") or "{}"))
                        if p_obj.get("type") == "text" and p_obj.get("text"):
                            txt = re.sub(r"<system-reminder>.*?</system-reminder>", "", str(p_obj["text"]), flags=re.DOTALL)
                            txt = re.sub(r"\s+", " ", txt).strip()
                            if txt:
                                extracted = txt[:48]
                                break
                    except Exception:
                        continue
                title = extracted or raw_title or f"OpenCode {sid[:8]}"
            else:
                title = raw_title

            directory = str(r.get("directory") or "")
            updated_at = ms_to_iso(r.get("time_updated"))
            msg_count = int(r.get("msg_count") or 0)

            sessions.append(
                UnifiedSession(
                    unified_id=f"{self.agent_type}:{sid}",
                    agent_type=self.agent_type,
                    agent_label=self.agent_label,
                    raw_session_id=sid,
                    title=title,
                    preview=title,
                    project_path=directory,
                    status="idle",
                    step_count=msg_count,
                    updated_at=updated_at,
                )
            )
        self._list_cache[limit] = (sig, sessions)
        return [copy.copy(s) for s in sessions]

    def _parse_messages_from_rows(
        self,
        raw_session_id: str,
        msg_rows: list[dict[str, Any]],
        step_index_map: dict[str, int],
    ) -> list[UnifiedMessageItem]:
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
                    summary, diff_txt = extract_tool_summary_and_diff(tname, tinput)
                    f_path = extract_tool_file_path(tinput)
                    trunc_out, is_trunc, full_len = truncate_large_output(str(toutput))
                    risk = classify_tool_risk(tname, summary)
                    tool_calls.append(
                        UnifiedToolCall(
                            tool_id=str(p.get("callID") or p.get("_part_id") or f"oc_tc_{idx}"),
                            name=tname,
                            args_summary=summary,
                            diff_text=diff_txt,
                            output=trunc_out,
                            is_truncated=is_trunc,
                            full_output_len=full_len,
                            risk_level=risk,
                            file_path=f_path,
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
                        # 纯后台任务完成通知，归入 Assistant 工具步骤，防止作为用户气泡劈断对话流
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

    def get_session_messages_page(
        self,
        raw_session_id: str,
        limit: int = 30,
        before_step: Optional[int] = None,
    ) -> tuple[list[UnifiedMessageItem], int, bool]:
        """
        深度下推至 SQL：先以极低开销查询全量 message 基础元信息，
        按 before_step 与 limit 切片后仅拉取目标消息关联的 part，杜绝全表扫描与缓存容量污染。
        """
        if not self.db_path.exists():
            return [], 0, False

        all_meta = safe_query_external_sqlite(
            self.db_path,
            "SELECT id, time_created, data FROM message WHERE session_id = ? ORDER BY time_created ASC",
            (raw_session_id,),
        )
        if not all_meta:
            return [], 0, False

        total_count = len(all_meta)
        step_index_map = {str(r.get("id")): idx for idx, r in enumerate(all_meta, start=1)}

        if before_step is not None:
            filtered = [r for r in all_meta if step_index_map.get(str(r.get("id")), 0) < before_step]
        else:
            filtered = all_meta

        page_rows = filtered[-limit:] if limit > 0 else filtered
        has_more = len(filtered) > len(page_rows)

        page_items = self._parse_messages_from_rows(raw_session_id, page_rows, step_index_map)
        return page_items, total_count, has_more

    def get_session_messages(
        self, raw_session_id: str, limit: int = 120
    ) -> list[UnifiedMessageItem]:
        """关联查询 opencode.db 的 message 与 part 表，组装为结构化对话卡片流（带 WAL 签名缓存与按需 part 过滤）。"""
        if not self.db_path.exists():
            return []

        sig = self._get_db_sig()
        cached = self._messages_cache.get(raw_session_id)
        if cached and cached[0] == sig:
            all_cached = cached[1]
            return all_cached[-limit:] if limit > 0 else list(all_cached)

        all_meta = safe_query_external_sqlite(
            self.db_path,
            "SELECT id, time_created, data FROM message WHERE session_id = ? ORDER BY time_created ASC",
            (raw_session_id,),
        )
        if not all_meta:
            self._messages_cache[raw_session_id] = (sig, [])
            return []

        step_index_map = {str(r.get("id")): idx for idx, r in enumerate(all_meta, start=1)}
        target_rows = all_meta[-limit:] if limit > 0 else all_meta
        items = self._parse_messages_from_rows(raw_session_id, target_rows, step_index_map)

        if limit <= 0 or limit >= len(all_meta):
            self._messages_cache[raw_session_id] = (sig, items)
        return items

    def get_full_step_output(self, raw_session_id: str, step_index: int) -> str:
        """按需读取 OpenCode 指定消息步骤的完整未截断工具输出。"""
        if not self.db_path.exists():
            return ""
        msg_rows = safe_query_external_sqlite(
            self.db_path,
            "SELECT id FROM message WHERE session_id = ? ORDER BY time_created ASC",
            (raw_session_id,),
        )
        if step_index < 1 or step_index > len(msg_rows):
            return ""
        target_mid = str(msg_rows[step_index - 1].get("id") or "")
        if not target_mid:
            return ""
        part_rows = safe_query_external_sqlite(
            self.db_path,
            "SELECT data FROM part WHERE session_id = ? AND message_id = ? ORDER BY time_created ASC",
            (raw_session_id, target_mid),
        )
        full_chunks: list[str] = []
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

