"""FlowKit CLI / TUI Launcher & Control Panel.

Runs with Python 3.10+ in D:\\python_envs\\flowkit\\python.exe.
Provides a clean, single-service terminal UI for starting, monitoring,
and maintaining FlowKit services on Port 8100.
"""
import os
import sys
import time
import subprocess
import webbrowser
import urllib.request
import urllib.error
import json
import socket
import unicodedata
from pathlib import Path

# Ensure colorama is initialized for Windows console ANSI support
try:
    import colorama
    colorama.init(autoreset=True)
    from colorama import Fore, Style, Back
except ImportError:
    class DummyColor:
        CYAN = GREEN = YELLOW = RED = BLUE = MAGENTA = WHITE = RESET = ""
        BOLD = DIM = ""
    Fore = Style = Back = DummyColor()

# Key constants
KEY_UP = "UP"
KEY_DOWN = "DOWN"
KEY_ENTER = "ENTER"
KEY_QUIT = "QUIT"
KEY_UNKNOWN = "UNKNOWN"

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DASHBOARD_DIR = PROJECT_ROOT / "dashboard"
PID_FILE = PROJECT_ROOT / ".flowkit.pid"
PORT = 8100

HEALTH_URL = f"http://127.0.0.1:{PORT}/health"
DASHBOARD_URL = f"http://127.0.0.1:{PORT}/dashboard"

# Active process reference if started by this script instance
_server_process = None

# Health cache to eliminate network latency during UI navigation
_last_health_check = 0.0
_cached_health = (False, None)


def get_key_input():
    """Read a single keypress from standard input (Windows msvcrt with POSIX fallback)."""
    if sys.platform == "win32":
        import msvcrt
        ch = msvcrt.getch()
        if ch in (b"\x00", b"\xe0"):
            ch2 = msvcrt.getch()
            if ch2 == b"H":
                return KEY_UP
            elif ch2 == b"P":
                return KEY_DOWN
            return KEY_UNKNOWN
        elif ch in (b"\r", b"\n"):
            return KEY_ENTER
        elif ch in (b"q", b"Q", b"\x1b"):  # ESC or q
            return KEY_QUIT
        else:
            try:
                return ch.decode("utf-8", errors="ignore")
            except Exception:
                return KEY_UNKNOWN
    else:
        import tty
        import termios
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                ch2 = sys.stdin.read(2)
                if ch2 == "[A":
                    return KEY_UP
                elif ch2 == "[B":
                    return KEY_DOWN
                return KEY_QUIT
            elif ch in ("\r", "\n"):
                return KEY_ENTER
            elif ch in ("q", "Q"):
                return KEY_QUIT
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def get_pids_by_port(port=8100):
    """Find process IDs listening on the given port."""
    pids = set()
    try:
        if sys.platform == "win32":
            output = subprocess.check_output(
                f"netstat -ano | findstr :{port}",
                shell=True,
                text=True,
                stderr=subprocess.DEVNULL
            )
            for line in output.strip().splitlines():
                parts = line.split()
                if len(parts) >= 5 and "LISTENING" in parts:
                    try:
                        pid = int(parts[-1])
                        if pid > 0:
                            pids.add(pid)
                    except ValueError:
                        pass
        else:
            output = subprocess.check_output(
                f"lsof -t -i:{port}",
                shell=True,
                text=True,
                stderr=subprocess.DEVNULL
            )
            for line in output.strip().splitlines():
                try:
                    pid = int(line.strip())
                    if pid > 0:
                        pids.add(pid)
                except ValueError:
                    pass
    except Exception:
        pass
    return list(pids)


def get_server_pid():
    """Get server PID from PID file or by scanning port 8100."""
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            if sys.platform == "win32":
                import ctypes
                kernel32 = ctypes.windll.kernel32
                SYNCHRONIZE = 0x0010
                h_proc = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
                if h_proc:
                    kernel32.CloseHandle(h_proc)
                    return pid
            else:
                os.kill(pid, 0)
                return pid
        except Exception:
            pass

    port_pids = get_pids_by_port(PORT)
    if port_pids:
        return port_pids[0]
    return None


