"""
Claude Code 本地 JSONL 会话存储、进程状态与通信适配器。

包含：
1. 监听并解析 `~/.claude/projects/` 下的会话 JSONL 数据。
2. 提取并格式化用户输入、思维链、工具调用与 Unified Diff。
3. 动态监测本机正在运行的 `claude` 进程状态。
4. 按需从原生日志中截取被 4KB 截断的超长工具输出。
"""

from __future__ import annotations
from backend.adapters.tool_utils import extract_snippet

import copy
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.adapters.base import (
    BaseAgentAdapter,
    BoundedLRUCache,
    SessionTokenUsage,
    UnifiedMessageItem,
    UnifiedSession,
    UnifiedToolCall,
    resolve_model_context_limit,
)
from backend.adapters.claude_parser import (
    clean_claude_user_text,
    compute_claude_token_usage,
    extract_claude_step_full_output,
    extract_record_plain_text,
    parse_claude_record_dict,
    parse_claude_record_line,
)
from backend.adapters.claude_scanner import (
    detect_active_claude_sessions,
    find_claude_session_file,
    parse_claude_session_file,
    read_claude_session_messages,
)
from backend.core.logger import log_structured_error, logger


class ClaudeCodeAdapter(BaseAgentAdapter):
    """Claude Code 本地存储与进程调度适配器。"""

    agent_type = "claude_code"
    agent_label = "Claude Code"

    def __init__(
        self,
        projects_dir: Optional[Path] = None,
        sessions_dir: Optional[Path] = None,
    ) -> None:
        super().__init__()
        default_projects = Path(os.path.expanduser("~/.claude/projects"))
        default_sessions = Path(os.path.expanduser("~/.claude/sessions"))
        self.projects_dir = projects_dir or default_projects
        if sessions_dir:
            self.sessions_dir = sessions_dir
        elif projects_dir and projects_dir != default_projects:
            self.sessions_dir = projects_dir.parent / "sessions"
        else:
            self.sessions_dir = default_sessions

        self.cli_bin = (
            shutil.which("claude")
            or os.path.expanduser(r"~\AppData\Roaming\npm\claude.cmd")
            or os.path.expanduser(r"~\.local\bin\claude.exe")
        )
        self._session_meta_cache: BoundedLRUCache = BoundedLRUCache(maxsize=100)
        self._session_file_map: BoundedLRUCache = BoundedLRUCache(maxsize=200)
        self._messages_cache: BoundedLRUCache = BoundedLRUCache(maxsize=40)
        self._token_usage_cache: BoundedLRUCache = BoundedLRUCache(maxsize=100)
        self._dir_scan_cache: tuple[float, list[Path]] = (0.0, [])

    _extract_record_plain_text = staticmethod(extract_record_plain_text)
    _parse_claude_record_dict = staticmethod(parse_claude_record_dict)
    _parse_claude_record_line = staticmethod(parse_claude_record_line)

    def get_active_sessions(self) -> dict[str, str]:
        """获取当前由外部运行的 Claude Code 进程活跃状态字典。"""
        return detect_active_claude_sessions(self.sessions_dir)

    def is_external_session_active(self, raw_session_id: str) -> bool:
        """轻量探测指定会话当前是否处于外部运行/活跃态。"""
        active_map = self.get_active_sessions()
        st = active_map.get(raw_session_id)
        if st in ("running", "desktop_active"):
            return True
        fpath = self._find_session_file(raw_session_id)
        if fpath and fpath.exists():
            try:
                if (time.time() - fpath.stat().st_mtime) < 3.0:
                    return True
            except OSError:
                pass
        return False

    def get_active_session_ids(self) -> list[str]:
        """返回当前处于外部原生运行/活跃态的 unified_id 列表。"""
        active_map = self.get_active_sessions()
        return [f"{self.agent_type}:{sid}" for sid in active_map.keys()]

    def invalidate_cache(self) -> None:
        """重置适配器内部短效缓存。"""
        self._dir_scan_cache = (0.0, [])
        self._session_meta_cache.clear()

    def invalidate_scan_cache(self) -> None:
        """清空会话列表短效扫描缓存。"""
        self._dir_scan_cache = (0.0, [])

    def is_available(self) -> tuple[bool, str]:
        """检查 Claude Code 环境就绪状态。"""
        bin_found = bool(self.cli_bin and os.path.exists(str(self.cli_bin)))
        dir_found = self.projects_dir.exists()
        if bin_found and dir_found:
            return True, f"已就绪 (CLI: {self.cli_bin})"
        if dir_found:
            return True, f"已挂载数据目录: {self.projects_dir} (未检测到全局 claude 命令)"
        if bin_found:
            return True, f"已找到 CLI: {self.cli_bin} (会话目录待初始化)"
        return False, "未找到 Claude Code CLI 且未初始化数据目录 ~/.claude"

    def get_watch_paths(self) -> list[Path]:
        """返回文件监听器需要监控的路径。"""
        paths = []
        if self.projects_dir.exists():
            paths.append(self.projects_dir)
        if self.sessions_dir.exists():
            paths.append(self.sessions_dir)
        return paths

    @classmethod
    def is_meaningful_storage_change(cls, path_str: str, filename: str) -> bool:
        """多态判定变动是否属于 Claude Code 会话日志（*.jsonl 排除 agent-*）或 sessions/*.json。"""
        s = path_str.lower().replace("\\", "/")
        n = filename.lower()
        if ".claude" not in s:
            return False
        return (
            (n.endswith(".jsonl") and not n.startswith("agent-"))
            or (n.endswith(".json") and "/sessions/" in s)
        )

    def _find_session_file(self, raw_session_id: str) -> Optional[Path]:
        """快速定位指定 session_id 对应的 `.jsonl` 文件。"""
        return find_claude_session_file(
            self.projects_dir, self._session_file_map, raw_session_id
        )

    def get_session_transcript_path(self, raw_session_id: str) -> Optional[Path]:
        """返回底层 JSONL 日志文件的绝对路径。"""
        return self._find_session_file(raw_session_id)

    def get_session(self, raw_session_id: str) -> Optional[UnifiedSession]:
        """按 ID 精确定位单个 Claude Code 会话摘要。"""
        fpath = self._find_session_file(raw_session_id)
        if not fpath or not fpath.exists():
            return None
        active_map = self.get_active_sessions()
        try:
            mtime = fpath.stat().st_mtime
            return parse_claude_session_file(
                fpath=fpath,
                mtime=mtime,
                active_map=active_map,
                agent_type=self.agent_type,
                agent_label=self.agent_label,
            )
        except OSError:
            return None

    def list_sessions(self, limit: int = 50) -> list[UnifiedSession]:
        """扫描 ~/.claude/projects 下所有 .jsonl 文件并提取会话元数据（带 mtime 内存缓存与短效扫描缓存）。"""
        if not self.projects_dir.exists():
            return []

        now_mono = time.monotonic()
        if now_mono - self._dir_scan_cache[0] < 2.5 and self._dir_scan_cache[1]:
            candidate_paths = self._dir_scan_cache[1]
        else:
            candidate_paths = []
            try:
                for fpath in self.projects_dir.rglob("*.jsonl"):
                    if not fpath.name.startswith("agent-"):
                        candidate_paths.append(fpath)
                self._dir_scan_cache = (now_mono, candidate_paths)
            except Exception as exc:
                log_structured_error(
                    problem="扫描 Claude Code 会话目录失败",
                    cause=str(exc),
                    fix="请检查 ~/.claude/projects 目录权限。",
                    exc=exc,
                )
                return []

        files: list[tuple[float, int, int, Path]] = []
        for fpath in candidate_paths:
            try:
                st = fpath.stat()
                self._session_file_map[fpath.stem] = fpath
                files.append((st.st_mtime, st.st_mtime_ns, st.st_size, fpath))
            except OSError:
                continue

        files.sort(key=lambda x: x[0], reverse=True)
        sessions: list[UnifiedSession] = []
        active_map = self.get_active_sessions()

        for mtime, mtime_ns, fsize, fpath in files[:limit]:
            f_key = str(fpath)
            cached = self._session_meta_cache.get(f_key)
            if cached and cached[0] == mtime_ns and cached[1] == fsize:
                s_copy = copy.copy(cached[2])
                sid_cached = s_copy.raw_session_id
                if sid_cached in active_map:
                    s_copy.status = active_map[sid_cached]
                elif s_copy.status in ("running", "desktop_active") and sid_cached not in active_map:
                    s_copy.status = "idle"
                sessions.append(s_copy)
                continue

            us = parse_claude_session_file(
                fpath=fpath,
                mtime=mtime,
                active_map=active_map,
                agent_type=self.agent_type,
                agent_label=self.agent_label,
            )
            if us:
                self._session_meta_cache[f_key] = (mtime_ns, fsize, us)
                sessions.append(us)

        return sessions

    def get_session_messages(
        self, raw_session_id: str, limit: int = 120
    ) -> list[UnifiedMessageItem]:
        """增量/全量解析 Claude Code 的 JSONL 文件映射为卡片流。"""
        fpath = self._find_session_file(raw_session_id)
        if not fpath or not fpath.exists():
            return []
        return read_claude_session_messages(
            fpath=fpath,
            raw_session_id=raw_session_id,
            messages_cache=self._messages_cache,
            limit=limit,
        )

    def get_full_step_output(self, raw_session_id: str, step_index: int) -> str:
        """按需读取 Claude Code 会话中指定步骤或工具的完整未截断输出。"""
        fpath = self._find_session_file(raw_session_id)
        if not fpath or not fpath.exists():
            return ""
        return extract_claude_step_full_output(fpath, raw_session_id, step_index)

    def build_cli_command(
        self,
        raw_session_id: str,
        prompt: str,
        execution_mode: str,
        model_name: str = "",
        approved_tools: Optional[list[str]] = None,
    ) -> list[str]:
        """为 Claude Code 组装流式命令行（支持 plan / accept_edits / auto / stdio 逐条拦截）。"""
        clean_model = (model_name or "").strip()
        if clean_model == "default":
            clean_model = ""
        bin_path = str(self.cli_bin or "claude")
        if sys.platform == "win32" and bin_path.lower().endswith((".cmd", ".bat")):
            cmd = ["cmd.exe", "/c", bin_path]
        else:
            cmd = [bin_path]
        cmd.extend([
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
        ])
        if clean_model:
            cmd.extend(["--model", clean_model])
        if self._find_session_file(raw_session_id):
            cmd.extend(["--resume", raw_session_id])
        else:
            cmd.extend(["--session-id", raw_session_id])

        if execution_mode == "plan":
            cmd.extend(["--permission-mode", "plan"])
        elif execution_mode == "accept_edits":
            cmd.extend(["--permission-mode", "acceptEdits"])
        elif execution_mode == "auto":
            cmd.append("--dangerously-skip-permissions")
        else:
            cmd.extend(["--permission-prompt-tool", "stdio"])
        if approved_tools:
            cmd.extend(["--allowedTools", ",".join(approved_tools)])
        return cmd

    def extract_stream_tool_blocks(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """从 Claude Code stream-json 帧中提取待审批工具调用。"""
        tool_blocks: list[dict[str, Any]] = []
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
        """解析 Claude Code 会话 JSONL 文件提取 token 统计（带 mtime 内存缓存）。"""
        limit = resolve_model_context_limit(model_name)
        fpath = self._find_session_file(raw_session_id)
        if not fpath or not fpath.exists():
            return SessionTokenUsage(context_limit=limit).to_dict()

        cache_key = str(fpath)
        try:
            mtime = fpath.stat().st_mtime_ns
        except OSError as err:
            logger.debug("获取 Claude 会话文件 mtime 失败: %s", err)
            mtime = 0

        cached = self._token_usage_cache.get(cache_key)
        if cached and cached[0] == mtime:
            res = dict(cached[1])
            res["context_limit"] = limit
            res["context_percent"] = (
                round((res["context_tokens"] / limit) * 100, 1) if limit > 0 else 0.0
            )
            return res

        data = compute_claude_token_usage(fpath, limit)
        self._token_usage_cache[cache_key] = (mtime, data)
        return data

    def delete_session(self, raw_session_id: str) -> bool:
        """彻底物理删除 Claude Code 本地会话 JSONL 文件。"""
        session_file = self._find_session_file(raw_session_id)
        if session_file and session_file.exists():
            try:
                session_file.unlink(missing_ok=True)
                self.invalidate_cache()
                return True
            except Exception as exc:
                logger.warning(f"删除 Claude 会话文件 {session_file} 失败: {exc}")
        return False

    def cancel_session(self, raw_session_id: str) -> bool:
        """尝试通过官方 claude stop 指令停止后台或外部运行的 Claude 会话。"""
        import subprocess
        bin_path = str(self.cli_bin or "claude")
        try:
            cmd = ["cmd.exe", "/c", bin_path, "stop", raw_session_id] if sys.platform == "win32" and bin_path.lower().endswith((".cmd", ".bat")) else [bin_path, "stop", raw_session_id]
            res = subprocess.run(cmd, capture_output=True, timeout=2.0)
            if res.returncode == 0:
                logger.info(f"已通过官方 claude stop 成功终止会话: {raw_session_id[:8]}")
                return True
        except Exception as exc:
            logger.debug(f"调用 claude stop 异常 ({raw_session_id[:8]}): {exc}")
        return False

    def rename_session(self, raw_session_id: str, new_title: str) -> bool:
        """为 Claude Code 会话同步更新本地标题缓存与伴生元数据。"""
        fpath = self._find_session_file(raw_session_id)
        if not fpath or not fpath.exists():
            return False
        try:
            meta_file = fpath.parent / f"{raw_session_id}.meta.json"
            meta_doc: dict[str, Any] = {}
            if meta_file.exists():
                try:
                    meta_doc = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception:
                    meta_doc = {}
            meta_doc["title"] = new_title
            meta_doc["updated_at"] = datetime.now(timezone.utc).isoformat()
            meta_file.write_text(json.dumps(meta_doc, ensure_ascii=False, indent=2), encoding="utf-8")
            self._session_meta_cache[raw_session_id] = meta_doc
            self.invalidate_cache()
            logger.info(f"已同步更新 Claude Code 会话元数据标题: {raw_session_id[:8]} -> {new_title}")
            return True
        except Exception as exc:
            logger.warning(f"同步更新 Claude Code 原生会话元数据失败: {exc}")
            return False

    def search_sessions_content(
        self, query: str, limit: int = 30
    ) -> list[dict[str, Any]]:
        """在 Claude Code 的 JSONL 会话文件中两阶段快速检索内容。"""
        results: list[dict[str, Any]] = []
        q = query.strip()
        if not q:
            return results
        q_bytes = q.lower().encode("utf-8", errors="ignore")

        files: list[Path] = []
        if self.sessions_dir.exists():
            files.extend(list(self.sessions_dir.glob("*.jsonl")))
        if self.projects_dir.exists():
            files.extend(list(self.projects_dir.glob("**/*.jsonl")))

        files.sort(key=lambda p: p.stat().st_mtime_ns if p.exists() else 0, reverse=True)

        for fpath in files:
            if len(results) >= limit:
                break
            try:
                with open(fpath, "rb") as fp:
                    raw_data = fp.read()
                if q_bytes not in raw_data.lower():
                    continue

                raw_session_id = fpath.stem
                msgs = self.get_session_messages(raw_session_id, limit=80)
                matched_item = None
                for m in reversed(msgs):
                    if m.content and q.lower() in m.content.lower():
                        matched_item = {"type": "content", "snippet": extract_snippet(m.content, q)}
                        break
                    if m.thinking and q.lower() in m.thinking.lower():
                        matched_item = {"type": "thinking", "snippet": extract_snippet(m.thinking, q)}
                        break
                    for tc in m.tool_calls:
                        if tc.args_summary and q.lower() in tc.args_summary.lower():
                            matched_item = {"type": "tool", "snippet": extract_snippet(tc.args_summary, q)}
                            break
                        if tc.output and q.lower() in tc.output.lower():
                            matched_item = {"type": "tool", "snippet": extract_snippet(tc.output, q)}
                            break

                if matched_item:
                    results.append({
                        "raw_session_id": raw_session_id,
                        "type": matched_item["type"],
                        "snippet": matched_item["snippet"],
                    })
                else:
                    results.append({
                        "raw_session_id": raw_session_id,
                        "type": "content",
                        "snippet": extract_snippet(raw_data.decode("utf-8", errors="ignore")[:300], q),
                    })
            except Exception as exc:
                logger.debug(f"检索 Claude 会话文件异常 ({fpath.name}): {exc}")

        return results

