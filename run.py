# -*- coding: utf-8 -*-
"""
AgentDeck 一键启动入口 (`python run.py`) — Clean-Slate v1.3 架构。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import uvicorn

from backend.app import create_app
from backend.config import get_settings
from backend.core.logger import log_structured_error, logger
from backend.core.network import is_local_port_listening
from backend.core.qr_box import render_terminal_qr_box
from backend.db.engine import create_sqlite_engine, inspect_sqlite_pragmas
from backend.db.migration import apply_database_migrations, inspect_migration_status
from backend.db.models import ALL_CANONICAL_TABLE_NAMES

VERSION = "1.3.0"


def setup_windows_console_icon() -> None:
    """在 Windows 环境下将当前控制台窗口、Alt+Tab 与任务栏图标替换为项目 Logo。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AgentDeck.Application.Launcher")
        except Exception:
            pass

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if not hwnd:
            return

        root = Path(__file__).resolve().parent
        for rel in ("frontend/public/favicon.ico", "frontend/favicon.ico"):
            candidate = root / rel
            if candidate.exists():
                ico_path = str(candidate)
                break
        else:
            return

        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        h_icon_sm = ctypes.windll.user32.LoadImageW(None, ico_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        h_icon_big = ctypes.windll.user32.LoadImageW(None, ico_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)

        WM_SETICON = 0x0080
        if h_icon_sm:
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 0, h_icon_sm)
        if h_icon_big:
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 1, h_icon_big)
    except Exception as exc:
        logger.debug("设置 Windows 控制台窗口图标失败: %s", exc)


def print_startup_banner(app, port: int) -> str:
    """打印 Runtime Provider 基线与手机扫码配对面板，返回推荐绑定的主网卡 IP。"""
    ts = app.state.ts_status
    auth_mgr = app.state.auth_mgr
    providers = app.state.runtime_registry_service.list_providers()

    bind_host = ts.tailscale_ip if ts.is_online else "127.0.0.1"
    lan_ip = getattr(ts, "lan_ip", "") or ""
    zt_ip = getattr(ts, "zerotier_ip", "") or ""
    pair_url = f"http://{bind_host}:{port}/?pair_code={auth_mgr.bootstrap_pair_code}"
    lan_pair_url = f"http://{lan_ip}:{port}/?pair_code={auth_mgr.bootstrap_pair_code}" if lan_ip else ""
    zt_pair_url = f"http://{zt_ip}:{port}/?pair_code={auth_mgr.bootstrap_pair_code}" if zt_ip else ""
    local_url = f"http://127.0.0.1:{port}/"

    print("\n" + "═" * 72)
    print(f"  ⚡ AgentDeck v{VERSION} — 多项目 AI 研发异步编排驾驶舱 (Clean-Slate)")
    print("═" * 72)
    ts_icon = "[✓]" if ts.is_online else "[!]"
    print(f"  {ts_icon} 网络隧道: {ts.diagnostic_msg}")
    if lan_ip:
        print(f"  [✓] Wi-Fi 网卡: {lan_ip}")
    if zt_ip:
        print(f"  [✓] ZeroTier 网卡: {zt_ip}")
    print(f"  [✓] 绑定策略: 严格限制于 {ts.bind_hosts} (已禁止 0.0.0.0 公网暴露)")
    print(f"  [✓] 领域模型: {len(ALL_CANONICAL_TABLE_NAMES)} 张规范表就绪 | RuntimeProviders: {len(providers)} 个")
    print("─" * 72)
    print(f"  💻 电脑本机控制台: {local_url}")
    if ts.is_online and pair_url:
        print(f"  🌐 Tailscale 直连: {pair_url}")
    if zt_pair_url:
        print(f"  ⚡ ZeroTier 直连: {zt_pair_url}")
    if lan_pair_url:
        print(f"  🏠 Wi-Fi 直连: {lan_pair_url}")
    print("─" * 72)

    ts_pair = pair_url if ts.is_online else ""
    primary_pair_url = ts_pair or zt_pair_url or lan_pair_url or pair_url
    print("  【手机扫码直连 (60 秒一次性配对码)】:")
    print(render_terminal_qr_box(primary_pair_url, title="AgentDeck 手机智能直连"))
    print("═" * 72 + "\n")
    return bind_host


async def serve_multi_host(app, hosts: list[str], port: int) -> None:
    """在指定受控网卡列表上启动 Uvicorn 服务。"""
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
        servers.append((h, uvicorn.Server(cfg)))

    failed_hosts: list[str] = []

    async def _serve_one(h: str, server: uvicorn.Server) -> None:
        try:
            await server.serve()
        except Exception as exc:
            failed_hosts.append(h)
            logger.error("【bind 失败】地址 %s:%d 服务启动异常并已隔离: %s", h, port, exc)

    await asyncio.gather(*(_serve_one(h, s) for h, s in servers))
    if len(failed_hosts) >= len(servers):
        raise RuntimeError(f"全部 bind 地址均启动失败: {failed_hosts}")


