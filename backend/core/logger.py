"""
统一中文日志与异常汇聚模块。

遵循工程规范：
1. 所有错误与异常必须统一汇聚写入 `logs/error.log`。
2. 强制采用 `[错误现象 Problem] + [根因 Cause] + [修复指令 Fix]` 三段式中文诊断结构。
"""

from __future__ import annotations

import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

# 强制 Windows 终端标准输出/错误流使用 UTF-8 编码，防止 GBK 控制台抛出 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# 项目根目录与日志目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
ERROR_LOG_FILE = LOG_DIR / "error.log"
APP_LOG_FILE = LOG_DIR / "app.log"


def setup_logger(name: str = "agentdeck") -> logging.Logger:
    """初始化并返回全局统一中文日志记录器。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 1. 控制台处理器（输出 INFO 及以上）
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 2. 常规运行日志处理器（按 5MB 轮转）
    app_file_handler = RotatingFileHandler(
        APP_LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    app_file_handler.setLevel(logging.INFO)
    app_file_handler.setFormatter(formatter)
    logger.addHandler(app_file_handler)

    # 3. 错误汇聚专用处理器（所有 ERROR/CRITICAL 强制写入 logs/error.log）
    error_file_handler = RotatingFileHandler(
        ERROR_LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    error_file_handler.setLevel(logging.ERROR)
    error_file_handler.setFormatter(formatter)
    logger.addHandler(error_file_handler)

    return logger


logger = setup_logger("agentdeck")


def format_diagnostic_message(
    problem: str,
    cause: str,
    fix: str,
    context: Optional[dict[str, Any]] = None,
) -> str:
    """构造三段式中文可操作诊断消息（Problem + Cause + Fix）。"""
    ctx_str = f" | 上下文: {context}" if context else ""
    return f"【错误现象】{problem} | 【根因分析】{cause} | 【修复建议】{fix}{ctx_str}"


def log_structured_error(
    problem: str,
    cause: str,
    fix: str,
    exc: Optional[BaseException] = None,
    context: Optional[dict[str, Any]] = None,
) -> str:
    """
    记录结构化三段式中文错误日志并自动汇聚写入 `logs/error.log`。
    返回格式化后的中文诊断文本供前端 Toast 或 API 响应使用。
    """
    diag_msg = format_diagnostic_message(problem, cause, fix, context)
    if exc is not None:
        tb_summary = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        logger.error(f"{diag_msg}\n--- 异常堆栈摘要 ---\n{tb_summary.strip()}")
    else:
        logger.error(diag_msg)
    return diag_msg
