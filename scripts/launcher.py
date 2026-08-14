"""FlowKit CLI / TUI Launcher & Control Panel.

Runs with Python 3.10+ in D:\\python_envs\\flowkit\\python.exe.
Provides a keyboard-navigable terminal UI for starting, monitoring,
and maintaining FlowKit services.
"""
import os
import sys
import time
import subprocess
import webbrowser
import urllib.request
import urllib.error
import json
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

import socket

# Key constants
KEY_UP = "UP"
KEY_DOWN = "DOWN"
KEY_ENTER = "ENTER"
KEY_QUIT = "QUIT"
KEY_UNKNOWN = "UNKNOWN"

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
PID_FILE = PROJECT_ROOT / ".flowkit.pid"
HEALTH_URL = "http://127.0.0.1:8100/health"
DASHBOARD_URL = "http://127.0.0.1:8100/dashboard"

# Active server process reference if started by this script instance
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


def get_server_pid():
    """Get server PID from PID file if running."""
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
    return None


def is_server_healthy(force=False):
    """Fast, non-blocking check if server health endpoint responds on 8100."""
    global _last_health_check, _cached_health
    now = time.time()
    if not force and (now - _last_health_check < 2.0):
        return _cached_health

    _last_health_check = now

    # 1. Fast socket probe (0.02s timeout) to avoid urllib connection freeze
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.02)
            if s.connect_ex(("127.0.0.1", 8100)) != 0:
                _cached_health = (False, None)
                return _cached_health
    except Exception:
        _cached_health = (False, None)
        return _cached_health

    # 2. Port is open, quickly read health json
    try:
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
    pid = get_server_pid()
    healthy, data = is_server_healthy(force=True)

    if healthy or pid:
        print(f"\n{Fore.YELLOW}FlowKit 服务已经在运行中 (PID: {pid or '未知'})!{Style.RESET_ALL}")
        time.sleep(1.5)
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
    print(f"{Fore.GREEN}进程已启动 (PID: {_server_process.pid})，日志记录至 flowkit_server.log{Style.RESET_ALL}")

    print("等待健康检查...")
    for _ in range(10):
        time.sleep(1)
        healthy, _ = is_server_healthy(force=True)
        if healthy:
            print(f"{Fore.GREEN}✓ FlowKit 服务已启动并在 http://127.0.0.1:8100 就绪!{Style.RESET_ALL}")
            time.sleep(1.5)
            return

    print(f"{Fore.YELLOW}服务已启动，但健康检查响应较慢，请稍后通过状态检测查看。{Style.RESET_ALL}")
    time.sleep(2)


def action_check_status():
    print(f"\n{Fore.CYAN}--- FlowKit 系统状态检查 ---{Style.RESET_ALL}")
    pid = get_server_pid()
    print(f"后台进程 PID: {pid if pid else '未运行/未知'}")

    healthy, data = is_server_healthy(force=True)
    if healthy:
        print(f"服务状态: {Fore.GREEN}正常 (OK){Style.RESET_ALL}")
        print(f"Extension 连接: {Fore.GREEN if data.get('extension_connected') else Fore.RED}{data.get('extension_connected')}{Style.RESET_ALL}")
        print(f"WebSocket 统计: {data.get('ws')}")
    else:
        print(f"服务状态: {Fore.RED}无法连接 8100 端口{Style.RESET_ALL}")

    print("\n按任意键返回主菜单...")
    get_key_input()


def action_open_dashboard():
    healthy, _ = is_server_healthy(force=True)
    if not healthy:
        print(f"\n{Fore.YELLOW}警告: 服务似乎未连接，尝试打开 Web 控制台...{Style.RESET_ALL}")
    else:
        print(f"\n{Fore.GREEN}正在打开 Web 控制台: {DASHBOARD_URL}{Style.RESET_ALL}")
    webbrowser.open(DASHBOARD_URL)
    time.sleep(1)


def action_restart_server():
    action_stop_server()
    time.sleep(1)
    action_start_server()