def is_server_healthy(force=False):
    """Fast, non-blocking check if server health endpoint responds on 8100."""
    global _last_health_check, _cached_health
    now = time.time()
    if not force and (now - _last_health_check < 1.5):
        return _cached_health

    _last_health_check = now

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.03)
            if s.connect_ex(("127.0.0.1", PORT)) != 0:
                _cached_health = (False, None)
                return _cached_health

        req = urllib.request.Request(HEALTH_URL, headers={"User-Agent": "FlowKit-Launcher"})
        with urllib.request.urlopen(req, timeout=0.3) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode())
                _cached_health = (True, data)
                return _cached_health
    except Exception:
        pass

    _cached_health = (False, None)
    return _cached_health


def action_start_server():
    global _server_process
    healthy, _ = is_server_healthy(force=True)
    pid = get_server_pid()

    if healthy or pid:
        print(f"\n{Fore.YELLOW}FlowKit 服务已经在运行中 (PID: {pid or '未知'})!{Style.RESET_ALL}")
        time.sleep(1.2)
        return

    print(f"\n{Fore.CYAN}正在启动 FlowKit 服务 (python -m agent.main)...{Style.RESET_ALL}")
    python_exe = sys.executable

    log_path = PROJECT_ROOT / "flowkit_server.log"
    log_file = open(log_path, "a", encoding="utf-8")

    _server_process = subprocess.Popen(
        [python_exe, "-m", "agent.main"],
        cwd=str(PROJECT_ROOT),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    )

    PID_FILE.write_text(str(_server_process.pid))
    print(f"{Fore.GREEN}服务进程已启动 (PID: {_server_process.pid})，日志记录至 flowkit_server.log{Style.RESET_ALL}")

    print("等待服务就绪...")
    for _ in range(10):
        time.sleep(1)
        healthy, _ = is_server_healthy(force=True)
        if healthy:
            print(f"{Fore.GREEN}✓ FlowKit 服务已就绪 (http://127.0.0.1:{PORT})!{Style.RESET_ALL}")
            time.sleep(1.2)
            return

    print(f"{Fore.YELLOW}服务已启动，请稍后通过状态检测查看。{Style.RESET_ALL}")
    time.sleep(1.5)


def action_stop_server():
    global _server_process
    pid = get_server_pid()
    print(f"\n{Fore.YELLOW}正在停止 FlowKit 服务 (8100)...{Style.RESET_ALL}")

    if _server_process and _server_process.poll() is None:
        _server_process.terminate()
        try:
            _server_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _server_process.kill()
        _server_process = None

    pids_to_kill = set()
    if pid:
        pids_to_kill.add(pid)
    for p in get_pids_by_port(PORT):
        pids_to_kill.add(p)

    for p in pids_to_kill:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p)], capture_output=True)
                ps_child = f"Get-CimInstance Win32_Process -Filter 'ParentProcessId = {p}' -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"
                subprocess.run(["powershell", "-NoProfile", "-Command", ps_child], capture_output=True)
            else:
                os.kill(p, 9)
        except Exception:
            pass

    if get_pids_by_port(PORT):
        try:
            if sys.platform == "win32":
                ps_sweep = "Get-CimInstance Win32_Process -Filter \"CommandLine like '%agent.main%'\" -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
                subprocess.run(["powershell", "-NoProfile", "-Command", ps_sweep], capture_output=True)
        except Exception:
            pass

    if PID_FILE.exists():
        try:
            PID_FILE.unlink()
        except Exception:
            pass

    time.sleep(0.5)
    is_server_healthy(force=True)
    remaining = get_pids_by_port(PORT)
    if remaining:
        print(f"{Fore.RED}⚠ 警告: 8100 端口仍有占用: {remaining}{Style.RESET_ALL}")
    else:
        print(f"{Fore.GREEN}✓ 所有 FlowKit 服务已停止并释放 8100 端口{Style.RESET_ALL}")
    time.sleep(1)


