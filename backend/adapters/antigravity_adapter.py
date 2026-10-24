"""
Antigravity IDE 与 CLI 双引擎本地会话存储与通信协议适配器。

包含：
1. 监听并解析 `~/.gemini/antigravity` 与 `~/.gemini/antigravity-cli` 的会话数据。
2. Connect-RPC 直连协议交互与 Fork-to-CLI 安全克隆接力。
3. 提取结构化消息流、思考过程与单列 Unified Diff。
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.adapters.antigravity_ls_bridge import (
    AntigravityLanguageServerBridge,
    CSRF_TOKEN_RE,
    LS_MODEL_ID_MAP,
    LS_SERVICE_PREFIX,
    MODEL_ID_TO_LS_MAP,
    encode_connect_envelope,
)
from backend.adapters.antigravity_parser import (
    AGY_TOOL_OUTPUT_TYPES,
    EXTENDED_ACTIVE_WINDOW_SECONDS,
    RECENT_ACTIVE_WINDOW_SECONDS,
    SHORT_IDLE_GRACE_SECONDS,
    USER_REQUEST_RE,
    clean_agy_user_content,
    clean_denied_tool_instruction,
    compute_agy_token_usage,
    extract_agy_step_full_output,
    extract_protobuf_model_id,
    fork_ide_session_to_cli,
    inspect_transcript_tail_status,
    is_temporary_probe_session,
    parse_agy_step_dict,
    parse_agy_step_line,
    parse_state_selected_model,
)
from backend.adapters.antigravity_scanner import (
    query_antigravity_single_session,
    merge_agy_models_catalog,
    query_antigravity_sessions,
    query_session_model,
    read_antigravity_messages,
    resolve_agy_default_model,
)
from backend.adapters.base import (
    BaseAgentAdapter,
    BoundedLRUCache,
    SessionTokenUsage,
    UnifiedMessageItem,
    UnifiedSession,
    UnifiedToolCall,
    decode_workspace_uri,
    evaluate_project_dir,
    resolve_model_context_limit,
)
from backend.core.database import safe_query_external_sqlite, utc_now_iso
from backend.core.logger import log_structured_error, logger


class AntigravityAdapter(BaseAgentAdapter):
    """Antigravity IDE 与 CLI 存储与通信协议适配器。"""

    def __init__(
        self,
        agent_type: str = "agy_ide",
        agent_label: str = "Antigravity IDE",
        root_dir: Optional[Path] = None,
    ) -> None:
        super().__init__()
        self.agent_type = agent_type
        self.agent_label = agent_label
        default_dir = (
            Path(os.path.expanduser("~/.gemini/antigravity"))
            if agent_type == "agy_ide"
            else Path(os.path.expanduser("~/.gemini/antigravity-cli"))
        )
        self.root_dir = root_dir or default_dir
        self.conversations_dir = self.root_dir / "conversations"
        self.brain_dir = self.root_dir / "brain"
        self.summary_db_path = self.root_dir / "conversation_summaries.db"
        self.cli_bin = (
            shutil.which("agy")
            or os.path.expanduser(r"~\AppData\Roaming\npm\agy.cmd")
            or os.path.expanduser(r"~\.local\bin\agy.exe")
        )
        self._summary_db_cache: BoundedLRUCache = BoundedLRUCache(maxsize=10)
        self._brain_scan_cache: tuple[float, dict[str, tuple[float, int]]] = (0.0, {})
        self._messages_cache: BoundedLRUCache = BoundedLRUCache(maxsize=40)
        self._token_usage_cache: BoundedLRUCache = BoundedLRUCache(maxsize=100)
        self.ls_bridge = AntigravityLanguageServerBridge()

    _parse_agy_step_dict = staticmethod(parse_agy_step_dict)
    _parse_agy_step_line = staticmethod(parse_agy_step_line)

    def get_session_model(self, raw_session_id: str) -> Optional[str]:
        """优先从 conversation_summaries.db 提取会话模型。"""
        return query_session_model(
            self.summary_db_path, self.root_dir, raw_session_id, agent_type=self.agent_type
        )

    def invalidate_cache(self) -> None:
        """重置适配器内部短效缓存。"""
        self._summary_db_cache.clear()
        self._brain_scan_cache = (0.0, {})

    def is_external_session_active(self, raw_session_id: str) -> bool:
        """单行极速探测 Antigravity 会话是否处于外部运行/活跃态（优先以 transcript 权威日志为准，联动子会话）。"""
        tp = self.get_session_transcript_path(raw_session_id)
        if tp and tp.exists():
            try:
                is_active, _ = inspect_transcript_tail_status(tp)
                if is_active:
                    return True
            except Exception as exc:
                logger.debug(f"探测 transcript 状态异常 ({raw_session_id[:8]}): {exc}")

        # 若主会话自身 transcript 未显式活跃，进一步检查是否存在处于活跃态的子代理/子进程
        if self.summary_db_path.exists():
            sql_sub = """
                SELECT conversation_id, status, not_fully_idle
                FROM conversation_summaries
                WHERE parent_conversation_id = ? AND killed = 0
            """
            sub_rows = safe_query_external_sqlite(self.summary_db_path, sql_sub, (raw_session_id,))
            for sr in sub_rows:
                s_cid = str(sr.get("conversation_id") or "")
                s_not_idle = bool(sr.get("not_fully_idle"))
                s_raw = str(sr.get("status") or "")
                if s_not_idle or "RUNNING" in s_raw.upper():
                    s_tp = self.get_session_transcript_path(s_cid)
                    if s_tp and s_tp.is_file():
                        try:
                            s_act, _ = inspect_transcript_tail_status(s_tp)
                            if s_act:
                                return True
                        except Exception:
                            return True
                    else:
                        return True

            # 若主会话 transcript 尚不存在，回退至 SQLite 摘要表探测
            if not tp or not tp.exists():
                sql_self = "SELECT status, not_fully_idle FROM conversation_summaries WHERE conversation_id = ? AND killed = 0 LIMIT 1"
                rows = safe_query_external_sqlite(self.summary_db_path, sql_self, (raw_session_id,))
                if rows:
                    r = rows[0]
                    not_idle = bool(r.get("not_fully_idle"))
                    raw_status = str(r.get("status") or "")
                    if self.agent_type == "agy_ide" and not_idle:
                        return True
                    if not_idle or "RUNNING" in raw_status.upper():
                        return True
        return False

    def get_active_session_ids(self) -> list[str]:
        """返回 Antigravity 当前处于原生活跃态的 unified_id 列表（含主会话与子进程活跃的父会话）。"""
        active_ids: set[str] = set()
        if self.summary_db_path.exists():
            sql = """
                SELECT conversation_id, parent_conversation_id
                FROM conversation_summaries
                WHERE killed = 0 AND (not_fully_idle = 1 OR status LIKE '%RUNNING%')
                LIMIT 20
            """
            rows = safe_query_external_sqlite(self.summary_db_path, sql)
            for r in rows:
                cid = r.get("conversation_id")
                p_cid = r.get("parent_conversation_id")
                if cid:
                    active_ids.add(f"{self.agent_type}:{cid}")
                if p_cid:
                    active_ids.add(f"{self.agent_type}:{p_cid}")
        return list(active_ids)[:10]

    def get_session_transcript_path(self, raw_session_id: str) -> Optional[Path]:
        return self.brain_dir / raw_session_id / ".system_generated" / "logs" / "transcript.jsonl"

    def _get_summary_db_sig(self) -> tuple[int, int, int, int]:
        m1, s1, m2, s2 = 0, 0, 0, 0
        try:
            if self.summary_db_path.exists():
                st1 = self.summary_db_path.stat()
                m1, s1 = st1.st_mtime_ns, st1.st_size
            wal_p = Path(f"{self.summary_db_path}-wal")
            if wal_p.exists():
                st2 = wal_p.stat()
                m2, s2 = st2.st_mtime_ns, st2.st_size
        except OSError as exc:
            logger.debug(f"获取 summary db 签名异常: {exc}")
        return (m1, s1, m2, s2)

    def is_available(self) -> tuple[bool, str]:
        if self.summary_db_path.exists() or self.brain_dir.exists():
            cnt = 0
            if self.summary_db_path.exists():
                try:
                    sql = """
                        SELECT count(*) as c
                        FROM conversation_summaries
                        WHERE killed = 0
                          AND NOT (
                              (title IS NULL OR trim(title) = '')
                              AND (preview IS NULL OR trim(preview) = '' OR preview LIKE '[已拒绝工具%')
                          )
                    """
                    rows = safe_query_external_sqlite(self.summary_db_path, sql)
                    if rows and isinstance(rows, list):
                        cnt = int(rows[0].get("c") or 0)
                except Exception as exc:
                    logger.debug(f"统计 conversation_summaries 会话数异常: {exc}")
                    cnt = 0
            mode_note = " (只读镜像 + 安全克隆接力)" if self.agent_type == "agy_ide" else " (stream-json 双向管道)"
            return True, f"已挂载 {self.root_dir.name}: 发现 {cnt} 个会话{mode_note}"
        return False, f"未找到数据目录 {self.root_dir}"

    def get_watch_paths(self) -> list[Path]:
        paths: list[Path] = []
        if self.root_dir.exists():
            paths.append(self.root_dir)
        if self.brain_dir.exists():
            paths.append(self.brain_dir)
        return paths

    @classmethod
    def is_meaningful_storage_change(cls, path_str: str, filename: str) -> bool:
        """多态判定变动是否属于 Antigravity 核心会话存储。"""
        s = path_str.lower().replace("\\", "/")
        n = filename.lower()
        if "antigravity" not in s and ".gemini" not in s:
            return False
        return (
            n == "transcript.jsonl"
            or "conversation_summaries.db" in n
            or (n.endswith(".db") and "/conversations/" in s)
        )

    def list_sessions(self, limit: int = 50) -> list[UnifiedSession]:
        """合并 conversation_summaries.db 与 brain 目录日志，构建会话摘要。"""
        return query_antigravity_sessions(
            summary_db_path=self.summary_db_path,
            brain_dir=self.brain_dir,
            agent_type=self.agent_type,
            agent_label=self.agent_label,
            limit=limit,
        )

    def get_session(self, raw_session_id: str) -> Optional[UnifiedSession]:
        """按 ID 精确定位单个 Antigravity 会话摘要。"""
        return query_antigravity_single_session(
            summary_db_path=self.summary_db_path,
            brain_dir=self.brain_dir,
            agent_type=self.agent_type,
            agent_label=self.agent_label,
            raw_session_id=raw_session_id,
        )

    def get_session_messages(
        self, raw_session_id: str, limit: int = 120
    ) -> list[UnifiedMessageItem]:
        """增量/全量解析 transcript.jsonl 并映射追加结构化卡片流。"""
        transcript_path = self.get_session_transcript_path(raw_session_id)
        if not transcript_path or not transcript_path.exists():
            return []
        return read_antigravity_messages(
            transcript_path=transcript_path,
            raw_session_id=raw_session_id,
            agent_label=self.agent_label,
            messages_cache=self._messages_cache,
            limit=limit,
        )

    def send_prompt_via_ls(
        self, raw_session_id: str, prompt: str, model_id: str = ""
    ) -> bool:
        """通过桌面端 Language Server Connect-RPC 直连下发指令。"""
        return self.ls_bridge.send_cascade_message(raw_session_id, prompt, model_id)

    def cancel_via_ls(self, raw_session_id: str) -> bool:
        """通过桌面端 Language Server Connect-RPC 取消会话。"""
        return self.ls_bridge.cancel_cascade(raw_session_id)

    def get_full_step_output(self, raw_session_id: str, step_index: int) -> str:
        """按需从底层 transcript.jsonl 读取指定单步完整原始输出。"""
        return extract_agy_step_full_output(self.brain_dir, raw_session_id, step_index)

    def send_ide_message(
        self, raw_session_id: str, prompt: str, model_id: str = ""
    ) -> bool:
        return self.ls_bridge.send_cascade_message(raw_session_id, prompt, model_id)

    def cancel_ide_session(self, raw_session_id: str) -> bool:
        return self.ls_bridge.cancel_cascade(raw_session_id)

    def try_direct_dispatch(
        self,
        raw_session_id: str,
        prompt: str,
        model_name: str = "",
        workdir: str = "",
    ) -> Optional[dict[str, Any]]:
        if self.agent_type == "agy_ide":
            if self.ls_bridge.send_cascade_message(raw_session_id, prompt, model_name):
                return {"via_ls_rpc": True}
        return None

    def cancel_session(self, raw_session_id: str) -> bool:
        if self.agent_type == "agy_ide":
            try:
                return self.ls_bridge.cancel_cascade(raw_session_id)
            except Exception:
                return False
        return False

    def build_cli_command(
        self,
        raw_session_id: str,
        prompt: str,
        execution_mode: str,
        model_name: str = "",
        approved_tools: Optional[list[str]] = None,
    ) -> list[str]:
        """为 Antigravity CLI 组装流式命令行。"""
        clean_model = (model_name or "").strip()
        if clean_model == "default":
            clean_model = ""
        bin_path = str(self.cli_bin or "agy")
        if sys.platform == "win32" and bin_path.lower().endswith((".cmd", ".bat")):
            cmd = ["cmd.exe", "/c", bin_path]
        else:
            cmd = [bin_path]
        cmd.extend([
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--conversation",
            raw_session_id,
        ])
        if clean_model:
            cmd.extend(["--model", clean_model])
        if execution_mode == "plan":
            cmd.extend(["--mode", "plan"])
        elif execution_mode == "accept_edits":
            cmd.extend(["--mode", "accept-edits"])
        elif execution_mode == "auto":
            cmd.append("--dangerously-skip-permissions")
        else:
            cmd.extend(["--mode", "accept-edits"])
        return cmd

    def extract_stream_tool_blocks(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """从 Antigravity CLI stream-json 帧中提取待审批工具调用。"""
        tool_blocks: list[dict[str, Any]] = []
        raw_calls = event.get("tool_calls")
        if not raw_calls and isinstance(event.get("step"), dict):
            raw_calls = event["step"].get("tool_calls")
        if isinstance(raw_calls, list):
            for tc in raw_calls:
                if isinstance(tc, dict):
                    tool_blocks.append(
                        {
                            "id": str(tc.get("id") or tc.get("tool_id") or ""),
                            "name": str(tc.get("name") or tc.get("tool_name") or ""),
                            "args": tc.get("args") or tc.get("arguments") or {},
                            "input": tc.get("args") or tc.get("arguments") or tc.get("input") or {},
                        }
                    )
        return tool_blocks

    def get_session_token_usage(
        self, raw_session_id: str, model_name: str = ""
    ) -> dict[str, Any]:
        """从 transcript.jsonl 倒序扫描提取最新模型 Token 水位。"""
        limit = resolve_model_context_limit(model_name)
        transcript_path = self.get_session_transcript_path(raw_session_id)
        if not transcript_path or not transcript_path.exists():
            return SessionTokenUsage(context_limit=limit).to_dict()

        cache_key = str(transcript_path)
        try:
            mtime = transcript_path.stat().st_mtime_ns
        except OSError:
            mtime = 0

        cached = self._token_usage_cache.get(cache_key)
        if cached and cached[0] == mtime:
            res = dict(cached[1])
            res["context_limit"] = limit
            res["context_percent"] = (
                round((res["context_tokens"] / limit) * 100, 1) if limit > 0 else 0.0
            )
            return res

        data = compute_agy_token_usage(transcript_path, limit)
        self._token_usage_cache[cache_key] = (mtime, data)
        return data

    def get_default_model_id(self) -> str:
        """多层级自适应推断默认模型 ID。"""
        return resolve_agy_default_model(
            ls_bridge=self.ls_bridge,
            root_dir=self.root_dir,
            summary_db_path=self.summary_db_path,
            agent_type=self.agent_type,
        )

    def get_available_models_with_quota(self) -> list[dict[str, Any]]:
        """获取带实时配额的可用模型列表。"""
        return merge_agy_models_catalog(
            ls_bridge=self.ls_bridge,
            default_mid=self.get_default_model_id(),
            agent_type=self.agent_type,
        )
