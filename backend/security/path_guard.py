# -*- coding: utf-8 -*-
"""
Project.root_path 白名单与符号链接 / Windows Junction 逃逸校验器。

安全保证：
1. 默认闭合（Fail-Closed）：未提供任何已注册 Project 根目录白名单时，拒绝一切文件访问。
2. 真实路径包含性校验：目标文件经 resolve(strict=True) 解析后的真实物理路径必须严格位于某个合法 root_path 内部。
3. 逐级重解析点检查（Python 3.12 is_symlink + is_junction）：若项目子目录中存在指向当前项目根外部的
   符号链接或 Windows 目录联接（Junction），立即阻断并记录安全错误日志到 logs/error.log。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, Sequence

from backend.core.logger import log_structured_error
from backend.domain.exceptions import SecurityBoundaryError

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


def normalize_project_path(raw_path: str) -> str:
    """统一正斜杠、去除重复与末尾斜杠并规范化 Windows 盘符为小写。"""
    if not raw_path:
        return ""
    cleaned = re.sub(r"/+", "/", str(raw_path).strip().replace("\\", "/")).rstrip("/")
    if len(cleaned) >= 2 and cleaned[1] == ":":
        cleaned = cleaned[0].lower() + cleaned[1:]
    return cleaned


def _is_within_real_root(candidate_real: Path, root_real: Path) -> bool:
    """跨平台（兼容 Windows 盘符大小写不敏感）严格判定 candidate_real 位于 root_real 内部或相等。"""
    cand_norm = Path(os.path.normcase(str(candidate_real)))
    root_norm = Path(os.path.normcase(str(root_real)))
    return cand_norm.is_relative_to(root_norm)


def _is_link_or_junction(path_obj: Path) -> bool:
    """检测路径节点是否为符号链接或 Windows 目录联接 (Junction)。"""
    try:
        if path_obj.is_symlink():
            return True
        if hasattr(path_obj, "is_junction") and path_obj.is_junction():
            return True
    except OSError:
        return True
    return False


def resolve_and_verify_path_within_roots(
    target_path: str | Path,
    allowed_roots: Sequence[str | Path],
) -> Path:
    """
    校验目标文件路径严格归属于已授权的 Project 根目录白名单，且不存在 symlink/junction 逃逸。
    校验通过返回解析后的真实文件 Path；越权抛出 PermissionError（兼容路由 403 拦截）并写入 error.log。
    """
    valid_roots: list[tuple[Path, Path]] = []
    for raw_root in allowed_roots or ():
        root_str = str(raw_root or "").strip()
        if not root_str:
            continue
        try:
            lexical_root = Path(os.path.abspath(root_str))
            real_root = lexical_root.resolve(strict=True)
            if real_root.is_dir():
                valid_roots.append((lexical_root, real_root))
        except (OSError, RuntimeError):
            continue

    if not valid_roots:
        diag = log_structured_error(
            problem="工作区文件读取被拒绝（缺少受控项目根目录白名单）",
            cause="当前请求未提供任何有效且存在的已注册 Project.root_path 白名单",
            fix="请先在 AgentDeck 中注册或启用目标 Project 后再访问其内部文档。",
        )
        raise PermissionError(diag)

    lexical_target = Path(os.path.abspath(str(target_path)))

    # 1. 先检查字面路径是否落在某个合法根目录下，并逐级排查中间链路是否存在越界 symlink / junction
    matched_roots: list[tuple[Path, Path]] = []
    for lexical_root, real_root in valid_roots:
        if _is_within_real_root(lexical_target, lexical_root) or _is_within_real_root(lexical_target, real_root):
            matched_roots.append((lexical_root, real_root))

    if not matched_roots:
        diag = log_structured_error(
            problem="拦截越界文件读取请求（路径不在已注册项目白名单内）",
            cause=f"请求路径 '{lexical_target.name}' 不归属于任何已授权 Project.root_path",
            fix="仅允许访问已在 AgentDeck 登记的项目工作区目录内部文件。",
            context={"file_name": lexical_target.name},
        )
        raise PermissionError(diag)

    # 2. 解析真实物理路径（若文件不存在，上层期望收到 FileNotFoundError）
    if not lexical_target.exists() and not _is_link_or_junction(lexical_target):
        raise FileNotFoundError(f"文件不存在: {lexical_target.name}")

    try:
        resolved_target = lexical_target.resolve(strict=True)
    except FileNotFoundError:
        if _is_link_or_junction(lexical_target):
            diag = log_structured_error(
                problem="拦截悬空符号链接读取请求",
                cause=f"路径 '{lexical_target.name}' 为悬空符号链接或无效重解析点",
                fix="请移除无效符号链接，仅访问项目真实常规文件。",
            )
            raise PermissionError(diag)
        raise FileNotFoundError(f"文件不存在: {lexical_target.name}")

    # 3. 逐个匹配的根检查：不仅最终物理文件必须在同一 real_root 下，且从根到叶子的任一 symlink/junction 也不能越出该 real_root
    for lexical_root, real_root in matched_roots:
        if not _is_within_real_root(resolved_target, real_root):
            continue

        base_for_walk = lexical_root if _is_within_real_root(lexical_target, lexical_root) else real_root
        try:
            rel_parts = Path(os.path.normcase(str(lexical_target))).relative_to(
                Path(os.path.normcase(str(base_for_walk)))
            ).parts
        except ValueError:
            rel_parts = ()

        escaped = False
        cursor = base_for_walk
        for part in rel_parts:
            cursor = cursor / part
            if _is_link_or_junction(cursor):
                link_real = cursor.resolve(strict=True)
                if not _is_within_real_root(link_real, real_root):
                    escaped = True
                    break
        if not escaped:
            return resolved_target

    diag = log_structured_error(
        problem="拦截符号链接 / 目录联接 (Symlink/Junction) 越界逃逸读取",
        cause=f"目标 '{lexical_target.name}' 解析后的物理位置跳出了所属 Project.root_path 边界",
        fix="禁止通过符号链接或 Windows Junction 跨出当前项目根目录访问外部文件。",
        context={"file_name": lexical_target.name},
    )
    raise PermissionError(diag)


def validate_project_root_dir(raw_path: str) -> str:
    """
    校验用于注册 Project.root_path 的本地目录合法性：
    1. 必须是非空绝对路径且在磁盘上真实存在为目录；
    2. 禁止注册操作系统目录（Windows/System32）、用户 Home 根目录或临时下载桌面目录；
    3. 返回统一规范化后的绝对路径字符串（正斜杠、小写盘符）。
    """
    norm_input = normalize_project_path(raw_path)
    if not norm_input or not Path(norm_input).is_absolute():
        raise SecurityBoundaryError(
            problem="项目工作区路径不合法",
            cause=f"必须提供有效的本地绝对目录路径，收到: '{raw_path}'",
            fix="请填写完整的项目本地工作区绝对路径（例如 d:/Projects/MyRepo）。",
        )

    p_obj = Path(norm_input)
    if not p_obj.exists() or not p_obj.is_dir():
        raise SecurityBoundaryError(
            problem="项目工作区目录不存在",
            cause=f"本地磁盘未找到目录: '{norm_input}'",
            fix="请确认目录已在当前电脑创建且路径拼写无误。",
        )

    resolved = p_obj.resolve(strict=True)
    norm_resolved = normalize_project_path(str(resolved))
    parts = [seg for seg in norm_resolved.split("/") if seg]
    leaf = parts[-1].lower() if parts else ""

    if len(parts) <= 1 or leaf.startswith(".") or leaf in ADHOC_DIR_NAMES:
        raise SecurityBoundaryError(
            problem="禁止将系统根目录、隐藏目录或临时目录注册为研发项目",
            cause=f"路径 '{norm_resolved}' 命中高危/非工程目录拦截规则",
            fix="请选择具体的研发代码仓库目录作为 Project.root_path。",
        )

    try:
        home_norm = normalize_project_path(str(Path.home().resolve())).lower()
        if norm_resolved.lower() == home_norm:
            raise SecurityBoundaryError(
                problem="禁止将用户主目录 (Home) 直接注册为研发项目",
                cause="注册整个用户主目录会导致过宽的文件读取授权面",
                fix="请选择具体的子项目仓库目录作为 Project.root_path。",
            )
    except SecurityBoundaryError:
        raise
    except Exception:
        pass

    if any(seg.lower() in ("windows", "system32", "syswow64", "program files", "programdata") for seg in parts):
        raise SecurityBoundaryError(
            problem="禁止将操作系统目录注册为研发项目",
            cause=f"路径 '{norm_resolved}' 包含受保护系统目录段",
            fix="请选择独立的代码工程目录。",
        )

    return norm_resolved


def dedup_root_paths(paths: Iterable[str]) -> list[str]:
    """去重并保留有效路径字符串。"""
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        norm = normalize_project_path(p)
        if not norm:
            continue
        key = norm.lower()
        if key not in seen:
            seen.add(key)
            out.append(norm)
    return out


def validate_artifact_relative_path(project_root: str, relative_path: str) -> str:
    """
    校验 Artifact 的项目内相对路径安全性（Phase 4 补充安全要求）：
    1. 空字符串合法（表示纯摘要类产物，不关联具体磁盘文件）。
    2. 严禁包含控制字符、Windows 盘符（如 C:\\ / D:/）、UNC 路径（\\\\server\\share 或 //server）、绝对路径前导斜杠或 '..' 父目录逃逸。
    3. 结合 project_root 进行真实路径解析与逐级 symlink / Windows Junction 检查，禁止通过符号链接跳出所属 Project.root_path。
    4. 返回统一规范化后的正斜杠相对路径。
    """
    raw = (relative_path or "").strip()
    if not raw:
        return ""

    if any(ord(ch) < 32 for ch in raw):
        raise SecurityBoundaryError(
            problem="产物相对路径包含非法控制字符",
            cause="relative_path 中检测到 ASCII 控制字符",
            fix="请提供合法的项目内相对文件路径。",
            context={"mcp_error_code": "MCP_PATH_ESCAPE_BLOCKED"},
        )

    if raw.startswith(("\\\\", "//")) or re.match(r"^[a-zA-Z]:", raw):
        raise SecurityBoundaryError(
            problem="禁止在产物路径中使用 Windows 盘符或 UNC 网络路径",
            cause=f"收到非法路径: '{raw}'",
            fix="请仅填写相对于项目根目录 (Project.root_path) 的相对路径（例如 'docs/report.md'）。",
            context={"mcp_error_code": "MCP_PATH_ESCAPE_BLOCKED"},
        )

    if "\\" in raw:
        raise SecurityBoundaryError(
            problem="禁止在产物相对路径中使用反斜杠 ('\\')",
            cause=f"路径 '{raw}' 包含 Windows 反斜杠分隔符",
            fix="请统一使用正斜杠 '/' 格式的项目内相对路径（例如 'docs/report.md'）。",
            context={"mcp_error_code": "MCP_PATH_ESCAPE_BLOCKED"},
        )

    if raw.startswith("/"):
        raise SecurityBoundaryError(
            problem="禁止在产物路径中使用绝对路径",
            cause=f"路径 '{raw}' 以正斜杠开头",
            fix="请移除前导斜杠，仅使用项目内相对路径。",
            context={"mcp_error_code": "MCP_PATH_ESCAPE_BLOCKED"},
        )

    norm_rel = re.sub(r"/+", "/", raw).strip("/")
    parts = [seg for seg in norm_rel.split("/") if seg]
    if not parts or any(seg in (".", "..") or ":" in seg for seg in parts):
        raise SecurityBoundaryError(
            problem="禁止在产物路径中使用 '..' 目录穿越或非法路径段",
            cause=f"路径 '{raw}' 包含父目录跳转或非法字符",
            fix="产物路径必须严格位于当前项目根目录内部。",
            context={"mcp_error_code": "MCP_PATH_ESCAPE_BLOCKED"},
        )

    clean_rel = "/".join(parts)
    if not (project_root or "").strip():
        return clean_rel

    try:
        root_real = Path(os.path.abspath(project_root)).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SecurityBoundaryError(
            problem="项目根目录无效，无法校验产物路径",
            cause=f"无法解析项目根目录 '{project_root}': {exc}",
            fix="请确认项目 root_path 在本地磁盘真实存在。",
            context={"mcp_error_code": "MCP_PATH_ESCAPE_BLOCKED"},
        ) from exc

    cursor = root_real
    for seg in parts:
        cursor = cursor / seg
        if _is_link_or_junction(cursor):
            try:
                target_real = cursor.resolve(strict=True)
            except OSError as exc:
                raise SecurityBoundaryError(
                    problem="拦截悬空符号链接或无效重解析点产物路径",
                    cause=f"路径节点 '{cursor.name}' 是无效符号链接: {exc}",
                    fix="禁止登记指向悬空符号链接的产物路径。",
                    context={"mcp_error_code": "MCP_PATH_ESCAPE_BLOCKED"},
                ) from exc
            if not _is_within_real_root(target_real, root_real):
                raise SecurityBoundaryError(
                    problem="拦截符号链接 / Windows Junction 越界产物路径",
                    cause=f"路径 '{clean_rel}' 中的符号链接指向了项目根目录之外",
                    fix="禁止通过符号链接或目录联接登记项目外部文件。",
                    context={"mcp_error_code": "MCP_PATH_ESCAPE_BLOCKED"},
                )

    resolved_candidate = (root_real / clean_rel).resolve(strict=False)
    if not _is_within_real_root(resolved_candidate, root_real):
        raise SecurityBoundaryError(
            problem="产物路径越出项目根目录边界",
            cause=f"解析后的路径不位于项目根目录 '{root_real}' 内",
            fix="请确保 relative_path 严格指向当前项目内部。",
            context={"mcp_error_code": "MCP_PATH_ESCAPE_BLOCKED"},
        )

    return clean_rel


