# -*- coding: utf-8 -*-
"""
OpenCode (https://github.com/opencode-ai/opencode) 存储适配器。
负责解析 OpenCode 的本地 SQLite 存储、多会话发现以及与 `opencode serve` 的本地通信。
"""

from __future__ import annotations

import copy
import os
import shutil
import sqlite3
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