def action_restart_server():
    action_stop_server()
    time.sleep(1)
    action_start_server()


def _print_log_line(line: str):
    """Format and colorize a single log line."""
    if not line:
        return
    if " [ERROR] " in line or " ERROR " in line or " Traceback " in line or "Error: " in line:
        print(f"{Fore.RED}{line}{Style.RESET_ALL}")
    elif " [WARNING] " in line or " WARNING " in line:
        print(f"{Fore.YELLOW}{line}{Style.RESET_ALL}")
    elif " [INFO] " in line or " INFO: " in line:
        if "Extension" in line or "Flow key" in line or "WebSocket" in line:
            print(f"{Fore.GREEN}{line}{Style.RESET_ALL}")
        elif "HTTP" in line or "GET " in line or "POST " in line:
            print(f"{Fore.WHITE}{line}{Style.RESET_ALL}")
        else:
            print(f"{Style.DIM}{line[:24]}{Style.RESET_ALL} {line[24:]}" if len(line) > 24 else line)
    else:
        print(line)


def action_tail_logs():
    """Live tail -f log follower. Follows flowkit_server.log until user presses Q or ESC."""
    log_path = PROJECT_ROOT / "flowkit_server.log"
    sys.stdout.write("\033[2J\033[H\033[?25h")
    sys.stdout.flush()

    print(f"{Fore.CYAN}========================================================================{Style.RESET_ALL}")
    print(f"{Fore.CYAN}🚀 FlowKit 实时日志追踪 (tail -f){Style.RESET_ALL}")
    print(f"📁 日志路径: {log_path.resolve()}")
    print(f"⌨️  操作提示: 按 {Fore.YELLOW}[Q]{Style.RESET_ALL} 或 {Fore.YELLOW}[ESC]{Style.RESET_ALL} 退出并返回主菜单 | 按 {Fore.YELLOW}[C]{Style.RESET_ALL} 清屏")
    print(f"{Fore.CYAN}========================================================================{Style.RESET_ALL}\n")

    if not log_path.exists():
        log_path.touch()

    # Print recent history (up to 30 lines)
    try:
        content = log_path.read_text(encoding="utf-8", errors="ignore")
        lines = content.splitlines()
        for l in (lines[-30:] if len(lines) > 30 else lines):
            _print_log_line(l)
    except Exception as e:
        print(f"{Fore.RED}读取历史日志失败: {e}{Style.RESET_ALL}")

    # Enter non-blocking tail loop
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(0, os.SEEK_END)
            last_pos = f.tell()

            while True:
                # 1. Non-blocking key check
                if sys.platform == "win32":
                    import msvcrt
                    if msvcrt.kbhit():
                        ch = msvcrt.getch()
                        if ch in (b"\x00", b"\xe0"):
                            msvcrt.getch()  # consume scan code
                        elif ch in (b"q", b"Q", b"\x1b", b"\x03"):  # q, Q, ESC, Ctrl+C
                            break
                        elif ch in (b"c", b"C"):
                            sys.stdout.write("\033[2J\033[H")
                            print(f"{Fore.CYAN}--- 已清屏 (实时日志追踪中，按 [Q] 返回菜单) ---{Style.RESET_ALL}\n")
                            sys.stdout.flush()
                else:
                    import select
                    r, _, _ = select.select([sys.stdin], [], [], 0)
                    if r:
                        ch = sys.stdin.read(1)
                        if ch in ("q", "Q", "\x1b", "\x03"):
                            break
                        elif ch in ("c", "C"):
                            sys.stdout.write("\033[2J\033[H")
                            sys.stdout.flush()

                # 2. Read new log lines
                line = f.readline()
                if line:
                    _print_log_line(line.rstrip("\r\n"))
                    last_pos = f.tell()
                else:
                    # Check for file truncation/recreation
                    try:
                        cur_size = log_path.stat().st_size
                        if cur_size < last_pos:
                            f.seek(0, os.SEEK_SET)
                            last_pos = 0
                    except Exception:
                        pass
                    time.sleep(0.08)

    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"\n{Fore.RED}日志跟踪发生异常: {e}{Style.RESET_ALL}")
        time.sleep(1)

    print(f"\n{Fore.CYAN}正在返回 FlowKit 主菜单...{Style.RESET_ALL}")
    time.sleep(0.3)


