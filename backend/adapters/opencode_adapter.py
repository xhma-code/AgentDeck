# -*- coding: utf-8 -*-
"""
OpenCode (https://github.com/opencode-ai/opencode) 存储适配器。
负责解析 OpenCode 的本地 SQLite 存储、多会话发现以及与 `opencode serve` 的本地通信。
"""

from __future__ import annotations
from backend.adapters.tool_utils import extract_snippet

import copy
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional

from backend.adapters.base import (
    BaseAgentAdapter,
    BoundedLRUCache,
    ExecutionMode,
)
from backend.adapters.models import (
    UnifiedMessageItem,
    UnifiedSession,
    resolve_model_context_limit,
)
from backend.adapters.opencode_bridge import OpenCodeHttpBridge
from backend.adapters.opencode_parser import (
    build_opencode_unified_tool_call,
    extract_opencode_full_step_output,
    ms_to_iso,
    parse_messages_from_rows,
    parse_session_message_rows,
)
from backend.adapters.opencode_scanner import (
    extract_opencode_token_usage,
    fetch_opencode_messages_page,
    parse_opencode_model_field,
    query_all_messages_meta,
    query_opencode_sessions,
)
from backend.core.database import safe_query_external_sqlite
from backend.core.logger import log_structured_error

__all__ = [
    "OpenCodeHttpBridge",
    "OpenCodeAdapter",
    "ms_to_iso",
    "build_opencode_unified_tool_call",
]


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
        self._list_cache: BoundedLRUCache = BoundedLRUCache(maxsize=20)
        self._messages_cache: BoundedLRUCache = BoundedLRUCache(maxsize=40)
        self._token_usage_cache: BoundedLRUCache = BoundedLRUCache(maxsize=100)
        self._has_session_v2: Optional[bool] = None
        self._models_cache: Optional[list[dict[str, Any]]] = None
        self._models_cache_ts: float = 0.0

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
        """多态判定变动是否属于 OpenCode 核心存储（涵盖 SQLite 主库与 WAL 日志文件）。"""
        s = path_str.lower().replace("\\", "/")
        n = filename.lower()
        if "opencode" not in s:
            return False
        return n.startswith("opencode.db") or n.endswith(
            (".json", ".jsonl", ".sqlite", ".db", "-wal", "-shm")
        )

    def get_session(self, raw_session_id: str) -> Optional[UnifiedSession]:
        """按 ID 精确定位单个 OpenCode 会话摘要。"""
        if not self.db_path.exists() or not raw_session_id:
            return None
        active_map: dict[str, str] = {}
        if self.http_bridge.is_server_running():
            active_map = self.http_bridge.get_active_sessions()
        
        has_v2 = self._check_has_table("session_v2")
        table_name = "session_v2" if has_v2 else "session"
        count_sub = (
            "(SELECT COUNT(1) FROM session_message m WHERE m.session_id = s.id)"
            if (has_v2 and self._check_has_table("session_message"))
            else ("(SELECT COUNT(1) FROM message m WHERE m.session_id = s.id)" if not has_v2 else "0")
        )
        sql = f"""
            SELECT s.*, {count_sub} AS msg_count
            FROM {table_name} s
            WHERE s.id = ?
            LIMIT 1
        """
        rows = safe_query_external_sqlite(self.db_path, sql, (raw_session_id,))
        if not rows:
            return None
        r = rows[0]
        raw_title = re.sub(r"\s+", " ", str(r.get("title") or "").strip())
        parent_id = str(r.get("parent_id") or "").strip()
        is_sub = bool(parent_id) or ("subagent)" in raw_title.lower())
        parent_uid = f"{self.agent_type}:{parent_id}" if parent_id else None

        directory = str(r.get("directory") or "")
        updated_at = ms_to_iso(r.get("time_updated"))
        msg_count = int(r.get("msg_count") or 0)
        status = "running" if raw_session_id in active_map else "idle"

        return UnifiedSession(
            unified_id=f"{self.agent_type}:{raw_session_id}",
            agent_type=self.agent_type,
            agent_label=self.agent_label,
            raw_session_id=raw_session_id,
            title=raw_title or f"OpenCode {raw_session_id[:8]}",
            preview=raw_title or "暂无内容",
            project_path=directory,
            status=status,
            step_count=msg_count,
            updated_at=updated_at,
            parent_unified_id=parent_uid,
            is_subagent=is_sub,
        )

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
        sessions = query_opencode_sessions(
            db_path=self.db_path,
            limit=limit,
            active_map=active_map,
            agent_type=self.agent_type,
            agent_label=self.agent_label,
            has_v2=has_v2,
            has_sm=has_sm,
            check_has_table=self._check_has_table,
        )
        self._list_cache[limit] = (sig, sessions)
        return [copy.copy(s) for s in sessions]

    def _parse_session_message_rows(
        self,
        raw_session_id: str,
        sm_rows: list[dict[str, Any]],
        step_index_map: dict[str, int],
    ) -> list[UnifiedMessageItem]:
        return parse_session_message_rows(raw_session_id, sm_rows, step_index_map)

    def _parse_messages_from_rows(
        self,
        raw_session_id: str,
        msg_rows: list[dict[str, Any]],
        step_index_map: dict[str, int],
    ) -> list[UnifiedMessageItem]:
        return parse_messages_from_rows(self.db_path, raw_session_id, msg_rows, step_index_map)

    def _query_all_messages_meta(self, raw_session_id: str) -> tuple[bool, list[dict[str, Any]]]:
        return query_all_messages_meta(self.db_path, raw_session_id, self._check_has_table)

    def get_session_messages_page(
        self,
        raw_session_id: str,
        limit: int = 30,
        before_step: Optional[int] = None,
        since_step: Optional[int] = None,
    ) -> tuple[list[UnifiedMessageItem], int, bool]:
        """分页获取会话消息（自适应 OpenCode 2 与 1）。"""
        return fetch_opencode_messages_page(
            db_path=self.db_path,
            raw_session_id=raw_session_id,
            limit=limit,
            before_step=before_step,
            since_step=since_step,
            check_has_table=self._check_has_table,
        )

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
        return extract_opencode_full_step_output(
            db_path=self.db_path,
            raw_session_id=raw_session_id,
            step_index=step_index,
            all_meta=all_meta,
            is_v2=is_v2,
        )

    def create_session(self, title: str = "", project_path: str = "") -> Optional[str]:
        """创建 OpenCode 会话（优先通过官方 HTTP Bridge 创建并获取 ses_ 前缀 ID）。

        E1：HTTP 创建失败时不再自行伪造回调假 ID —— 返回 None 并记录
        last_create_via_http=False，由 orchestrator 生成兜底 ID 并标记 degraded。
        """
        self.last_create_via_http = False
        if self.http_bridge.is_server_running():
            sid, via_http = self.http_bridge.create_session(title=title, directory=project_path)
            if sid and via_http:
                self.last_create_via_http = True
                return sid
        return None

    def build_cli_command(
        self,
        raw_session_id: str,
        prompt: str,
        execution_mode: str,
        model_name: str = "",
        approved_tools: Optional[list[str]] = None,
    ) -> list[str]:
        """为 OpenCode 组装命令行（遵循 opencode run [flags] [<message...>] 规范）。"""
        clean_model = (model_name or "").strip()
        if clean_model == "default":
            clean_model = ""
        bin_path = str(self.cli_bin or "opencode")
        if sys.platform == "win32" and bin_path.lower().endswith((".cmd", ".bat")):
            cmd = ["cmd.exe", "/c", bin_path]
        elif bin_path.endswith(".ps1"):
            cmd = ["pwsh", "-NoProfile", "-File", bin_path]
        else:
            cmd = [bin_path]
        cmd.extend(["run", "--session", raw_session_id, "--format", "json"])
        if clean_model:
            cmd.extend(["--model", clean_model])
        if execution_mode in ("accept_edits", "auto"):
            cmd.append("--auto")
        cmd.append(prompt)
        return cmd

    def fetch_pending_forms(self, raw_session_id: str) -> list[dict[str, Any]]:
        """通过本地 HTTP Bridge 获取当前会话中等待决策的表单。"""
        if self.http_bridge.is_server_running():
            return self.http_bridge.fetch_pending_forms(raw_session_id)
        return []

    def reply_form(self, raw_session_id: str, form_id: str, answer_dict: dict[str, Any]) -> bool:
        """通过本地 HTTP Bridge 交付交互式决策表单答复。"""
        if self.http_bridge.is_server_running():
            return self.http_bridge.reply_form(raw_session_id, form_id, answer_dict)
        return False

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
            return extract_opencode_token_usage(self.db_path, raw_session_id, model_name, False)

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

        data = extract_opencode_token_usage(
            db_path=self.db_path,
            raw_session_id=raw_session_id,
            model_name=model_name,
            has_session_v2=self._check_has_session_v2(),
        )
        self._token_usage_cache[cache_key] = (0, data)
        return data

    def delete_session(self, raw_session_id: str) -> bool:
        """从 opencode.db 中物理删除指定会话及其关联消息。"""
        if not self.db_path or not self.db_path.exists():
            return False
        import sqlite3
        try:
            with sqlite3.connect(str(self.db_path), timeout=3.0) as conn:
                if self._check_has_session_v2():
                    conn.execute("DELETE FROM session_v2 WHERE id = ?", (raw_session_id,))
                    if self._check_has_table("session_message"):
                        conn.execute("DELETE FROM session_message WHERE session_id = ?", (raw_session_id,))
                else:
                    conn.execute("DELETE FROM session WHERE id = ?", (raw_session_id,))
                if self._check_has_table("message"):
                    conn.execute("DELETE FROM message WHERE session_id = ?", (raw_session_id,))
                conn.commit()
            return True
        except Exception as exc:
            logger.warning(f"删除 OpenCode 会话 {raw_session_id} 失败: {exc}")
            return False

    def cancel_session(self, raw_session_id: str) -> bool:
        """通过官方 HTTP Bridge 中断正在运行的 OpenCode 会话任务。"""
        if self.http_bridge.is_server_running():
            return self.http_bridge.abort_session(raw_session_id)
        return False

    def try_direct_dispatch(
        self,
        raw_session_id: str,
        prompt: str,
        model_name: str = "",
        workdir: str = "",
    ) -> Optional[dict[str, Any]]:
        """若 OpenCode 本地服务正在运行，优先通过官方 HTTP 桥接直连下发指令。"""
        if self.http_bridge.is_server_running():
            res = self.http_bridge.send_message(
                session_id=raw_session_id,
                prompt=prompt,
                model_name=model_name,
            )
            if res:
                return {"via_opencode_http": True, "result": res}
        return None

    def rename_session(self, raw_session_id: str, new_title: str) -> bool:
        """将新标题双向同步回写至 OpenCode（优先官方 HTTP Bridge 实时广播，并同步更新 SQLite 双表）。"""
        # 1. 优先尝试调用官方 HTTP Bridge 广播重命名
        bridge_renamed = False
        try:
            if self.http_bridge.is_server_running():
                bridge_renamed = self.http_bridge.rename_session(raw_session_id, new_title)
        except Exception as bridge_exc:
            logger.debug(f"通过 OpenCode HTTP 桥接重命名跳过: {bridge_exc}")

        # 2. 同步持久化写入 SQLite 数据库中的 session 与 session_v2 表
        if not self.db_path.exists():
            return bridge_renamed
        import sqlite3
        from backend.core.logger import logger
        updated = False
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=4.0)
            try:
                conn.execute("PRAGMA busy_timeout = 4000;")
                cur1 = conn.execute(
                    "UPDATE session SET title = ? WHERE id = ?",
                    (new_title, raw_session_id),
                )
                if cur1.rowcount > 0:
                    updated = True
                if self._check_has_session_v2():
                    cur2 = conn.execute(
                        "UPDATE session_v2 SET title = ? WHERE id = ?",
                        (new_title, raw_session_id),
                    )
                    if cur2.rowcount > 0:
                        updated = True
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.warning(f"同步更新 OpenCode 原生会话名称失败: {exc}")
            return False

        if updated:
            self.invalidate_cache()
            logger.info(f"已同步回写 OpenCode 原生会话名称: {raw_session_id[:8]} -> {new_title}")
            return True
        return False

    def search_sessions_content(
        self, query: str, limit: int = 30
    ) -> list[dict[str, Any]]:
        """在 OpenCode SQLite 数据库中检索消息内容。"""
        results: list[dict[str, Any]] = []
        q = query.strip()
        if not q or not self.db_path.exists():
            return results

        like_term = f"%{q}%"
        seen_sids: set[str] = set()

        try:
            sql_part = """
                SELECT session_id, data FROM part
                WHERE data LIKE ?
                ORDER BY time_created DESC
                LIMIT ?
            """
            rows = safe_query_external_sqlite(self.db_path, sql_part, (like_term, limit * 2))
            for r in rows:
                sid = str(r.get("session_id") or "")
                if not sid or sid in seen_sids:
                    continue
                seen_sids.add(sid)
                raw_data = str(r.get("data") or "")
                snippet = extract_snippet(raw_data, q)
                try:
                    parsed = json.loads(raw_data)
                    if isinstance(parsed, dict) and parsed.get("text"):
                        snippet = extract_snippet(parsed["text"], q)
                except Exception:
                    pass
                results.append({
                    "raw_session_id": sid,
                    "type": "content",
                    "snippet": snippet,
                })
                if len(results) >= limit:
                    break
        except Exception as exc:
            logger.debug(f"OpenCode part 表检索异常: {exc}")

        # part 表已覆盖所有会话的所有文本块，无需回退非标准旧表

        return results


    def get_default_model_id(self) -> str:
        """获取 OpenCode 当前生效的默认推荐模型 ID。"""
        # 1. 尝试从在线 HTTP Bridge 获取
        if self.http_bridge.is_server_running():
            mid = self.http_bridge.get_default_model_id()
            if mid:
                return mid

        # 2. 从可用模型中探测首选推荐
        models = self.get_available_models()
        for m in models:
            if m.get("is_recommended"):
                return str(m.get("id"))
        return models[0]["id"] if models else "opencode/big-pickle"

    def get_session_model(self, raw_session_id: str) -> Optional[str]:
        """从 SQLite 存储中读取特定会话当前绑定的真实模型。"""
        if not self.db_path.exists():
            return None
        table = "session_v2" if self._check_has_session_v2() else "session"
        try:
            rows = safe_query_external_sqlite(
                self.db_path,
                f"SELECT model FROM {table} WHERE id = ? LIMIT 1",
                (raw_session_id,),
            )
            if rows and rows[0].get("model") is not None:
                parsed = parse_opencode_model_field(rows[0]["model"])
                if parsed:
                    return parsed
        except Exception as exc:
            logger.debug(f"读取 OpenCode 会话模型失败 ({raw_session_id[:8]}): {exc}")
        return self.get_default_model_id()

    def get_available_models(self) -> list[dict[str, Any]]:
        """获取 OpenCode 可用模型清单（优先 HTTP 桥接实时探测，回退 CLI 探测，最终静态兜底）。"""
        import subprocess
        import time

        now = time.monotonic()
        if self._models_cache is not None and (now - self._models_cache_ts < 15.0):
            return [dict(m) for m in self._models_cache]

        # 1. 优先调用 HTTP Bridge 动态获取（支持最全的元数据、配额与上下文上限）
        if self.http_bridge.is_server_running():
            bridge_models = self.http_bridge.get_available_models()
            if bridge_models:
                self._models_cache = bridge_models
                self._models_cache_ts = now
                return [dict(m) for m in bridge_models]

        # 2. 若 HTTP 服务离线，尝试使用本地 CLI `opencode models` 探测
        if self.cli_bin:
            try:
                proc = subprocess.run(
                    [self.cli_bin, "models"],
                    capture_output=True,
                    text=True,
                    timeout=4.0,
                    encoding="utf-8",
                    errors="replace",
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    cli_models: list[dict[str, Any]] = []
                    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
                    for ln in lines:
                        # 格式通常为 opencode/deepseek-v4.1-flash-free 或 opencode/big-pickle
                        mid = ln
                        is_free = "-free" in mid.lower() or mid.startswith("opencode/")
                        is_def = "big-pickle" in mid.lower()
                        # 生成可读的 label
                        clean_name = mid.split("/", 1)[-1] if "/" in mid else mid
                        label_parts = [p.capitalize() for p in clean_name.replace("-", " ").replace(".", ".").split(" ")]
                        label = " ".join(label_parts)
                        tag = "默认" if is_def else ("免费" if is_free else "")
                        ctx_limit = resolve_model_context_limit(mid)
                        cli_models.append({
                            "id": mid,
                            "label": label,
                            "tag": tag,
                            "is_recommended": is_def,
                            "context_limit": ctx_limit,
                        })
                    if cli_models:
                        cli_models.sort(key=lambda item: 0 if item.get("is_recommended") else 1)
                        self._models_cache = cli_models
                        self._models_cache_ts = now
                        return [dict(m) for m in cli_models]
            except Exception as exc:
                logger.debug(f"CLI 探测 opencode models 跳过/失败: {exc}")

        # 3. 静态目录配置兜底
        from backend.engines.model_catalog import AGENT_MODELS
        fallback = [dict(m) for m in AGENT_MODELS.get("opencode", [])]
        self._models_cache = fallback
        self._models_cache_ts = now
        return fallback

    def get_available_models_with_quota(self) -> list[dict[str, Any]]:
        """获取可用模型列表（OpenCode 模型无独立配额数据，直接返回可用清单）。"""
        return self.get_available_models()
