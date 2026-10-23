"""
AgentDeck 一键启动入口 (`python run.py`)。

遵循 `/autoplan` 安全与开发者体验 (DX) 规范：
1. 启动时自动运行 4 大 Agent 状态体检表（Startup Health Check Table）。
2. 自动检测本机 Tailscale 网卡 IP (`100.x.y.z`)，仅在 Tailscale IP 与 `127.0.0.1` 上监听，严禁绑定 `0.0.0.0`。
3. 终端直接打印含一次性配对码的手机扫码直连地址与二维码。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import uvicorn

from backend.app import create_app
from backend.core.logger import log_structured_error, logger
from backend.core.tailscale_auth import render_terminal_qr_box


def print_startup_banner(app, port: int) -> str:
    """打印 4 大 Agent 启动体检表与手机扫码配对面板，返回推荐绑定的主网卡 IP。"""
    ts = app.state.ts_status
    auth_mgr = app.state.auth_mgr
    orchestrator = app.state.orchestrator

    bind_host = ts.tailscale_ip if ts.is_online else "127.0.0.1"
    pair_url = f"http://{bind_host}:{port}/?pair_code={auth_mgr.bootstrap_pair_code}"
    local_url = f"http://127.0.0.1:{port}/"

    print("\n" + "═" * 72)
    print("  ⚡ AgentDeck v2.0 — 多 Agent 移动端统一中控台 (Tailscale 安全网关)")
    print("═" * 72)
    ts_icon = "[✓]" if ts.is_online else "[!]"
    print(f"  {ts_icon} 网络隧道: {ts.diagnostic_msg}")
    print(f"  [✓] 绑定策略: 严格限制于 {ts.bind_hosts} (已禁止 0.0.0.0 公网/局域网暴露)")

    for item in orchestrator.get_startup_health_report():
        icon = "[✓]" if item["ready"] else "[!]"
        print(f"  {icon} {item['agent_label']:<16}: {item['description']}")

    print("─" * 72)
    print(f"  💻 电脑本机控制台: {local_url} (打开可查看图形化高清配对二维码)")
    print("─" * 72)
    print(render_terminal_qr_box(pair_url))
    print("═" * 72 + "\n")
    return bind_host


async def serve_multi_host(app, hosts: list[str], port: int) -> None:
    """同时在 Tailscale IP (100.x.y.z) 与本机环回 (127.0.0.1) 上启动 Uvicorn 服务。"""
    unique_hosts: list[str] = []
    for h in hosts:
        if h not in unique_hosts:
            unique_hosts.append(h)

    servers = []
    for h in unique_hosts:
        cfg = uvicorn.Config(
            app,
            host=h,
            port=port,
            log_level="info",
            access_log=False,
        )
        servers.append(uvicorn.Server(cfg))

    await asyncio.gather(*(s.serve() for s in servers))


def run_server_worker(port: int, local_only: bool) -> None:
    """启动 AgentDeck 主服务（单进程工作实例）。"""
    try:
        app = create_app()
        print_startup_banner(app, port)
        hosts = ["127.0.0.1"] if local_only else app.state.ts_status.bind_hosts
        asyncio.run(serve_multi_host(app, hosts, port))
    except KeyboardInterrupt:
        logger.info("收到退出信号，AgentDeck 已安全停止。")
        sys.exit(0)
    except Exception as exc:
        log_structured_error(
            problem="AgentDeck 启动失败",
            cause=str(exc),
            fix=f"请检查端口 {port} 是否被占用，可使用 '--port {port + 1}' 更换端口重试。",
            exc=exc,
        )
        sys.exit(1)


def run_with_reload(port: int, local_only: bool) -> None:
    """热重载 Supervisor：监听 backend/ 变动，保存时自动终止并重启服务。"""
    try:
        from watchfiles import DefaultFilter, watch
    except ImportError:
        logger.warning("未检测到 watchfiles 模块，将以普通单实例模式启动。")
        run_server_worker(port, local_only)
        return

    script_path = Path(__file__).resolve()
    backend_path = script_path.parent / "backend"

    cmd = [
        sys.executable,
        str(script_path),
        "--port",
        str(port),
        "--worker",
    ]
    if local_only:
        cmd.append("--local-only")

    logger.info("⚡ AgentDeck 热重载模式已启用，正在监听 backend/ 源码变动...")

    def _terminate_proc(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

    subproc = subprocess.Popen(cmd)

    watch_filter = DefaultFilter(
        ignore_dirs={"__pycache__", ".git", "logs", ".pytest_cache", ".gemini", ".venv"},
        ignore_entity_patterns=[r"\.py[cod]$", r"\.log$", r"\.db$", r"\.sqlite3?$"],
    )

    try:
        for changes in watch(backend_path, script_path, watch_filter=watch_filter, debounce=1000):
            change_files = {Path(p).name for _, p in changes}
            print(f"\n[⚡] 检测到代码变动 ({', '.join(change_files)})，正在热重启 AgentDeck...")
            _terminate_proc(subproc)
            time.sleep(0.3)
            subproc = subprocess.Popen(cmd)
    except KeyboardInterrupt:
        logger.info("收到退出信号，正在关闭 AgentDeck...")
    finally:
        _terminate_proc(subproc)
        sys.exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentDeck — 多 Agent 移动端统一中控台")
    parser.add_argument("--port", type=int, default=8765, help="监听端口 (默认: 8765)")
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="仅绑定本机 127.0.0.1 调试",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="开启自动热重载模式 (监听 backend/ 变动)",
    )
    parser.add_argument(
        "--worker",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    if args.reload and not args.worker:
        run_with_reload(args.port, args.local_only)
    else:
        run_server_worker(args.port, args.local_only)


if __name__ == "__main__":
    main()
