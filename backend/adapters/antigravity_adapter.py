"""
Antigravity IDE (`~/.gemini/antigravity`) 与 Antigravity CLI (`~/.gemini/antigravity-cli`) 同构适配器。

核心能力：
1. 复用同一套 `AntigravityStorageAdapter` 同时读取桌面版 IDE 与 CLI 的 `conversation_summaries.db` 及 `transcript.jsonl`。
2. 遵循 `/autoplan` 防双写脑裂规范：当 `agy_ide` 会话在桌面端活跃时标记 `desktop_locked=True`；
   支持在手机端发送消息时将 IDE 会话安全克隆至 `agy_cli` (`fork_ide_session_to_cli`) 继续执行。
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sqlite3
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

from backend.adapters.base import (
    BaseAgentAdapter,
    BoundedLRUCache,
    SessionTokenUsage,
    UnifiedMessageItem,
    UnifiedSession,
    UnifiedToolCall,
    classify_tool_risk,
    count_diff_lines,
    extract_interactive_questions,
    extract_tool_file_path,
    extract_tool_summary_and_diff,
    is_read_only_tool,
    resolve_model_context_limit,
    truncate_large_output,
    truncate_tool_output,
)
from backend.core.database import safe_query_external_sqlite, utc_now_iso
from backend.core.logger import log_structured_error, logger
from datetime import datetime, timezone
from backend.core.workspace import decode_workspace_uri

# 会话被认定为持续活跃的最大文件写入时间窗口（秒）
RECENT_ACTIVE_WINDOW_SECONDS: float = 60.0
# 处于思考中 (CoT) 或末尾为 RUNNING/USER_INPUT 态的会话最大容忍活跃窗口（秒，平滑消解深度思考状态抖动）
EXTENDED_ACTIVE_WINDOW_SECONDS: float = 300.0
# 明确 DONE 且工具全部完成后的闲置判定平滑缓冲时间（秒，避免假活感知迟钝）
SHORT_IDLE_GRACE_SECONDS: float = 15.0


def inspect_transcript_tail_status(tp: Path) -> tuple[bool, str]:
    """
    极速从 transcript.jsonl 末尾探测会话真实活跃态与状态机（耗时 < 1ms）：
    1. 从文件末尾倒读最多 8KB 快速定位末尾非空记录行，避免全量读大文件。
    2. 若末尾 step 正在运行 (status in ('RUNNING', 'PENDING') 或含有未完成工具调用)，或处于用户提问等待响应态 (USER_INPUT)，
       在 EXTENDED_ACTIVE_WINDOW_SECONDS (300s) 内均判定为活跃，平滑消解深度思考 (CoT) 超过 60s 时的状态抖动。
    3. 若末尾 step 明确为 DONE 且工具全部完成，在最后写入超过 SHORT_IDLE_GRACE_SECONDS (15s) 后立即转入闲置，避免假活迟钝。
    """
    if not tp.is_file():
        return False, "idle"
    try:
        st = tp.stat()
        file_mtime = st.st_mtime
        age = time.time() - file_mtime
    except OSError:
        return False, "idle"

    # 超出最大容忍窗口（5 分钟无任何改动），必定为闲置
    if age > EXTENDED_ACTIVE_WINDOW_SECONDS:
        return False, "idle"

    # 读取尾部内容以快速解析末行状态
    tail_bytes = b""
    try:
        with open(tp, "rb") as f:
            if st.st_size > 8192:
                f.seek(-8192, os.SEEK_END)
            tail_bytes = f.read()
    except OSError:
        # 文件若暂时被独占，以 45 秒 mtime 为保底
        return age < 45.0, "running" if age < 45.0 else "idle"

    lines = [ln.strip() for ln in tail_bytes.split(b"\n") if ln.strip()]
    if not lines:
        return age < RECENT_ACTIVE_WINDOW_SECONDS, "running" if age < RECENT_ACTIVE_WINDOW_SECONDS else "idle"

    # 倒序寻找最后一个可合法解析的 JSON step 对象
    last_step: Optional[dict[str, Any]] = None
    has_incomplete_tail = False
    for raw_ln in reversed(lines):
        try:
            obj = json.loads(raw_ln.decode("utf-8", errors="replace"))
            if isinstance(obj, dict):
                last_step = obj
                break
        except Exception:
            has_incomplete_tail = True

    # 若最末尾行解析失败且在 60 秒内有文件写入，说明当前外部进程正在流式写盘
    if has_incomplete_tail and age < RECENT_ACTIVE_WINDOW_SECONDS:
        return True, "running"

    if not last_step:
        return age < RECENT_ACTIVE_WINDOW_SECONDS, "running" if age < RECENT_ACTIVE_WINDOW_SECONDS else "idle"

    step_type = str(last_step.get("type") or "")
    source = str(last_step.get("source") or "")
    status = str(last_step.get("status") or "").upper()
    tool_calls = last_step.get("tool_calls") or []

    # 1. 用户提问后等待模型规划，或者模型/工具处于明确运行中态
    is_user_waiting = step_type == "USER_INPUT" or source == "USER_EXPLICIT"
    is_running_step = status in ("RUNNING", "PENDING") or any(
        isinstance(tc, dict) and str(tc.get("status") or "").upper() in ("RUNNING", "PENDING")
        for tc in tool_calls
    )

    if (is_user_waiting or is_running_step) and age < EXTENDED_ACTIVE_WINDOW_SECONDS:
        return True, "running"

    # 2. 明确 DONE 且所有工具完成
    if status == "DONE" and not is_running_step:
        if age > SHORT_IDLE_GRACE_SECONDS:
            return False, "idle"
        return True, "running"

    return age < RECENT_ACTIVE_WINDOW_SECONDS, "running" if age < RECENT_ACTIVE_WINDOW_SECONDS else "idle"


def is_temporary_probe_session(title: str, preview: str, project_path: str = "") -> bool:
    """判定是否为外部自动化测试（如 cmem 等）在临时目录留下的探针残留会话，结合路径避免误伤真实业务项目。"""
    proj_lower = (project_path or "").lower()
    is_temp_dir = any(t in proj_lower for t in ("temp", "tmp", "cmem-")) or not proj_lower
    if not is_temp_dir:
        return False
    check_text = f"{title} {preview}".lower()
    probe_keywords = ("flag.txt", "marker.txt", "reading flag file", "read flag file")
    return any(p in check_text for p in probe_keywords)

USER_REQUEST_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)

# 已知的工具执行结果 step_type 集合（在 transcript.jsonl 中它们的 source 也是 MODEL，必须挂载到工具卡片而非渲染成聊天气泡）
AGY_TOOL_OUTPUT_TYPES = {
    "GENERIC",
    "RUN_COMMAND",
    "VIEW_FILE",
    "CODE_ACTION",
    "LIST_DIRECTORY",
    "SEARCH_WEB",
    "GREP_SEARCH",
    "MCP_TOOL",
    "BROWSER_ACTION",
    "READ_FILE",
    "EDIT_FILE",
    "WRITE_FILE",
}




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


def extract_protobuf_model_id(data: bytes) -> Optional[str]:
    """
    从 Antigravity 会话 steps / gen_metadata 二进制 payload 中稳健提取模型标识符。
    设计特性：
    1. 兼容单字节与多字节 varint 长度标记，消除固定单字节匹配隐患。
    2. 优先通过 model_enum 提取并映射至标准模型 ID。
    3. 支持嵌入的标准模型名称模式兜底（gemini-*, claude-*, gpt-*）。
    """
    if not data or not isinstance(data, bytes):
        return None

    # 1. 优先定位 model_enum 键值（放宽长度与前缀字节匹配，避免单字节假设）
    pattern = rb"model_enum[\x00-\x20\x80-\xff]+([A-Za-z0-9_]{4,40})"
    for m in re.finditer(pattern, data):
        raw_token = m.group(1).decode("ascii", errors="ignore").lower()
        if raw_token in LS_MODEL_ID_MAP:
            return LS_MODEL_ID_MAP[raw_token]
        if raw_token.startswith("model_placeholder_"):
            return LS_MODEL_ID_MAP.get(raw_token, raw_token)

    # 2. 优先精确匹配已知权威模型 ID 字节序列（按长度倒序，防止前缀碰撞）
    lower_data = data.lower()
    for mid in sorted(LS_MODEL_ID_MAP.values(), key=len, reverse=True):
        if mid.encode("ascii").lower() in lower_data:
            return mid

    # 3. 匹配可能未在静态表中的新嵌入标准模型名称字符串 (gemini-*, claude-*, gpt-*)
    fallback_pattern = rb"(?:^|[^a-zA-Z0-9.-])(gemini-[a-zA-Z0-9.-]+|claude-[a-zA-Z0-9.-]+|gpt-[a-zA-Z0-9.-]+)(?:[^a-zA-Z0-9.-]|$)"
    m_fb = re.search(fallback_pattern, data)
    if m_fb:
        return m_fb.group(1).decode("ascii", errors="ignore").lower()

    return None


def parse_state_selected_model(state_file: Path) -> Optional[str]:
    """从电脑端 antigravity_state.pbtxt 中提取用户最后选定的真实模型 ID。"""
    if not state_file.exists():
        return None
    try:
        content = state_file.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"last_selected_agent_model:\s*([A-Za-z0-9_]+)", content)
        if m:
            raw_id = m.group(1).lower()
            return LS_MODEL_ID_MAP.get(raw_id, raw_id)
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug(f"读取电脑端配置 {state_file} 遇到文件或编码异常: {exc}")
    return None


def clean_agy_user_content(raw_text: str) -> str:
    """剥离 Antigravity transcript.jsonl 中包裹用户输入的外层 <USER_REQUEST> 与系统元数据标签，并清理 TUI 框线冗余空格。"""
    if not raw_text:
        return ""
    match = USER_REQUEST_RE.search(raw_text)
    if match:
        cleaned = match.group(1).strip()
        if cleaned:
            cleaned = re.sub(r"[ \t]{6,}", "  ", cleaned)
            return cleaned
    # 若无标签或标签内为空（如点击了 Proceed 审批），提取可读部分
    stripped = re.sub(r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", "", raw_text, flags=re.DOTALL)
    stripped = re.sub(r"<USER_SETTINGS_CHANGE>.*?</USER_SETTINGS_CHANGE>", "", stripped, flags=re.DOTALL)
    stripped = re.sub(r"<EPHEMERAL_MESSAGE>.*?</EPHEMERAL_MESSAGE>", "", stripped, flags=re.DOTALL)
    stripped = re.sub(r"<system-reminder>.*?</system-reminder>", "", stripped, flags=re.DOTALL)
    stripped = stripped.replace("<USER_REQUEST>", "").replace("</USER_REQUEST>", "").strip()
    stripped = re.sub(r"[ \t]{6,}", "  ", stripped)
    cleaned_prompt = clean_denied_tool_instruction(stripped)
    return cleaned_prompt or "（已确认执行 / Proceed）"


def clean_denied_tool_instruction(text: str) -> str:
    """清洗带有内部控制标记的工具拒绝跟进文本，呈现更人性化的指导意见。"""
    if not text:
        return ""
    m = re.match(
        r"^\[已拒绝工具\s+[^\]]+\]\s*(请勿执行刚才的操作，按以下要求调整方案：)?\s*(.*)$",
        text.strip(),
        re.DOTALL,
    )
    if m:
        feedback = m.group(2).strip()
        return f"指导: {feedback}" if feedback else "已拒绝执行该工具"
    return text


class AntigravityAdapter(BaseAgentAdapter):
    """通用 Antigravity 存储与会话适配器（通过传入不同 root_dir 分别服务 IDE 与 CLI）。"""

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
            "~/.gemini/antigravity"
            if agent_type == "agy_ide"
            else "~/.gemini/antigravity-cli"
        )
        self.root_dir = root_dir or Path(os.path.expanduser(default_dir))
        self.summary_db_path = self.root_dir / "conversation_summaries.db"
        self.brain_dir = self.root_dir / "brain"
        self.conversations_dir = self.root_dir / "conversations"
        self.cli_bin = (
            shutil.which("agy")
            or os.path.expanduser(r"~\AppData\Local\agy\bin\agy.exe")
        )
        self.ls_bridge = AntigravityLanguageServerBridge()
        # (last_scan_ts, dict[cid, UnifiedSession])
        self._brain_scan_cache: tuple[float, dict[str, UnifiedSession]] = (0.0, {})
        # limit -> (db_sig, dict[cid, UnifiedSession])
        self._summary_db_cache: BoundedLRUCache = BoundedLRUCache(maxsize=20)
        # raw_session_id -> (mtime_ns, file_size, list[UnifiedMessageItem])
        self._messages_cache: BoundedLRUCache = BoundedLRUCache(maxsize=50)
        # raw_session_id -> (mtime, model_name)
        self._session_model_cache: BoundedLRUCache = BoundedLRUCache(maxsize=100)

    _shared_session_model_cache: dict[str, tuple[float, Optional[str]]] = {}

    def get_session_model(self, raw_session_id: str) -> Optional[str]:
        """从 conversations/<id>.db 读取该会话最近使用的真实模型 ID（带 LRU/共享内存缓存与只读免锁直连）。"""
        db_path = self.conversations_dir / f"{raw_session_id}.db"
        if not db_path.exists():
            if self.agent_type == "agy_cli":
                ide_db = Path(os.path.expanduser(f"~/.gemini/antigravity/conversations/{raw_session_id}.db"))
                if ide_db.exists():
                    db_path = ide_db
                else:
                    return None
            else:
                return None

        try:
            mtime = db_path.stat().st_mtime
            cached = self._session_model_cache.get(raw_session_id) or self._shared_session_model_cache.get(raw_session_id)
            if cached and cached[0] == mtime:
                return cached[1]

            model_id: Optional[str] = None
            sql = "SELECT data FROM gen_metadata ORDER BY idx DESC LIMIT 1"
            rows = safe_query_external_sqlite(db_path, sql, ())
            if rows and rows[0].get("data"):
                data = rows[0]["data"]
                if isinstance(data, bytes):
                    model_id = extract_protobuf_model_id(data)

            self._session_model_cache[raw_session_id] = (mtime, model_id)
            self.__class__._shared_session_model_cache[raw_session_id] = (mtime, model_id)
            return model_id
        except Exception as exc:
            logger.debug(f"读取会话 {raw_session_id[:8]} 模型异常: {exc}")

        return None

    def invalidate_cache(self) -> None:
        """重置适配器内部短效缓存（供外部 watchfiles 事件或手动触发时多态调用）。"""
        self._summary_db_cache.clear()
        self._brain_scan_cache = (0.0, {})

    def is_external_session_active(self, raw_session_id: str) -> bool:
        """极速单行探测指定 Antigravity 会话是否处于外部运行/活跃态（耗时 < 2ms，避免调用 list_sessions 全量扫描）。"""
        if self.summary_db_path.exists():
            sql = "SELECT status, not_fully_idle FROM conversation_summaries WHERE conversation_id = ? AND killed = 0 LIMIT 1"
            rows = safe_query_external_sqlite(self.summary_db_path, sql, (raw_session_id,))
            if rows:
                r = rows[0]
                not_idle = bool(r.get("not_fully_idle"))
                raw_status = str(r.get("status") or "")
                if self.agent_type == "agy_ide" and not_idle:
                    return True
                if not_idle or "RUNNING" in raw_status.upper():
                    return True

        tp = self.get_session_transcript_path(raw_session_id)
        if tp and tp.exists():
            try:
                is_active, _ = inspect_transcript_tail_status(tp)
                if is_active:
                    return True
            except Exception as exc:
                logger.debug(f"探测 transcript 状态异常 ({raw_session_id[:8]}): {exc}")
        return False

    def get_active_session_ids(self) -> list[str]:
        """极速多态返回 Antigravity 当前处于原生活跃态的 unified_id 列表（耗时 < 3ms）。"""
        active_ids: list[str] = []
        if self.summary_db_path.exists():
            sql = """
                SELECT conversation_id
                FROM conversation_summaries
                WHERE killed = 0 AND (not_fully_idle = 1 OR status LIKE '%RUNNING%')
                LIMIT 5
            """
            rows = safe_query_external_sqlite(self.summary_db_path, sql)
            for r in rows:
                cid = r.get("conversation_id")
                if cid:
                    active_ids.append(f"{self.agent_type}:{cid}")
        return active_ids

    def get_session_transcript_path(self, raw_session_id: str) -> Optional[Path]:
        """返回 Antigravity 会话对应的本地 transcript.jsonl 路径。"""
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
        """多态判定变动是否属于 Antigravity 核心会话存储（transcript.jsonl 或 SQLite 数据库）。"""
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
        """从 conversation_summaries.db 只读查询会话列表，并补充扫描 brain 目录（带 WAL 签名与目录扫描缓存）。"""
        sessions_map: dict[str, UnifiedSession] = {}

        if self.summary_db_path.exists():
            sig = self._get_summary_db_sig()
            cached_db = self._summary_db_cache.get(limit)
            if cached_db and cached_db[0] == sig:
                sessions_map = {k: copy.copy(v) for k, v in cached_db[1].items()}
            else:
                sql = """
                    SELECT conversation_id, title, preview, step_count,
                           last_modified_time, workspace_uris, status, not_fully_idle
                    FROM conversation_summaries
                    WHERE killed = 0
                    ORDER BY last_modified_time DESC
                    LIMIT ?
                """
                rows = safe_query_external_sqlite(self.summary_db_path, sql, (limit,))
                for r in rows:
                    cid = r.get("conversation_id") or ""
                    if not cid:
                        continue
                    not_idle = bool(r.get("not_fully_idle"))
                    raw_status = str(r.get("status") or "")
                    if self.agent_type == "agy_ide" and not_idle:
                        status = "desktop_active"
                    elif not_idle or "RUNNING" in raw_status.upper():
                        status = "running"
                    else:
                        status = "idle"

                    raw_title = clean_denied_tool_instruction(re.sub(r"\s+", " ", (r.get("title") or "").strip()))
                    raw_preview = clean_denied_tool_instruction(re.sub(r"\s+", " ", (r.get("preview") or "").strip()).strip("│ "))
                    project_path = decode_workspace_uri(str(r.get("workspace_uris") or ""))

                    # 过滤自动化探针/临时测试残留会话（如 cmem 探针生成的 flag.txt / marker.txt 测试记录，结合路径防误伤）
                    if is_temporary_probe_session(raw_title, raw_preview, project_path):
                        continue

                    # 过滤无真实 transcript 对话文件的孤儿异常会话（如无标题且以 [已拒绝工具 开头的异常记录）
                    tp = self.get_session_transcript_path(cid)
                    raw_prev_str = str(r.get("preview") or "").strip()
                    raw_title_str = str(r.get("title") or "").strip()
                    if (not tp or not tp.exists()) and not raw_title_str and (raw_prev_str.startswith("[已拒绝工具") or not raw_prev_str):
                        continue

                    # 过滤无标题且无预览的内部 Subagent 会话（首条消息为 SYSTEM_MESSAGE 而非用户输入）
                    if not raw_title and not raw_preview:
                        if tp and tp.exists():
                            try:
                                with open(tp, "r", encoding="utf-8", errors="replace") as tf:
                                    first_ln = tf.readline().strip()
                                if first_ln and '"SYSTEM_MESSAGE"' in first_ln:
                                    continue
                            except OSError as exc:
                                logger.debug(f"检查内部子会话首行跳过 ({cid[:8]}): {exc}")

                    title = raw_title or (raw_preview[:48] if raw_preview else f"会话 {cid[:8]}")
                    preview = raw_preview or title
                    updated_at = str(r.get("last_modified_time") or utc_now_iso()).replace(" ", "T")

                    model_name = self.get_session_model(cid) or ""
                    sessions_map[cid] = UnifiedSession(
                        unified_id=f"{self.agent_type}:{cid}",
                        agent_type=self.agent_type,
                        agent_label=self.agent_label,
                        raw_session_id=cid,
                        title=title,
                        preview=preview[:160],
                        project_path=project_path,
                        status=status,
                        step_count=int(r.get("step_count") or 0),
                        updated_at=updated_at,
                        model_name=model_name,
                        desktop_locked=(self.agent_type == "agy_ide" and not_idle),
                    )
                self._summary_db_cache[limit] = (
                    sig,
                    {k: copy.copy(v) for k, v in sessions_map.items()},
                )

        # 补充扫描 brain 目录中存在 transcript.jsonl 但尚未写入 summary 表的活跃会话（带 3s 内存缓存防高频 I/O 与降低感知延迟）
        if self.brain_dir.exists():
            now_mono = time.monotonic()
            cached_ts, cached_brain_map = self._brain_scan_cache
            if now_mono - cached_ts > 3.0:
                new_brain_map: dict[str, UnifiedSession] = {}
                try:
                    for conv_dir in self.brain_dir.iterdir():
                        if not conv_dir.is_dir():
                            continue
                        cid = conv_dir.name
                        # 核心性能优化：若已从 summary 表加载，直接跳过昂贵的磁盘与模型解析
                        if cid in sessions_map:
                            continue
                        transcript = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
                        if transcript.exists():
                            fallback_title = f"活跃会话 {cid[:8]}"
                            try:
                                with open(transcript, "r", encoding="utf-8", errors="replace") as tf:
                                    first_ln = tf.readline().strip()
                                if first_ln:
                                    if '"SYSTEM_MESSAGE"' in first_ln:
                                        continue
                                    first_obj = json.loads(first_ln)
                                    if first_obj.get("type") == "USER_INPUT" and first_obj.get("content"):
                                        cleaned_first = re.sub(
                                            r"\s+", " ", clean_agy_user_content(str(first_obj["content"]))
                                        ).strip()
                                        if cleaned_first:
                                            if is_temporary_probe_session(cleaned_first, cleaned_first, ""):
                                                continue
                                            fallback_title = cleaned_first[:48]
                            except Exception as exc:
                                logger.debug(f"提取活跃会话标题异常 ({cid[:8]}): {exc}")

                            stat = transcript.stat()
                            mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
                            model_name = self.get_session_model(cid) or ""
                            is_active, _ = inspect_transcript_tail_status(transcript)
                            new_status = ("desktop_active" if self.agent_type == "agy_ide" else "running") if is_active else "idle"
                            new_brain_map[cid] = UnifiedSession(
                                unified_id=f"{self.agent_type}:{cid}",
                                agent_type=self.agent_type,
                                agent_label=self.agent_label,
                                raw_session_id=cid,
                                title=fallback_title,
                                preview=fallback_title,
                                project_path="",
                                status=new_status,
                                step_count=0,
                                updated_at=mtime_iso,
                                model_name=model_name,
                                desktop_locked=(self.agent_type == "agy_ide" and is_active),
                            )
                except Exception as exc:
                    log_structured_error(
                        problem=f"扫描 {self.agent_label} brain 目录异常",
                        cause=str(exc),
                        fix="请检查 ~/.gemini 目录读取权限。",
                        exc=exc,
                    )
                self._brain_scan_cache = (now_mono, new_brain_map)
                cached_brain_map = new_brain_map

            for cid, b_sess in cached_brain_map.items():
                if cid not in sessions_map:
                    sessions_map[cid] = copy.copy(b_sess)

        # 实时联动：轻量级检查前 30 个最近会话的真实 transcript.jsonl 修改时间与活跃态（消解 IDE 流式输出未落库 DB 时的感知延迟）
        now_ts = time.time()
        for cid, sess in list(sessions_map.items())[:30]:
            tp = self.get_session_transcript_path(cid)
            if tp and tp.exists():
                try:
                    st = tp.stat()
                    file_mtime = st.st_mtime
                    file_iso = datetime.fromtimestamp(file_mtime, tz=timezone.utc).isoformat()
                    if file_iso > sess.updated_at:
                        sess.updated_at = file_iso
                    # 深度联动：结合末尾 Step 状态机与写入时间，平滑识别真实活跃态与闲置态（消除思考中抖动与假活迟钝）
                    is_active, _ = inspect_transcript_tail_status(tp)
                    if is_active:
                        if self.agent_type == "agy_ide":
                            sess.status = "desktop_active"
                            sess.desktop_locked = True
                        else:
                            sess.status = "running"
                    elif sess.status in ("running", "desktop_active"):
                        sess.status = "idle"
                        sess.desktop_locked = False
                except OSError as exc:
                    logger.debug(f"联动活跃会话时间戳异常 ({cid[:8]}): {exc}")

        sorted_list = sorted(sessions_map.values(), key=lambda s: s.updated_at, reverse=True)
        return sorted_list[:limit]

    def _parse_agy_step_dict(
        self,
        step: dict[str, Any],
        raw_session_id: str,
        fallback_idx: int,
        items: list[UnifiedMessageItem],
        last_assistant_container: list[Optional[UnifiedMessageItem]],
    ) -> bool:
        """根据已解析的 step 字典映射并追加结构化消息卡片。"""
        if not isinstance(step, dict):
            return False

        step_index = int(step.get("step_index", fallback_idx))
        step_type = str(step.get("type") or "")
        source = str(step.get("source") or "")
        created_at = str(step.get("created_at") or "")
        status = str(step.get("status") or "DONE")
        content_raw = str(step.get("content") or "")
        thinking_raw = str(step.get("thinking") or "").strip()
        raw_tool_calls = step.get("tool_calls") or []

        if step_type == "USER_INPUT" or source == "USER_EXPLICIT":
            clean_text = clean_agy_user_content(content_raw)
            items.append(
                UnifiedMessageItem(
                    item_id=f"{raw_session_id}:step:{step_index}",
                    step_index=step_index,
                    role="user",
                    created_at=created_at,
                    content=clean_text,
                    status=status,
                )
            )
            return True
        elif step_type == "ASK_QUESTION":
            ans_lines = [
                ln.strip()
                for ln in content_raw.splitlines()
                if ln.strip() and not ln.startswith(("Created At:", "Completed At:"))
            ]
            ans_text = "\n".join(ans_lines).strip()
            if ans_text:
                items.append(
                    UnifiedMessageItem(
                        item_id=f"{raw_session_id}:step:{step_index}",
                        step_index=step_index,
                        role="user",
                        created_at=created_at,
                        content=ans_text,
                        status=status,
                    )
                )
            return True
        elif step_type in AGY_TOOL_OUTPUT_TYPES or "TOOL" in step_type:
            last_assistant_item = last_assistant_container[0]
            if content_raw and last_assistant_item and last_assistant_item.tool_calls:
                placed = False
                for tc in last_assistant_item.tool_calls:
                    if not tc.output:
                        trunc_out, is_trunc, full_len = truncate_tool_output(tc.name, content_raw)
                        tc.output = trunc_out
                        tc.is_truncated = is_trunc
                        tc.full_output_len = full_len
                        placed = True
                        break
                if not placed:
                    last_tc = last_assistant_item.tool_calls[-1]
                    if is_read_only_tool(last_tc.name):
                        trunc_out, is_trunc, full_len = truncate_tool_output(last_tc.name, content_raw)
                        last_tc.output = trunc_out
                        last_tc.full_output_len += full_len
                    else:
                        combined = f"{last_tc.output}\n---\n{content_raw}" if last_tc.output else content_raw
                        c_out, c_trunc, c_len = truncate_tool_output(last_tc.name, combined)
                        last_tc.output = c_out
                        last_tc.is_truncated = c_trunc
                        last_tc.full_output_len = last_tc.full_output_len + c_len
            return True
        elif step_type == "PLANNER_RESPONSE" or (
            source == "MODEL" and (content_raw or thinking_raw or raw_tool_calls)
        ):
            parsed_tools: list[UnifiedToolCall] = []
            extracted_question_md: list[str] = []
            parsed_questions: list[dict[str, Any]] = []
            for tc_idx, tc in enumerate(raw_tool_calls):
                if not isinstance(tc, dict):
                    continue
                t_name = str(tc.get("name") or tc.get("tool_name") or "tool")
                t_args = tc.get("arguments") or tc.get("args") or tc.get("input") or {}
                if t_name == "ask_question" and isinstance(t_args, dict):
                    q_items = extract_interactive_questions(t_name, t_args)
                    parsed_questions.extend(q_items)
                    first_q = q_items[0]["question"] if q_items else "交互式提问"
                    parsed_tools.append(
                        UnifiedToolCall(
                            tool_id=str(tc.get("id") or f"tc_{step_index}_{tc_idx}"),
                            name=t_name,
                            args_summary=first_q[:120],
                            status=status,
                        )
                    )
                    continue
                args_summary, diff_text = extract_tool_summary_and_diff(t_name, t_args)
                add_c, del_c = count_diff_lines(diff_text)
                f_path = extract_tool_file_path(t_args)
                risk = classify_tool_risk(t_name, args_summary)
                parsed_tools.append(
                    UnifiedToolCall(
                        tool_id=str(tc.get("id") or f"tc_{step_index}_{tc_idx}"),
                        name=t_name,
                        args_summary=args_summary,
                        diff_text=diff_text,
                        diff_add_count=add_c,
                        diff_del_count=del_c,
                        risk_level=risk,
                        status=status,
                        file_path=f_path,
                    )
                )

            final_content = content_raw.strip()
            if not final_content and not parsed_questions and extracted_question_md:
                final_content = "\n\n".join(extracted_question_md)

            msg_item = UnifiedMessageItem(
                item_id=f"{raw_session_id}:step:{step_index}",
                step_index=step_index,
                role="assistant",
                created_at=created_at,
                content=final_content,
                thinking=thinking_raw,
                tool_calls=parsed_tools,
                questions=parsed_questions,
                status=status,
            )
            items.append(msg_item)
            if parsed_tools:
                last_assistant_container[0] = msg_item
            return True
        return False

    def _parse_agy_step_line(
        self,
        line: str,
        raw_session_id: str,
        fallback_idx: int,
        items: list[UnifiedMessageItem],
        last_assistant_container: list[Optional[UnifiedMessageItem]],
    ) -> bool:
        """解析单行 transcript.jsonl 数据并映射追加至 items 列表中（向后兼容）。"""
        line = line.strip()
        if not line:
            return False
        try:
            step = json.loads(line)
        except json.JSONDecodeError:
            return False
        return self._parse_agy_step_dict(
            step=step,
            raw_session_id=raw_session_id,
            fallback_idx=fallback_idx,
            items=items,
            last_assistant_container=last_assistant_container,
        )

    def get_session_messages(
        self, raw_session_id: str, limit: int = 120
    ) -> list[UnifiedMessageItem]:
        """解析 brain/<id>/.system_generated/logs/transcript.jsonl 为结构化对话卡片流（带 mtime_ns/文件游标增量缓存）。"""
        transcript_path = (
            self.brain_dir / raw_session_id / ".system_generated" / "logs" / "transcript.jsonl"
        )
        if not transcript_path.exists():
            return []

        try:
            st = transcript_path.stat()
            mtime_ns, fsize = st.st_mtime_ns, st.st_size
        except OSError:
            mtime_ns, fsize = 0, 0

        cached = self._messages_cache.get(raw_session_id)
        if cached and cached[0] == mtime_ns and cached[1] == fsize:
            return cached[2][-limit:] if limit > 0 else list(cached[2])

        # 增量 Seek 读取判定：文件发生追加写且先前有成功读取记录
        if (
            cached
            and len(cached) >= 4
            and fsize > cached[1]
            and cached[3] > 0
        ):
            try:
                items = list(cached[2])
                last_assistant = next(
                    (it for it in reversed(items) if it.role == "assistant" and it.tool_calls),
                    None,
                )
                last_assistant_box: list[Optional[UnifiedMessageItem]] = [last_assistant]
                with open(transcript_path, "rb") as f:
                    f.seek(cached[3])
                    inc_raw = f.read()

                inc_lines = inc_raw.splitlines(keepends=True)
                # 安全防护：若最后一行正在刷盘尚未写出换行符，丢弃半截行
                if inc_lines and not inc_lines[-1].endswith((b"\n", b"\r")):
                    inc_lines = inc_lines[:-1]

                start_idx = len(items)
                consumed_bytes = cached[3]
                for idx_offset, b_line in enumerate(inc_lines):
                    u_line = b_line.decode("utf-8", errors="replace").strip()
                    if not u_line:
                        consumed_bytes += len(b_line)
                        continue

                    # 语法完整性检验：测试 JSON 是否完整闭合
                    try:
                        step_obj = json.loads(u_line)
                    except json.JSONDecodeError:
                        # 仅当末行非空且 JSON 语法残缺时判定为流式写入未完成，游标保留在行首等待补齐
                        if idx_offset == len(inc_lines) - 1:
                            logger.debug(f"增量读取时末行正在流式写入中 ({raw_session_id[:8]})，游标安全保留在行首")
                            break
                        consumed_bytes += len(b_line)
                        continue

                    # 只要 JSON 语法完整，物理偏移量即合法推进（杜绝系统无气泡行导致游标脱节）
                    consumed_bytes += len(b_line)
                    self._parse_agy_step_dict(
                        step=step_obj,
                        raw_session_id=raw_session_id,
                        fallback_idx=start_idx + idx_offset,
                        items=items,
                        last_assistant_container=last_assistant_box,
                    )

                self._messages_cache[raw_session_id] = (mtime_ns, fsize, items, consumed_bytes)
                return items[-limit:] if limit > 0 else items
            except Exception as inc_exc:
                logger.debug(f"增量读取 Antigravity 会话 {raw_session_id[:8]} 异常，回退全量解析: {inc_exc}")

        # 全量冷读取
        items: list[UnifiedMessageItem] = []
        last_assistant_box = [None]
        raw_b_lines: list[bytes] = []
        try:
            with open(transcript_path, "rb") as f:
                all_raw = f.read()
            raw_b_lines = all_raw.splitlines(keepends=True)
            if raw_b_lines and not raw_b_lines[-1].endswith((b"\n", b"\r")):
                raw_b_lines = raw_b_lines[:-1]
        except Exception as exc:
            log_structured_error(
                problem=f"读取 {self.agent_label} 会话日志失败 ({raw_session_id[:8]})",
                cause=str(exc),
                fix="系统将在下次文件刷新时自动重试。",
                exc=exc,
            )
            return []

        consumed_bytes = 0
        for idx, b_line in enumerate(raw_b_lines):
            u_line = b_line.decode("utf-8", errors="replace").strip()
            if not u_line:
                consumed_bytes += len(b_line)
                continue

            try:
                step_obj = json.loads(u_line)
            except json.JSONDecodeError:
                if idx == len(raw_b_lines) - 1:
                    logger.debug(f"全量读取时末行正在流式写入中 ({raw_session_id[:8]})，游标安全保留在行首")
                    break
                consumed_bytes += len(b_line)
                continue

            consumed_bytes += len(b_line)
            self._parse_agy_step_dict(
                step=step_obj,
                raw_session_id=raw_session_id,
                fallback_idx=idx,
                items=items,
                last_assistant_container=last_assistant_box,
            )

        self._messages_cache[raw_session_id] = (mtime_ns, fsize, items, consumed_bytes)
        return items[-limit:] if limit > 0 else items

    def send_prompt_via_ls(
        self, raw_session_id: str, prompt: str, model_id: str = ""
    ) -> bool:
        """封装下沉：尝试通过桌面端 Language Server Connect-RPC 直连下发指令。"""
        return self.ls_bridge.send_cascade_message(raw_session_id, prompt, model_id)

    def cancel_via_ls(self, raw_session_id: str) -> bool:
        """封装下沉：尝试通过桌面端 Language Server Connect-RPC 取消会话。"""
        return self.ls_bridge.cancel_cascade(raw_session_id)

    def get_full_step_output(self, raw_session_id: str, step_index: int) -> str:

        """按需分页/全量读取被 4KB 截断的单步原始输出。"""
        transcript_path = (
            self.brain_dir / raw_session_id / ".system_generated" / "logs" / "transcript.jsonl"
        )
        if not transcript_path.exists():
            return ""
        try:
            with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
                for idx, line in enumerate(f):
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        if int(data.get("step_index", idx)) == step_index:
                            return json.dumps(data, ensure_ascii=False, indent=2)
                    except Exception:
                        continue
        except Exception as exc:
            log_structured_error(
                problem="读取单步完整输出失败",
                cause=str(exc),
                fix="请确认 transcript.jsonl 文件存在。",
                exc=exc,
            )
        return ""

    def send_ide_message(
        self, raw_session_id: str, prompt: str, model_id: str = ""
    ) -> bool:
        """封装调用内部 language server bridge 发送 IDE 级消息（消除 Feature Envy）。"""
        return self.ls_bridge.send_cascade_message(raw_session_id, prompt, model_id)

    def cancel_ide_session(self, raw_session_id: str) -> bool:
        """封装调用内部 language server bridge 取消 IDE 级会话执行（消除 Feature Envy）。"""
        return self.ls_bridge.cancel_cascade(raw_session_id)

    def try_direct_dispatch(
        self,
        raw_session_id: str,
        prompt: str,
        model_name: str = "",
        workdir: str = "",
    ) -> Optional[dict[str, Any]]:
        """若为 Antigravity IDE 且桌面端 LS 在线，通过 Connect-RPC 直发指令，避免克隆开销。"""
        if self.agent_type == "agy_ide":
            if self.ls_bridge.send_cascade_message(raw_session_id, prompt, model_name):
                return {"via_ls_rpc": True}
        return None

    def cancel_session(self, raw_session_id: str) -> bool:
        """若为 Antigravity IDE 会话，通过 Connect-RPC 取消正在运行的 Cascade 任务。"""
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
                            "input": tc.get("args") or tc.get("arguments") or tc.get("input") or {},
                        }
                    )

        # 兼容标准 tool_use 块与权限请求帧
        msg = event.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("content"), list):
            for b in msg["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tool_blocks.append(b)
        ev_type = str(event.get("type") or "")
        if ev_type in ("permission_request", "control_request", "tool_call"):
            tool_blocks.append(event)
        return tool_blocks

    def get_session_token_usage(
        self, raw_session_id: str, model_name: str = ""
    ) -> dict[str, Any]:
        """从 Antigravity brain transcript.jsonl 中提取会话 Token 统计与上下文水位（带 mtime 内存缓存）。"""
        limit = resolve_model_context_limit(model_name)
        tp = (
            self.brain_dir
            / raw_session_id
            / ".system_generated"
            / "logs"
            / "transcript.jsonl"
        )
        if not tp.is_file():
            return SessionTokenUsage(context_limit=limit).to_dict()

        cache_key = str(tp)
        try:
            mtime = tp.stat().st_mtime_ns
        except Exception:
            mtime = 0

        cached = self._token_usage_cache.get(cache_key)
        if cached and cached[0] == mtime:
            res = dict(cached[1])
            res["context_limit"] = limit
            res["context_percent"] = (
                round((res["context_tokens"] / limit) * 100, 1) if limit > 0 else 0.0
            )
            return res

        latest_input = 0
        latest_output = 0
        latest_cache_read = 0
        total_input = 0
        total_output = 0
        total_cache_read = 0

        try:
            with open(tp, "r", encoding="utf-8", errors="replace") as f:
                # 若日志文件超过 512KB，直接偏移至尾部 256KB 进行最新 token 解析，消除大文件 I/O 阻塞
                try:
                    fsize = tp.stat().st_size
                    if fsize > 512 * 1024:
                        f.seek(fsize - 256 * 1024)
                        f.readline()  # 舍弃可能被切断的首行残缺数据
                except OSError:
                    pass

                for line in f:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                        in_tokens = record.get("input_tokens") or 0
                        out_tokens = record.get("output_tokens") or 0
                        cr_tokens = record.get("cache_read_tokens") or 0
                        if in_tokens or out_tokens or cr_tokens:
                            latest_input = in_tokens
                            latest_output = out_tokens
                            latest_cache_read = cr_tokens
                            total_input += in_tokens
                            total_output += out_tokens
                            total_cache_read += cr_tokens
                    except json.JSONDecodeError:
                        continue
        except OSError as exc:
            logger.debug(f"读取 Antigravity 会话 transcript 跳过: {exc}")

        latest_context = latest_input + latest_cache_read
        pct = round((latest_context / limit) * 100, 1) if limit > 0 else 0.0
        data = {
            "context_tokens": latest_context,
            "context_limit": limit,
            "context_percent": pct,
            "total_tokens": total_input + total_output,
            "last_output_tokens": latest_output,
            "cache_read_tokens": total_cache_read,
        }
        self._token_usage_cache[cache_key] = (mtime, data)
        return data

    def get_default_model_id(self) -> str:
        """获取与电脑端 Antigravity IDE / CLI 真实选定保持实时一致的默认模型 ID。"""
        # 1. 优先从当前 root_dir 下的 antigravity_state.pbtxt 读取用户在电脑端最后选定的真实模型
        state_file = self.root_dir / "antigravity_state.pbtxt"
        if not state_file.exists() and self.agent_type == "agy_cli":
            ide_state = Path(os.path.expanduser("~/.gemini/antigravity/antigravity_state.pbtxt"))
            if ide_state.exists():
                state_file = ide_state

        parsed_model = parse_state_selected_model(state_file)
        if parsed_model:
            return parsed_model

        # 2. 尝试从最近活跃的真实会话中推断当前电脑端正在使用的模型
        try:
            conv_dirs = [self.conversations_dir]
            if self.agent_type == "agy_cli":
                conv_dirs.append(Path(os.path.expanduser("~/.gemini/antigravity/conversations")))
            for cdir in conv_dirs:
                if cdir.exists():
                    db_files = sorted(
                        cdir.glob("*.db"),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )
                    for dbf in db_files[:3]:
                        recent_cid = dbf.stem
                        m = self.get_session_model(recent_cid)
                        if m:
                            return m
        except Exception as exc:
            logger.debug(f"从最近活跃会话推断默认模型异常: {exc}")

        # 3. 从 LS Bridge Connect-RPC 获取
        ls_def = self.ls_bridge.get_default_model_id()
        if ls_def:
            return ls_def

        # 4. 终极动态兜底：从对应 Agent 的注册模型清单中选择第 1 个推荐模型，不硬编码任何固定值
        from backend.engines.process_pool import AGENT_MODELS
        base_models = AGENT_MODELS.get(self.agent_type, [])
        rec_model = next((m["id"] for m in base_models if m.get("is_recommended")), None)
        return rec_model or (base_models[0]["id"] if base_models else "gemini-3.8-flash-medium")

    def get_available_models_with_quota(self) -> list[dict[str, Any]]:
        """将 LS 实时 quota 剩余比例与默认模型合并进模型清单，严格保持与电脑端 IDE 排序一致。"""
        configs = self.ls_bridge.get_cascade_model_configs()
        default_mid = self.get_default_model_id()
        q_map: dict[str, dict[str, Any]] = {}
        for c in configs:
            if c.get("modelId"):
                q_map[c["modelId"].lower()] = c
            if c.get("label"):
                q_map[c["label"].lower()] = c

        from backend.engines.process_pool import AGENT_MODELS
        base_models = AGENT_MODELS.get(self.agent_type, [])
        merged: list[dict[str, Any]] = []
        for bm in base_models:
            item = dict(bm)
            matched = q_map.get(bm["id"].lower()) or q_map.get(bm["label"].lower())
            if matched:
                item["quota"] = matched["quota"]
                item["reset_time"] = matched.get("reset_time")
                if matched.get("tag") and not item.get("tag"):
                    item["tag"] = matched["tag"]
                if matched.get("is_recommended"):
                    item["is_recommended"] = True
            else:
                item["quota"] = 1.0
            if item["id"] == default_mid:
                item["is_default"] = True
            merged.append(item)

        sort_labels = getattr(self.ls_bridge, "_cached_sort_labels", [])
        if sort_labels:
            label_index = {lbl.lower(): i for i, lbl in enumerate(sort_labels)}
            merged.sort(key=lambda m: (0 if m.get("is_default") else 1, label_index.get(m["label"].lower(), 999)))
        return merged



def fork_ide_session_to_cli(
    raw_session_id: str,
    ide_root: Optional[Path] = None,
    cli_root: Optional[Path] = None,
) -> tuple[bool, str]:
    """
    安全克隆接力（Fork-to-CLI）：
    将 `Antigravity IDE` (`~/.gemini/antigravity`) 中的指定会话文件（`conversations/<id>.db`、
    `brain/<id>` 及 `conversation_summaries` 索引行）单向安全同步至 `Antigravity CLI` (`~/.gemini/antigravity-cli`)。

    这样既解决了 `agy.exe` 无 `--data-dir` 参数无法直接找到 IDE 会话的问题，
    又彻底杜绝了外部 CLI 进程直接改写桌面端 IDE 正在占用的 SQLite 数据库引发的双写脑裂！
    """
    src_root = ide_root or Path(os.path.expanduser("~/.gemini/antigravity"))
    dst_root = cli_root or Path(os.path.expanduser("~/.gemini/antigravity-cli"))

    try:
        (dst_root / "conversations").mkdir(parents=True, exist_ok=True)
        (dst_root / "brain").mkdir(parents=True, exist_ok=True)

        # 1. 拷贝 conversations/<id>.db
        src_conv_db = src_root / "conversations" / f"{raw_session_id}.db"
        dst_conv_db = dst_root / "conversations" / f"{raw_session_id}.db"
        if src_conv_db.exists():
            shutil.copy2(src_conv_db, dst_conv_db)

        # 2. 同步 brain/<id> 目录
        src_brain = src_root / "brain" / raw_session_id
        dst_brain = dst_root / "brain" / raw_session_id
        if src_brain.exists():
            shutil.copytree(src_brain, dst_brain, dirs_exist_ok=True)

        # 3. 同步 conversation_summaries.db 中的元数据行
        src_sum_db = src_root / "conversation_summaries.db"
        dst_sum_db = dst_root / "conversation_summaries.db"
        if src_sum_db.exists() and dst_sum_db.exists():
            rows = safe_query_external_sqlite(
                src_sum_db,
                "SELECT * FROM conversation_summaries WHERE conversation_id = ?",
                (raw_session_id,),
            )
            if rows:
                row = rows[0]
                cols = list(row.keys())
                placeholders = ", ".join(["?"] * len(cols))
                col_names = ", ".join([f"`{c}`" for c in cols])
                conn = sqlite3.connect(str(dst_sum_db), timeout=3.0)
                try:
                    conn.execute("PRAGMA busy_timeout = 3000;")
                    conn.execute(
                        f"INSERT OR REPLACE INTO conversation_summaries ({col_names}) VALUES ({placeholders})",
                        tuple(row[c] for c in cols),
                    )
                    conn.commit()
                finally:
                    conn.close()

        logger.info(f"已将 Antigravity IDE 会话 {raw_session_id[:8]} 安全克隆同步至 AGY CLI 环境")
        return True, f"agy_cli:{raw_session_id}"
    except Exception as exc:
        msg = log_structured_error(
            problem=f"克隆 Antigravity IDE 会话 ({raw_session_id[:8]}) 至 CLI 失败",
            cause=str(exc),
            fix="请确认 ~/.gemini/antigravity-cli 目录可写且磁盘空间充足。",
            exc=exc,
        )
        return False, msg


CSRF_TOKEN_RE = re.compile(r"--csrf_token[\s=]+([0-9a-fA-F-]{8,})")
LS_SERVICE_PREFIX = "/exa.language_server_pb.LanguageServerService"


def encode_connect_envelope(body: dict[str, Any]) -> bytes:
    """编码 Connect-RPC 5 字节流式协议帧 (`[0x00][uint32_be_len][json_bytes]`)。"""
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return b"\x00" + len(raw).to_bytes(4, "big") + raw



class AntigravityLanguageServerBridge:
    """
    借鉴 `Antigravity-Deck` (`src/detector.js` & `src/ls-stream.js`) 的轻量级 Hub Language Server 桥接器：
    1. 自动嗅探本机运行中的 `language_server` 进程 `--csrf_token` 与本地监听端口（带 15s 内存缓存）。
    2. 支持通过 Connect-RPC 协议直接向桌面端活跃的 Antigravity IDE 会话发送指令与取消任务，
       实现手机与电脑桌面 IDE 窗口 100% 同步（当桌面端 IDE 未开启时自动回退至 `fork_ide_session_to_cli`）。
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
        """扫描进程表提取 (pid, csrf_token)（优先使用 psutil 底层 C 扩展快速查询，未安装时安全回退至系统命令）。"""
        results: list[tuple[int, str]] = []
        try:
            import psutil
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
            logger.debug(f"psutil 扫描 language_server 失败，回退至命令行: {ps_exc}")

        try:
            if sys.platform == "win32":
                ps_bin = (
                    shutil.which("powershell.exe")
                    or os.path.expandvars(r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe")
                    or shutil.which("pwsh")
                    or "powershell"
                )
                ps_cmd = (
                    "Get-CimInstance Win32_Process -Filter \"Name LIKE '%language_server%'\" "
                    "| Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
                )
                proc = subprocess.run(
                    [ps_bin, "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=2.5,
                )
                out = (proc.stdout or "").strip()
                if out:
                    data = json.loads(out)
                    rows = data if isinstance(data, list) else [data]
                    for r in rows:
                        if not isinstance(r, dict):
                            continue
                        pid = int(r.get("ProcessId") or 0)
                        cmdline = str(r.get("CommandLine") or "")
                        m = CSRF_TOKEN_RE.search(cmdline)
                        if pid > 0 and m:
                            results.append((pid, m.group(1)))
            else:
                proc = subprocess.run(
                    ["ps", "-eo", "pid,args"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=2.0,
                )
                for line in (proc.stdout or "").splitlines():
                    if "language_server" not in line and "csrf_token" not in line:
                        continue
                    m = CSRF_TOKEN_RE.search(line)
                    parts = line.strip().split(None, 1)
                    if m and parts and parts[0].isdigit():
                        results.append((int(parts[0]), m.group(1)))
        except Exception as exc:
            logger.debug(f"扫描 language_server 进程信息已跳过: {exc}")
        return results

    @staticmethod
    def _find_listening_ports(pid: int) -> list[int]:
        """查找指定 PID 在本机监听的 TCP 端口列表（优先使用 psutil 免开子进程）。"""
        ports: list[int] = []
        try:
            import psutil
            p = psutil.Process(pid)
            for conn in p.net_connections(kind="tcp"):
                if conn.status == psutil.CONN_LISTEN and conn.laddr:
                    port = getattr(conn.laddr, "port", None)
                    if port and port not in ports:
                        ports.append(port)
            if ports:
                return ports[:6]
        except Exception as ps_exc:
            logger.debug(f"psutil 查询 PID={pid} 端口失败，回退至 netstat: {ps_exc}")

        try:
            if sys.platform == "win32":
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
                    if "LISTENING" not in line:
                        continue
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
            logger.debug(f"查询 PID={pid} 端口失败: {exc}")
        return ports[:6]

    def _probe_connect_port(self, port: int, csrf_token: str) -> Optional[str]:
        """探测指定端口是否响应 Connect-RPC `GetUserStatus`，返回 `'https'` 或 `'http'`。"""
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
            logger.debug(f"Connect-RPC POST {method} 探测失败 ({url}): {exc}")
        return False, None

    def send_cascade_message(
        self, cascade_id: str, prompt: str, model_id: str = ""
    ) -> bool:
        """
        通过 Connect-RPC 直接向运行中的 Antigravity IDE 会话发送用户指令。
        成功返回 True；若桌面端 IDE 未开启或会话不受该 LS 管辖则返回 False（上层自动回退 CLI）。
        """
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
        """从 LS Connect-RPC 获取当前服务端推荐或覆盖的默认模型 ID（由外层 Adapter 进行业务兜底）。"""
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
        """从 LS Connect-RPC 获取包含实时配额剩余比例的真实模型清单（带 10s 内存缓存）。"""
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
            # 提取电脑端 IDE 当前选定的默认模型
            default_override = data.get("defaultOverrideModelConfig") or {}
            moa = default_override.get("modelOrAlias") or {}
            def_mid = moa.get("model") or moa.get("alias") or ""
            self._cached_default_model = def_mid

            # 提取官方推荐排序清单
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



