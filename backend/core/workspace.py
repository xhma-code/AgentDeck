"""
工作区目录探测、Git 状态只读巡检与项目快捷工作流发现模块。

职责划分：
1. 识别真正工程项目特征（`.git`, `pyproject.toml`, `package.json` 等）与缓存过滤系统临时目录。
2. 兼容导出工作区 Git 状态、Diff 计算与项目快捷工作流发现能力。
3. 工作区本地文档与源码安全读取器（`WorkspaceDocumentReader`）。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from backend.core.logger import logger
from backend.core.workspace_git import (
    decode_workspace_uri,
    get_git_workspace_diff,
    get_git_workspace_status,
    normalize_project_path,
)
from backend.core.workspace_workflows import (
    discover_quick_workflows,
    parse_skill_meta,
)

# 目录工程特征磁盘探测缓存：norm_key -> (expire_ts, has_marker, real_folder_name)
_DIR_MARKER_CACHE: dict[str, tuple[float, bool, str]] = {}

# 识别“真正工程项目”的标志性文件/目录集合
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

# 随手打开的临时会话系统目录特征（强制排除出项目列表）
ADHOC_DIR_NAMES = {
    "system32",
    "syswow64",
    "windows",
    "downloads",
    "desktop",
    "documents",
    "temp",
    "tmp",
    "appdata",
}


def evaluate_project_dir(
    raw_path: str,
    bookmarked_keys: Optional[set[str]] = None,
    hidden_keys: Optional[set[str]] = None,
) -> tuple[bool, str, str]:
    """
    判定指定目录是否属于真正的工程项目（而非随手打开的临时会话文件夹）。
    返回 `(是否为真项目, 规范化路径, 项目名称)`：
    1. 若在手动隐藏集合 (`hidden_keys`) 中 -> False
    2. 若在手动收藏集合 (`bookmarked_keys`) 中 -> True
    3. 若为 Home 根目录、Windows/System32、Downloads、隐藏点目录 (.xxx) -> False
    4. 若目录包含 `.git` / `pyproject.toml` / `package.json` / `pom.xml` 等工程特征 -> True
    """
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

    # 排除隐藏目录（如 .claude-mem, .gemini）和常见系统/临时叶子目录名
    if not folder_name or folder_name.startswith(".") or folder_name.lower() in ADHOC_DIR_NAMES:
        return False, norm, ""

    # 排除用户 Home 根目录（如 C:/Users/username 或 /home/user）
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

    # 检查本地目录是否包含工程特征文件（带 120s 内存缓存，避免每次拉列表重复 stat 磁盘）
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
    def read_document(cls, raw_path: str, max_bytes: int = MAX_BYTES) -> dict[str, Any]:
        """
        安全读取指定文件内容。
        若路径无效抛出 ValueError；文件不存在抛出 FileNotFoundError；敏感文件拦截抛出 PermissionError。
        """
        raw_clean = (raw_path or "").split("#")[0].split("?")[0].strip()
        norm_path = decode_workspace_uri(raw_clean)
        if not norm_path:
            raise ValueError("无效的文件路径")

        file_obj = Path(norm_path).resolve()
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