def free_port_if_occupied(port: int) -> None:
    """启动前检测并清理占用指定端口的本项目残留旧进程。"""
    if not is_local_port_listening(port, timeout=0.03):
        return

    current_pid = os.getpid()
    project_root_str = str(Path(__file__).resolve().parent).lower()
    occupied_pids: list[int] = []

    try:
        import psutil
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
                if conn.pid and conn.pid != current_pid and conn.pid not in occupied_pids:
                    occupied_pids.append(conn.pid)
    except Exception:
        pass

    if not occupied_pids:
        return

    import psutil
    killed = False
    for pid in occupied_pids:
        try:
            proc = psutil.Process(pid)
            cmdline = " ".join(proc.cmdline() or []).lower()
            proc_name = (proc.name() or "").lower()
            is_agentdeck = (
                ("python" in proc_name or "uvicorn" in proc_name)
                and ("run.py" in cmdline or project_root_str in cmdline or "agentdeck" in cmdline)
            )
            if is_agentdeck:
                logger.warning("检测到本系统历史残留进程 (PID %d) 占用端口 %d，正在安全回收...", pid, port)
                proc.kill()
                proc.wait(timeout=2)
                killed = True
        except Exception:
            continue

    if killed:
        time.sleep(0.4)


def run_doctor(port: int = 8765) -> int:
    """运行 AgentDeck Clean-Slate 环境诊断体检。"""
    settings = get_settings()
    app = create_app(settings=settings, auto_init_schema=False)
    pragmas = inspect_sqlite_pragmas(app.state.engine)
    migration_status = inspect_migration_status(app.state.engine, settings.db_path)
    providers = app.state.runtime_registry_service.list_providers()
    machines = app.state.runtime_registry_service.list_machines()
    print("\n" + "═" * 72)
    print(f"  🩺 AgentDeck v{VERSION} 环境诊断体检报告")
    print("═" * 72)
    print(f"  [✓] 数据库路径: {settings.db_path}")
    print(f"  [✓] SQLite PRAGMA: {pragmas}")
    mig_mark = "[✓]" if migration_status["is_synced"] else "[!]"
    print(f"  {mig_mark} Alembic 迁移版本: 当前={migration_status['current_revision']} | 目标Head={migration_status['head_revision']}")
    print(f"  [✓] 规范表数量: {len(ALL_CANONICAL_TABLE_NAMES)} 张")
    print(f"  [✓] RuntimeProviders: {[p.provider_key for p in providers]}")
    print(f"  [✓] Machines: {[m.display_name for m in machines]}")
    print(f"  [✓] VAPID 公钥长度: {len(app.state.push_service.get_public_key())}")
    print(f"  [i] 监听端口: {port}")
    print("═" * 72 + "\n")
    app.state.engine.dispose()
    return 0


def run_server_worker(port: int, local_only: bool) -> None:
    """启动 AgentDeck 主服务。"""
    free_port_if_occupied(port)
    settings = get_settings()
    # 强制以 Alembic 为唯一正式 Schema 演进来源
    engine = create_sqlite_engine(settings.db_path)
    try:
        mig_status = inspect_migration_status(engine, settings.db_path)
        if not mig_status["is_synced"]:
            logger.info(
                "检测到数据库尚未对齐 Alembic 最新迁移版本 (当前: %s, 目标: %s)，正在执行升级迁移...",
                mig_status["current_revision"],
                mig_status["head_revision"],
            )
            apply_database_migrations(settings.db_path)
    finally:
        engine.dispose()

    try:
        app = create_app(settings=settings, auto_init_schema=False)
        app.state.settings.port = port
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


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agentdeck",
        description="⚡ AgentDeck — 多项目 AI 研发异步编排驾驶舱",
    )
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s v{VERSION}")
    parser.add_argument("--doctor", "--check", dest="doctor", action="store_true", help="运行环境诊断体检")
    parser.add_argument("--port", type=int, default=8765, help="监听端口 (默认: 8765)")
    parser.add_argument("--local-only", action="store_true", help="仅绑定本机 127.0.0.1")
    parser.add_argument("--reload", action="store_true", help="开启自动热重载模式")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    setup_windows_console_icon()
    if args.doctor:
        sys.exit(run_doctor(args.port))
    run_server_worker(args.port, args.local_only)


if __name__ == "__main__":
    main()
