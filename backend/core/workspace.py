# -*- coding: utf-8 -*-
"""
工作区目录探测、Git 状态只读巡检与受控文档读取模块。

职责划分：
1. 识别工程项目特征（`.git`, `pyproject.toml`, `package.json` 等）与缓存过滤系统临时目录。
2. 轻量只读查询指定工作区的 Git 分支、未提交变更文件列表与 Unified Diff。
3. 本地文档与源码安全读取器（强制受控 Project.root_path 白名单与防 symlink/junction 逃逸）。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional, Sequence
from urllib.parse import unquote, urlparse

from backend.core.workspace_git import (
    get_git_repo_sig,
    get_git_workspace_diff,
    get_git_workspace_status,
)
from backend.security.path_guard import (
    ADHOC_DIR_NAMES,
    normalize_project_path,
    resolve_and_verify_path_within_roots,
)

_get_git_repo_sig = get_git_repo_sig

_DIR_MARKER_CACHE: dict[str, tuple[float, bool, str]] = {}

PROJECT_MARKER_NAMES = (
    ".git",
    "pyproject.toml",
    "package.json",
    "pom.xml",
    "go.mod",
    "Cargo.toml",
    "requirements.txt",
    "Makefile",
    "CMakeLists.txt",
    "build.gradle",
    ".idea",
    ".vscode",
    "README.md",
)


def decode_workspace_uri(raw_uri: str) -> str:
    """将 file:///d%3A/foo 形式的 URI 解码为干净的本地路径字符串。"""
    if not raw_uri:
        return ""
    first_uri = raw_uri.split(",")[0].strip().strip("[]\"'")
    if first_uri.startswith("file://"):
        parsed = urlparse(first_uri)
        decoded = unquote(parsed.path or "")
        if len(decoded) >= 3 and decoded[0] == "/" and decoded[2] == ":":
            decoded = decoded[1:]
        return normalize_project_path(decoded)
    return normalize_project_path(unquote(first_uri))


def evaluate_project_dir(
    raw_path: str,
    bookmarked_keys: Optional[set[str]] = None,
    hidden_keys: Optional[set[str]] = None,
) -> tuple[bool, str, str]:
    """判定指定目录是否属于真正的工程项目。"""
    norm = normalize_project_path(raw_path)
    if not norm:
        return False, "", ""

    key = norm.lower()
    parts = [p for p in norm.split("/") if p]
    folder_name = parts[-1] if parts else ""

    if hidden_keys and key in hidden_keys:
        return False, norm, ""

    if bookmarked_keys and key in bookmarked_keys:
        return True, norm, folder_name

    if not folder_name or folder_name.startswith(".") or folder_name.lower() in ADHOC_DIR_NAMES:
        return False, norm, ""

    try:
        home_norm = normalize_project_path(str(Path.home())).lower()
        if key == home_norm:
            return False, norm, ""
    except Exception:
        pass

    if len(parts) == 3 and parts[0].endswith(":") and parts[1].lower() == "users":
        return False, norm, ""
    if any(seg.lower() in ("windows", "system32", "syswow64") for seg in parts):
        return False, norm, ""

    now_ts = time.monotonic()
    cached = _DIR_MARKER_CACHE.get(key)
    if cached and cached[0] > now_ts:
        if cached[1]:
            return True, norm, cached[2]
        return False, norm, ""

    try:
        p_obj = Path(norm)
        if p_obj.is_dir():
            for marker in PROJECT_MARKER_NAMES:
                if (p_obj / marker).exists():
                    try:
                        real_name = p_obj.resolve().name or folder_name
                    except Exception:
                        real_name = folder_name
                    _DIR_MARKER_CACHE[key] = (now_ts + 120.0, True, real_name)
                    return True, norm, real_name
    except Exception:
        pass

    _DIR_MARKER_CACHE[key] = (now_ts + 120.0, False, "")
    return False, norm, ""


class WorkspaceDocumentReader:
    """工作区本地文档与源码安全读取器深模块。"""

    MAX_BYTES: int = 256 * 1024
    BLOCKED_DIR_NAMES = {
        ".ssh", ".aws", ".azure", ".kube", ".gnupg", ".git", ".docker", "credentials"
    }
    BLOCKED_NAMES = {
        ".env", ".env.local", ".env.production", ".env.staging", ".env.test",
        "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", "known_hosts", "authorized_keys",
        ".npmrc", ".pypirc", ".netrc", ".bash_history", ".zsh_history",
    }
    BLOCKED_EXTS = {
        ".exe", ".dll", ".so", ".dylib", ".db", ".sqlite", ".sqlite3",
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".tar", ".gz",
        ".pem", ".key", ".pfx", ".crt",
    }

    @classmethod
    def read_document(
        cls,
        raw_path: str,
        max_bytes: int = MAX_BYTES,
        *,
        allowed_roots: Optional[Sequence[str | Path]] = None,
    ) -> dict[str, Any]:
        """安全读取指定文件内容（强制受控 Project.root_path 白名单与防 symlink/junction 逃逸）。"""
        raw_clean = (raw_path or "").split("#")[0].split("?")[0].strip()
        norm_path = decode_workspace_uri(raw_clean)
        if not norm_path:
            raise ValueError("无效的文件路径")

        if not Path(norm_path).is_absolute():
            raise ValueError(f"不支持相对路径（缺少目录与盘符）: {norm_path}")

        file_obj = resolve_and_verify_path_within_roots(norm_path, allowed_roots or ())
        if not file_obj.exists() or not file_obj.is_file():
            raise FileNotFoundError(f"文件不存在: {file_obj.name}")

        lower_name = file_obj.name.lower()
        ext = file_obj.suffix.lower()
        parts_lower = {p.lower() for p in file_obj.parts}
        if any(bd in parts_lower for bd in cls.BLOCKED_DIR_NAMES):
            raise PermissionError(f"禁止读取系统敏感配置目录文件 ({file_obj.name})")

        if lower_name in cls.BLOCKED_NAMES or ext in cls.BLOCKED_EXTS or lower_name.startswith(".env"):
            raise PermissionError(f"暂不支持直接文本预览该类型文件 ({file_obj.name})")

        stat = file_obj.stat()
        raw_bytes = file_obj.read_bytes()
        is_truncated = len(raw_bytes) > max_bytes
        if is_truncated:
            raw_bytes = raw_bytes[:max_bytes]

        content = raw_bytes.decode("utf-8", errors="replace")
        if is_truncated:
            content += f"\n\n> [!NOTE]\n> 文档较大（共 {stat.st_size} 字节），手机端已截取前 256KB 展示。"

        return {
            "ok": True,
            "path": norm_path,
            "filename": file_obj.name,
            "ext": ext,
            "is_markdown": ext in (".md", ".markdown", ".mdx", ".txt"),
            "size_bytes": stat.st_size,
            "content": content,
        }