def action_diagnostics():
    """View detailed health diagnostics and system stats."""
    healthy, data = is_server_healthy(force=True)
    pid = get_server_pid()

    print(f"\n{Fore.CYAN}===================================================={Style.RESET_ALL}")
    print(f"{Fore.CYAN}            FlowKit 服务深度健康与统计诊断             {Style.RESET_ALL}")
    print(f"{Fore.CYAN}===================================================={Style.RESET_ALL}")

    print(f"\n1. 基础进程与网络状态:")
    print(f"   - 8100 端口服务 PID: {Fore.GREEN + str(pid) if pid else Fore.RED + '未运行'}{Style.RESET_ALL}")
    pids_8100 = get_pids_by_port(PORT)
    print(f"   - 8100 监听进程列表: {pids_8100 if pids_8100 else '无'}")
    pids_9223 = get_pids_by_port(9223)
    print(f"   - 9223 (WebSocket) 监听: {Fore.GREEN + str(pids_9223) if pids_9223 else Fore.YELLOW + '未监听'}{Style.RESET_ALL}")

    print(f"\n2. 健康检查 (/health) 响应:")
    if healthy and data:
        print(f"   - HTTP 状态码: {Fore.GREEN}200 OK{Style.RESET_ALL}")
        print(f"   - 服务版本: {data.get('version', '未知')}")
        ext_conn = data.get("extension_connected", False)
        ext_color = Fore.GREEN if ext_conn else Fore.RED
        print(f"   - Chrome 扩展连接: {ext_color}{'已连接 (OK)' if ext_conn else '未连接 (请确认扩展是否开启)'}{Style.RESET_ALL}")
        if "ws" in data:
            ws_info = data["ws"]
            print(f"   - WebSocket 活跃连接: {ws_info.get('active_connections', 0)} (已认证: {ws_info.get('authenticated_connections', 0)})")
            print(f"   - WebSocket 运行时间: {ws_info.get('uptime_s', 0)} 秒")
    else:
        print(f"   - HTTP 状态: {Fore.RED}无法连接至 http://127.0.0.1:{PORT}/health{Style.RESET_ALL}")

    print(f"\n3. 本地数据库与缓存状态:")
    db_path = PROJECT_ROOT / "flow_agent.db"
    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024 * 1024)
        print(f"   - 数据库文件 (flow_agent.db): {Fore.GREEN}存在 ({size_mb:.2f} MB){Style.RESET_ALL}")
    else:
        print(f"   - 数据库文件: {Fore.YELLOW}未创建{Style.RESET_ALL}")

    cache_dir = PROJECT_ROOT / "output" / "_cache"
    if cache_dir.exists():
        cache_files = list(cache_dir.glob("*.*"))
        total_size_mb = sum(f.stat().st_size for f in cache_files) / (1024 * 1024)
        print(f"   - 媒体本地缓存目录: {len(cache_files)} 个文件 ({total_size_mb:.2f} MB)")
    else:
        print(f"   - 媒体本地缓存目录: 未创建")

    print(f"\n{Fore.CYAN}----------------------------------------------------{Style.RESET_ALL}")
    print("按任意键返回主菜单...")
    get_key_input()


