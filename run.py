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
from backend.core.network import is_local_port_listening
from backend.core.tailscale_auth import render_terminal_qr_box


VERSION = "2.0.0"


def setup_windows_console_icon() -> None:
    """在 Windows 环境下将当前控制台窗口、Alt+Tab 与任务栏图标替换为项目 Logo。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from pathlib import Path

        # 1. 设置专属的 AppUserModelID，使 Windows 任务栏为其独立分组并加载自定义图标
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AgentDeck.Application.Launcher")
        except Exception:
            pass

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if not hwnd:
            return

        ico_path = str(Path(__file__).resolve().parent / "frontend" / "favicon.ico")
        if not os.path.exists(ico_path):
            return

        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010

        # 加载 16x16 (小图标，用于控制台窗口标题栏左上角)
        h_icon_sm = ctypes.windll.user32.LoadImageW(None, ico_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        # 加载 32x32 (大图标，用于 Alt+Tab 和任务栏任务预览)
        h_icon_big = ctypes.windll.user32.LoadImageW(None, ico_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)

        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1

        if h_icon_sm:
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, h_icon_sm)
        if h_icon_big:
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, h_icon_big)
    except Exception as exc:
        logger.debug("设置 Windows 控制台窗口图标失败: %s", exc)


def ensure_windows_shortcut(desktop: bool = False) -> str:
    """在 Windows 上生成/确保带项目 Logo 的启动快捷方式。"""
    if sys.platform != "win32":
        return ""
    try:
        import subprocess
        from pathlib import Path

        root = Path(__file__).resolve().parent
        start_cmd = root / "start.cmd"
        ico_file = root / "frontend" / "favicon.ico"

        if not start_cmd.exists() or not ico_file.exists():
            return ""

        if desktop:
            dest_dir = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
            if not dest_dir.exists():
                dest_dir = Path.home() / "Desktop"
            target_lnk = dest_dir / "AgentDeck.lnk"
        else:
            target_lnk = root / "AgentDeck.lnk"

        if not desktop and target_lnk.exists():
            return str(target_lnk)

        ps_script = (
            f"$ws = New-Object -ComObject WScript.Shell; "
            f"$s = $ws.CreateShortcut('{str(target_lnk)}'); "
            f"$s.TargetPath = '{str(start_cmd)}'; "
            f"$s.WorkingDirectory = '{str(root)}'; "
            f"$s.IconLocation = '{str(ico_file)},0'; "
            f"$s.Description = 'AgentDeck 一键启动中控台'; "
            f"$s.Save();"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return str(target_lnk)
    except Exception as exc:
        logger.debug("确保 Windows 启动快捷方式失败: %s", exc)
        return ""


def print_startup_banner(app, port: int) -> str:
    """打印 4 大 Agent 启动体检表与手机扫码配对面板，返回推荐绑定的主网卡 IP。"""
    ts = app.state.ts_status
    auth_mgr = app.state.auth_mgr
    orchestrator = app.state.orchestrator

    bind_host = ts.tailscale_ip if ts.is_online else "127.0.0.1"
    lan_ip = getattr(ts, "lan_ip", "") or ""
    zt_ip = getattr(ts, "zerotier_ip", "") or ""
    pair_url = f"http://{bind_host}:{port}/?pair_code={auth_mgr.bootstrap_pair_code}"
    lan_pair_url = f"http://{lan_ip}:{port}/?pair_code={auth_mgr.bootstrap_pair_code}" if lan_ip else ""
    zt_pair_url = f"http://{zt_ip}:{port}/?pair_code={auth_mgr.bootstrap_pair_code}" if zt_ip else ""
    local_url = f"http://127.0.0.1:{port}/"

    print("\n" + "═" * 72)
    print("  ⚡ AgentDeck v2.0 — 多 Agent 移动端统一中控台")
    print("═" * 72)
    ts_icon = "[✓]" if ts.is_online else "[!]"
    print(f"  {ts_icon} 网络隧道: {ts.diagnostic_msg}")
    if lan_ip:
        print(f"  [✓] Wi-Fi 网卡: {lan_ip} (可免代理直连)")
    if zt_ip:
        print(f"  [✓] ZeroTier 网卡: {zt_ip} (ZeroTier 虚拟隧道已就绪)")
    print(f"  [✓] 绑定策略: 严格限制于 {ts.bind_hosts} (已禁止 0.0.0.0 公网暴露)")

    for item in orchestrator.get_startup_health_report():
        icon = "[✓]" if item["ready"] else "[!]"
        print(f"  {icon} {item['agent_label']:<16}: {item['description']}")

    print("─" * 72)
    print(f"  💻 电脑本机控制台: {local_url} (打开可查看图形化高清配对二维码)")
    if ts.is_online and pair_url:
        print(f"  🌐 Tailscale 直连: {pair_url}")
    if zt_pair_url:
        print(f"  ⚡ ZeroTier 直连: {zt_pair_url}")
    if lan_pair_url:
        print(f"  🏠 Wi-Fi 直连: {lan_pair_url}")
    print("─" * 72)

    ts_pair = pair_url if ts.is_online else ""
    # 终端近场手机扫码：优先局域网 Wi-Fi 直连，杜绝手机未配置虚拟私网时扫码无法连接
    primary_pair_url = lan_pair_url or ts_pair or zt_pair_url or pair_url
    print("─" * 72)
    print("  【手机扫码直连 (已集成 Wi-Fi / Tailscale / ZeroTier 多链路自动漫游)】:")
    print(render_terminal_qr_box(primary_pair_url, title="AgentDeck 手机智能直连"))
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


def free_port_if_occupied(port: int) -> None:
    """启动前检测并清理占用指定端口的本项目残留旧进程；若占用进程非 AgentDeck，则报错退出防止误杀。"""
    # 0. 极速 Socket 预检（<1ms）：若端口未处于监听状态，直接极速返回，杜绝 psutil/powershell 全系统网络扫描开销
    if not is_local_port_listening(port, timeout=0.03):
        return

    current_pid = os.getpid()
    project_root_str = str(Path(__file__).resolve().parent).lower()
    occupied_pids: list[int] = []

    # 1. 扫描占用目标端口的监听进程 PID
    try:
        import psutil
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
                if conn.pid and conn.pid != current_pid and conn.pid not in occupied_pids:
                    occupied_pids.append(conn.pid)
    except Exception:
        pass

    if sys.platform == "win32" and not occupied_pids:
        try:
            ps_cmd = (
                f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | "
                f"Select-Object -ExpandProperty OwningProcess -Unique"
            )
            res = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in res.stdout.strip().splitlines():
                line = line.strip()
                if line.isdigit():
                    pid = int(line)
                    if pid > 0 and pid != current_pid and pid not in occupied_pids:
                        occupied_pids.append(pid)
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

            # 校验该进程是否确实属于当前 AgentDeck 项目
            is_agentdeck = (
                ("python" in proc_name or "uvicorn" in proc_name)
                and ("run.py" in cmdline or project_root_str in cmdline or "agentdeck" in cmdline)
            )

            if is_agentdeck:
                logger.warning("检测到本系统历史残留进程 (PID %d: %s) 占用端口 %d，正在安全回收...", pid, proc.name(), port)
                proc.kill()
                proc.wait(timeout=2)
                killed = True
            else:
                logger.error(
                    "【端口冲突】端口 %d 已被外部非 AgentDeck 进程 (PID %d: %s) 占用，禁止强制终止以防误杀其他服务！",
                    port, pid, proc.name()
                )
                print(
                    f"\n[错误] 端口 {port} 已被外部进程 (PID {pid}: {proc.name()}) 占用！\n"
                    f"命令行: {cmdline}\n"
                    f"AgentDeck 不会强制杀除非本项目的外部服务。请更换端口 (run.py --port <新端口>) 或手动关闭该程序后再启动。\n"
                )
                sys.exit(1)
        except psutil.NoSuchProcess:
            continue
        except Exception as exc:
            logger.error("检查/清理端口 %d 占用进程 PID %d 失败: %s", port, pid, exc)

    if killed:
        time.sleep(0.5)


def run_server_worker(port: int, local_only: bool) -> None:
    """启动 AgentDeck 主服务（单进程工作实例）。"""
    free_port_if_occupied(port)
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
    free_port_if_occupied(port)
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
            free_port_if_occupied(port)
            subproc = subprocess.Popen(cmd)
    except KeyboardInterrupt:
        logger.info("收到退出信号，正在关闭 AgentDeck...")
    finally:
        _terminate_proc(subproc)
        sys.exit(0)


def run_doctor(port: int = 8765) -> int:
    """运行 AgentDeck 环境全面诊断体检（--doctor），打印诊断报告并退出。"""
    import socket
    from backend.core.database import MetaDatabase
    from backend.core.tailscale_auth import detect_tailscale_status
    from backend.engines.process_pool import AgentOrchestrator

    db = MetaDatabase()
    ts = detect_tailscale_status()
    orchestrator = AgentOrchestrator(meta_db=db)

    print("\n" + "═" * 72)
    print("  🩺 AgentDeck 环境与依赖诊断体检报告 (AgentDeck Doctor)")
    print("═" * 72)
    print(f"  [✓] AgentDeck 版本: v{VERSION} (Python {sys.version.split()[0]})")

    ts_icon = "[✓]" if ts.is_online else "[!]"
    print(f"  {ts_icon} Tailscale 隧道: {ts.diagnostic_msg}")

    health_items = orchestrator.get_startup_health_report()
    all_ready = True
    for item in health_items:
        icon = "[✓]" if item["ready"] else "[!]"
        if not item["ready"]:
            all_ready = False
        print(f"  {icon} {item['agent_label']:<16}: {item['description']}")

    port_status = "空闲可用"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            port_status = f"当前已被占用 (启动时将自动清理或可使用 '--port {port + 1}')"
    print(f"  [i] 目标端口 ({port})  : {port_status}")

    print("─" * 72)
    if ts.is_online and all_ready:
        print("  🎉 完美！所有 Agent 与 Tailscale 网络均已就绪，可直接执行 'python run.py' 启动。")
    elif not ts.is_online:
        print("  💡 提示: Tailscale 未启动，本机调试完全正常；若需手机跨设备扫码，请运行 'tailscale up'。")
    else:
        print("  💡 提示: 部分 Agent 未安装或未找到历史会话，已就绪的 Agent 可正常使用。")
    print("═" * 72 + "\n")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agentdeck",
        description="⚡ AgentDeck — 基于 Tailscale 的多 Agent 移动端统一中控台",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s v{VERSION}",
        help="显示当前 AgentDeck 版本号并退出",
    )
    parser.add_argument(
        "--doctor",
        "--check",
        dest="doctor",
        action="store_true",
        help="运行环境体检与 Agent 挂载就绪状态检查后退出",
    )
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
    parser.add_argument(
        "--desktop-shortcut",
        dest="desktop_shortcut",
        action="store_true",
        help="在 Windows 桌面生成带项目 Logo 的 AgentDeck 快捷方式并退出",
    )
    args = parser.parse_args()

    setup_windows_console_icon()
    ensure_windows_shortcut()

    if args.desktop_shortcut:
        lnk = ensure_windows_shortcut(desktop=True)
        if lnk:
            print(f"[✓] 已在桌面创建 AgentDeck 启动快捷方式: {lnk}")
        else:
            print("[x] 创建桌面快捷方式失败")
        sys.exit(0)

    if args.doctor:
        sys.exit(run_doctor(args.port))

    if args.reload and not args.worker:
        run_with_reload(args.port, args.local_only)
    else:
        run_server_worker(args.port, args.local_only)


if __name__ == "__main__":
    main()
