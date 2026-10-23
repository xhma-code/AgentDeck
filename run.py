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
import sys
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


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentDeck — 多 Agent 移动端统一中控台")
    parser.add_argument("--port", type=int, default=8765, help="监听端口 (默认: 8765)")
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="仅绑定本机 127.0.0.1 调试",
    )
    args = parser.parse_args()

    try:
        app = create_app()
        print_startup_banner(app, args.port)
        hosts = ["127.0.0.1"] if args.local_only else app.state.ts_status.bind_hosts
        asyncio.run(serve_multi_host(app, hosts, args.port))
    except KeyboardInterrupt:
        logger.info("收到退出信号，AgentDeck 已安全停止。")
        sys.exit(0)
    except Exception as exc:
        log_structured_error(
            problem="AgentDeck 启动失败",
            cause=str(exc),
            fix=f"请检查端口 {args.port} 是否被占用，可使用 '--port 8766' 更换端口重试。",
            exc=exc,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