def action_open_dashboard():
    healthy, _ = is_server_healthy(force=True)
    if not healthy:
        print(f"\n{Fore.YELLOW}提示: 服务尚未启动，尝试打开 {DASHBOARD_URL}...{Style.RESET_ALL}")
    else:
        print(f"\n{Fore.GREEN}正在打开 Web 控制台: {DASHBOARD_URL}{Style.RESET_ALL}")
    webbrowser.open(DASHBOARD_URL)
    time.sleep(1)


def action_build_frontend():
    print(f"\n{Fore.CYAN}--- 重新编译前端静态资源 (npm run build) ---{Style.RESET_ALL}")
    if not DASHBOARD_DIR.exists():
        print(f"{Fore.RED}未找到前端目录: {DASHBOARD_DIR}{Style.RESET_ALL}")
    else:
        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        try:
            res = subprocess.run([npm_cmd, "run", "build"], cwd=str(DASHBOARD_DIR))
            if res.returncode == 0:
                print(f"{Fore.GREEN}✓ 前端构建成功，静态文件已更新至 dashboard/dist{Style.RESET_ALL}")
            else:
                print(f"{Fore.RED}前端构建失败，请检查上方报错信息。{Style.RESET_ALL}")
        except Exception as e:
            print(f"{Fore.RED}执行构建命令失败: {e}{Style.RESET_ALL}")

    print("\n按任意键返回主菜单...")
    get_key_input()


def action_run_tests():
    print(f"\n{Fore.CYAN}--- 运行 Pytest 单元测试 ---{Style.RESET_ALL}")
    python_exe = sys.executable
    try:
        subprocess.run([python_exe, "-m", "pytest", "tests/"], cwd=str(PROJECT_ROOT))
    except Exception as e:
        print(f"{Fore.RED}测试执行失败: {e}{Style.RESET_ALL}")
    print("\n按任意键返回主菜单...")
    get_key_input()


COMMANDS = [
    {"key": "1", "label": "启动 FlowKit 服务 (后端 + 控制台)", "action": action_start_server},
    {"key": "2", "label": "重启 FlowKit 服务", "action": action_restart_server},
    {"key": "3", "label": "停止 FlowKit 服务 (强力释放端口)", "action": action_stop_server},
    {"key": "4", "label": "实时追踪服务日志 (tail -f 模式)", "action": action_tail_logs},
    {"key": "5", "label": "查看详细服务健康与统计诊断", "action": action_diagnostics},
    {"key": "6", "label": "在浏览器中打开 Web 控制台", "action": action_open_dashboard},
    {"key": "7", "label": "重新编译前端静态资源 (npm run build)", "action": action_build_frontend},
    {"key": "8", "label": "运行 Pytest 单元测试", "action": action_run_tests},
]


def display_width(s):
    """Calculate terminal display width taking East Asian wide characters into account."""
    w = 0
    for ch in s:
        if unicodedata.east_asian_width(ch) in ("F", "W"):
            w += 2
        else:
            w += 1
    return w


def pad_to_width(s, target_width):
    """Pad string with trailing spaces to reach target visual terminal display width."""
    w = display_width(s)
    if w < target_width:
        return s + " " * (target_width - w)
    return s