def action_stop_server():
    global _server_process
    pid = get_server_pid()
    print(f"\n{Fore.YELLOW}正在停止 FlowKit 服务...{Style.RESET_ALL}")

    if _server_process and _server_process.poll() is None:
        _server_process.terminate()
        try:
            _server_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _server_process.kill()
        _server_process = None

    if pid:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
            else:
                os.kill(pid, 9)
        except Exception:
            pass

    if PID_FILE.exists():
        try:
            PID_FILE.unlink()
        except Exception:
            pass

    # Invalidate cache
    is_server_healthy(force=True)
    print(f"{Fore.GREEN}✓ 所有 FlowKit 服务及进程树已停止{Style.RESET_ALL}")
    time.sleep(1)


def action_run_tests():
    print(f"\n{Fore.CYAN}--- 运行 Pytest 单元测试 ---{Style.RESET_ALL}")
    python_exe = sys.executable
    try:
        subprocess.run([python_exe, "-m", "pytest", "tests/"], cwd=str(PROJECT_ROOT))
    except Exception as e:
        print(f"{Fore.RED}测试执行失败: {e}{Style.RESET_ALL}")
    print("\n按任意键返回主菜单...")
    get_key_input()


def action_view_logs():
    log_path = PROJECT_ROOT / "flowkit_server.log"
    print(f"\n{Fore.CYAN}--- 最近服务日志 ({log_path.name}) ---{Style.RESET_ALL}")
    if not log_path.exists():
        print(f"{Fore.YELLOW}暂无日志文件。{Style.RESET_ALL}")
    else:
        try:
            lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            last_lines = lines[-25:] if len(lines) > 25 else lines
            for line in last_lines:
                print(line)
        except Exception as e:
            print(f"{Fore.RED}读取日志出错: {e}{Style.RESET_ALL}")
    print("\n按任意键返回主菜单...")
    get_key_input()


# Extensible Command Registry
COMMANDS = [
    {"key": "1", "label": "启动 FlowKit 服务", "action": action_start_server, "cat": "Core"},
    {"key": "2", "label": "检查服务健康状态", "action": action_check_status, "cat": "Core"},
    {"key": "3", "label": "打开 Web 控制台 (Dashboard)", "action": action_open_dashboard, "cat": "Core"},
    {"key": "4", "label": "重启 FlowKit 服务", "action": action_restart_server, "cat": "Manage"},
    {"key": "5", "label": "停止 FlowKit 服务", "action": action_stop_server, "cat": "Manage"},
    {"key": "6", "label": "查看最新服务日志", "action": action_view_logs, "cat": "Tools"},
    {"key": "7", "label": "运行 Pytest 测试集", "action": action_run_tests, "cat": "Tools"},
]


import unicodedata


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
    healthy, _ = is_server_healthy()

    status_str = f"{Fore.GREEN}● 运行中 (PID: {pid}){Style.RESET_ALL}" if healthy else f"{Fore.RED}○ 已停止{Style.RESET_ALL}"

    lines = [
        f"{Fore.CYAN}===================================================={Style.RESET_ALL}\033[K",
        f"{Fore.CYAN}            FlowKit 控制台管理终端 (TUI)             {Style.RESET_ALL}\033[K",
        f"{Fore.CYAN}===================================================={Style.RESET_ALL}\033[K",
        f" 服务状态: {status_str}\033[K",
        f" API 地址: http://127.0.0.1:8100\033[K",
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
            raw_text = f"{prefix}{key_hint} {cmd['label']}"
            # Render unselected line with yellow key hint and clean text
            prefix_fmt = f"{prefix}{Fore.YELLOW}{key_hint}{Style.RESET_ALL} {cmd['label']}"
            lines.append(f"{prefix_fmt}\033[K")

    lines.append(f"\033[K")
    lines.append(f"   {Fore.RED}[Q]{Style.RESET_ALL} 退出 TUI\033[K")
    lines.append(f"{Fore.CYAN}===================================================={Style.RESET_ALL}\033[K")

    # Build entire buffer: hide cursor (\033[?25l) + cursor home (\033[H)
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

    # Check CLI arguments for direct command execution (e.g., launcher.py 1 or launcher.py 2)
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
                print(f"  {cmd['key']}: {cmd['label']}")
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
                # Check direct number key matching
                for idx, cmd in enumerate(COMMANDS):
                    if key == cmd["key"]:
                        selected_idx = idx
                        sys.stdout.write("\033[?25h\033[2J\033[H")
                        sys.stdout.flush()
                        cmd["action"]()
                        need_full_clear = True
                        break
    finally:
        # Always restore cursor
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

