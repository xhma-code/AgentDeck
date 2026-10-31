# -*- coding: utf-8 -*-
"""
AgentDeck MCP Stdio 标准输入输出服务端入口（python -m backend.mcp.stdio_server）。

严格遵循 Phase 4 审阅安全要求（阻塞项 3）：
1. 严禁支持命令行明文 `--token` 参数：若命令行参数出现 `--token` 或明文 `agmcp_` 令牌，
   立即向 stderr 输出安全拒绝原因并以退出码 2 终止，防止令牌暴露在操作系统进程列表或审计日志中。
2. 仅支持从环境变量 `AGENTDECK_MCP_TOKEN` 读取项目专属 MCP 令牌。
3. 严格隔离 stdout 与 stderr：
   - stdout 仅输出合法的 MCP JSON-RPC 2.0 协议消息（单行 UTF-8 JSON）；
   - 所有日志、异常诊断与调试输出一律重定向至 stderr 与 logs/error.log。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from backend.config import AppSettings
from backend.db.base import Base, sync_comments_to_connection
from backend.db.engine import create_session_factory, create_sqlite_engine
import backend.db.models  # noqa: F401
from backend.domain.exceptions import SecurityBoundaryError
from backend.mcp.server import VersionedMcpServerDispatcher


def _enforce_no_cli_token_args(argv: list[str]) -> None:
    """拦截任何试图通过命令行参数传递 MCP Token 的不安全行为。"""
    for arg in argv[1:]:
        lowered = arg.strip().lower()
        if (
            lowered == "--token"
            or lowered.startswith("--token=")
            or lowered == "-t"
            or "agmcp_" in lowered
        ):
            sys.stderr.write(
                "[SECURITY ERROR] 严禁通过命令行参数 (--token) 传递 MCP Token，以免泄露至系统进程列表！\n"
                "请改用环境变量传递: AGENTDECK_MCP_TOKEN=<PROJECT_TOKEN>\n"
            )
            sys.stderr.flush()
            raise SystemExit(2)


def _configure_stderr_only_logging() -> None:
    """将标准库根日志全部绑定至 stderr，保护 stdout 协议流纯净。"""
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    )
    root_logger.addHandler(stderr_handler)
    root_logger.setLevel(logging.WARNING)


def _build_settings_from_env() -> AppSettings:
    """从环境变量构造 AppSettings（支持 AGENTDECK_DATA_DIR 与 AGENTDECK_DB_FILENAME）。"""
    data_dir_env = (os.environ.get("AGENTDECK_DATA_DIR") or "").strip()
    db_file_env = (os.environ.get("AGENTDECK_DB_FILENAME") or "").strip()
    kwargs: dict[str, object] = {}
    if data_dir_env:
        kwargs["data_dir"] = Path(data_dir_env)
    if db_file_env:
        kwargs["db_filename"] = db_file_env
    return AppSettings(**kwargs)  # type: ignore[arg-type]


def run_stdio_loop(argv: Optional[list[str]] = None) -> int:
    """启动标准 MCP Stdio 消息循环。"""
    args = argv if argv is not None else sys.argv
    _enforce_no_cli_token_args(args)
    _configure_stderr_only_logging()

    raw_token = (os.environ.get("AGENTDECK_MCP_TOKEN") or "").strip()
    if not raw_token:
        sys.stderr.write(
            "[SECURITY ERROR] 未检测到环境变量 AGENTDECK_MCP_TOKEN，Stdio MCP Server 拒绝启动。\n"
        )
        sys.stderr.flush()
        return 2

    settings = _build_settings_from_env()
    engine = create_sqlite_engine(
        db_path=settings.db_path,
        busy_timeout_ms=settings.sqlite_busy_timeout_ms,
    )
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        sync_comments_to_connection(conn)
    session_factory = create_session_factory(engine)
    dispatcher = VersionedMcpServerDispatcher(session_factory)

    # 启动时立即验证 Token 有效性
    try:
        dispatcher.auth_guard.verify_principal(
            raw_token_or_header=raw_token,
            client_key="stdio",
        )
    except SecurityBoundaryError as exc:
        sys.stderr.write(f"[AUTH ERROR] {exc.problem}: {exc.cause}\n")
        sys.stderr.flush()
        engine.dispose()
        return 3

    # 强制使用 UTF-8 编码读写 stdio
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    sticky_version: Optional[str] = None
    try:
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "JSON 解析错误"},
                }
                sys.stdout.write(json.dumps(err_resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
                continue

            try:
                # 每次调用均重新校验 Token（支持运行期间若 Token 被吊销或过期则立即拦截）
                principal = dispatcher.auth_guard.verify_principal(
                    raw_token_or_header=raw_token,
                    client_key="stdio",
                )
            except SecurityBoundaryError as exc:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": payload.get("id") if isinstance(payload, dict) else None,
                    "error": {
                        "code": -32001,
                        "message": f"{exc.problem}: {exc.cause}",
                        "data": exc.context or {},
                    },
                }
                sys.stdout.write(json.dumps(err_resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
                continue

            resp, used_ver = dispatcher.dispatch_sync(
                principal=principal,
                payload=payload,
                sticky_version=sticky_version,
            )
            if isinstance(payload, dict) and payload.get("method") == "initialize":
                sticky_version = used_ver

            if resp is not None:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
    finally:
        engine.dispose()

    return 0


if __name__ == "__main__":
    raise SystemExit(run_stdio_loop())