def render_ui(selected_idx, clear_screen=False):
    """Render formatted TUI menu into an in-memory buffer with exact line clearing."""
    pid = get_server_pid()
    healthy, data = is_server_healthy()

    if healthy:
        ext_str = " | 扩展: 已连接" if (data and data.get("extension_connected")) else " | 扩展: 未连接"
        status_str = f"{Fore.GREEN}● 运行中 (PID: {pid}{ext_str}){Style.RESET_ALL}"
    else:
        status_str = f"{Fore.RED}○ 已停止{Style.RESET_ALL}"

    lines = [
        f"{Fore.CYAN}===================================================={Style.RESET_ALL}\033[K",
        f"{Fore.CYAN}            FlowKit 控制台管理终端 (TUI)             {Style.RESET_ALL}\033[K",
        f"{Fore.CYAN}===================================================={Style.RESET_ALL}\033[K",
        f" 服务状态 (8100): {status_str}\033[K",
        f" 控制台地址: http://127.0.0.1:{PORT}\033[K",
        f" 环境位置: {sys.executable}\033[K",
        f"{Fore.CYAN}----------------------------------------------------{Style.RESET_ALL}\033[K",
        f" 使用 {Fore.YELLOW}↑/↓ 方向键{Style.RESET_ALL} 选择，{Fore.YELLOW}Enter{Style.RESET_ALL} 确认 | 直接按 {Fore.YELLOW}[数字键]{Style.RESET_ALL} 或 {Fore.YELLOW}[Q]{Style.RESET_ALL} 退出\033[K",
        f"\033[K",
    ]

    MENU_WIDTH = 46

    for idx, cmd in enumerate(COMMANDS):
        prefix = " > " if idx == selected_idx else "   "
        key_hint = f"[{cmd['key']}]"

        if idx == selected_idx:
            raw_text = f"{prefix}{key_hint} {cmd['label']}"
            padded = pad_to_width(raw_text, MENU_WIDTH)
            lines.append(f"{Back.BLUE}{Fore.WHITE}{padded}{Style.RESET_ALL}\033[K")
        else:
            prefix_fmt = f"{prefix}{Fore.YELLOW}{key_hint}{Style.RESET_ALL} {cmd['label']}"
            lines.append(f"{prefix_fmt}\033[K")

    lines.append(f"\033[K")
    lines.append(f"   {Fore.RED}[Q]{Style.RESET_ALL} 退出 TUI\033[K")
    lines.append(f"{Fore.CYAN}===================================================={Style.RESET_ALL}\033[K")

    prefix_ctrl = "\033[2J\033[H\033[?25l" if clear_screen else "\033[H\033[?25l"
    sys.stdout.write(prefix_ctrl + "\n".join(lines) + "\n\033[J")
    sys.stdout.flush()


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
            try:
                import colorama
                colorama.just_fix_windows_console()
            except Exception:
                pass
        except Exception:
            pass

    # Direct CLI argument execution
    if len(sys.argv) > 1:
        arg = sys.argv[1].strip()
        for cmd in COMMANDS:
            if arg == cmd["key"] or arg.lower() == cmd["label"].lower():
                print(f"执行命令: [{cmd['key']}] {cmd['label']}")
                cmd["action"]()
                return
        if arg in ("-h", "--help"):
            print("FlowKit CLI Launcher")
            for cmd in COMMANDS:
                print(f"  [{cmd['key']}] {cmd['label']}")
            return

    selected_idx = 0
    need_full_clear = True

    try:
        while True:
            render_ui(selected_idx, clear_screen=need_full_clear)
            need_full_clear = False

            key = get_key_input()

            if key == KEY_UP:
                selected_idx = (selected_idx - 1) % len(COMMANDS)
            elif key == KEY_DOWN:
                selected_idx = (selected_idx + 1) % len(COMMANDS)
            elif key == KEY_ENTER:
                sys.stdout.write("\033[?25h\033[2J\033[H")
                sys.stdout.flush()
                cmd = COMMANDS[selected_idx]
                cmd["action"]()
                need_full_clear = True
            elif key == KEY_QUIT:
                sys.stdout.write("\033[?25h\n")
                sys.stdout.flush()
                print(f"{Fore.CYAN}感谢使用 FlowKit TUI 控制台，再见！{Style.RESET_ALL}")
                break
            else:
                for idx, cmd in enumerate(COMMANDS):
                    if key == cmd["key"]:
                        selected_idx = idx
                        sys.stdout.write("\033[?25h\033[2J\033[H")
                        sys.stdout.flush()
                        cmd["action"]()
                        need_full_clear = True
                        break
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
