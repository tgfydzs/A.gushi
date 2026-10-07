#!/usr/bin/env python3
"""
gushi4 启动器

    python start.py             网页版（默认，端口 8765）
    python start.py web 9000    网页版，指定端口
    python start.py term        终端版
    python start.py test        跑离线测试（不花 API 额度）
    python start.py help        看用法

# 为什么是 Python 而不是 .bat / .sh

Windows 的 cmd.exe **按当前代码页逐字节解析批处理文件**，而 `chcp` 一改代码页，
后面所有含中文的行都会解析错乱（实测把 `start.bat` 里的参数判断搞坏过）。
一个脚本同时要覆盖 cmd / PowerShell / Git Bash 三种编码环境，写两遍还都不稳。

Python 没这个问题：它是跑起来的进程，编码自己说了算。
一个文件，三个平台，中文随便写。

Windows 上双击也能跑（见 `start.bat`，那个只剩两行转发）。
"""

import os
import subprocess
import sys
import threading
import time
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

DEFAULT_PORT = 8765
WEB_READY_TIMEOUT = 120          # 等端口起来的秒数上限（载入世界+NPC 约 10 秒）


# ══════════════════════════════════════════════════════════════
#  控制台
# ══════════════════════════════════════════════════════════════

def setup_console():
    """
    把控制台切成 UTF-8。

    不做这一步的话，Windows 上 Python 默认按控制台代码页（简中是 cp936）编码输出，
    打印 `✓` 这类字符会直接 `UnicodeEncodeError` 崩掉——这是项目里踩过的坑
    （smoke_test 第一次跑就是这么挂的）。

    两个动作都要做：
      - `SetConsoleOutputCP(65001)` 改的是**控制台**，让 cmd 窗口能显示 UTF-8
      - `reconfigure` 改的是 **Python 自己**往 stdout 写什么字节
    在 Git Bash / mintty 下 stdout 是管道，前者是空操作，后者仍然正确。
    """
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


def say(*a):
    print(*a, flush=True)


def banner():
    say()
    say('  gushi4')
    say('  ' + '=' * 60)


# ══════════════════════════════════════════════════════════════
#  检查
# ══════════════════════════════════════════════════════════════

def child_env():
    """子进程的环境：强制 UTF-8，免得游戏里的中文输出在管道里崩。"""
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


def run(script_args, **kw):
    """跑一个子进程，返回退出码。Ctrl-C 交给子进程自己处理。"""
    try:
        return subprocess.call([sys.executable] + script_args,
                               env=child_env(), cwd=ROOT, **kw)
    except KeyboardInterrupt:
        return 130


def has_module(name) -> bool:
    return subprocess.call([sys.executable, '-c', f'import {name}'],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, cwd=ROOT) == 0


def ensure_module(import_name, pip_name=None) -> bool:
    """装了就直接过；没装就现装。装不上返回 False。"""
    if has_module(import_name):
        return True
    say(f'  [!] 缺 {import_name}，正在安装...')
    rc = subprocess.call([sys.executable, '-m', 'pip', 'install',
                          pip_name or import_name], cwd=ROOT)
    if rc != 0 or not has_module(import_name):
        say(f'  [x] {import_name} 装不上。手动来一下：'
            f'  {os.path.basename(sys.executable)} -m pip install {pip_name or import_name}')
        return False
    return True


def check_python():
    if sys.version_info < (3, 10):
        say(f'  [x] Python 版本太低：{sys.version.split()[0]}，需要 3.10+')
        return False
    return True


def check_api_key() -> bool:
    if os.environ.get('DEEPSEEK_API_KEY') or os.environ.get('MODELSCOPE_API_KEY'):
        return True
    say()
    say('  [x] 没找到 API key。先设一个：')
    say()
    if os.name == 'nt':
        say('      只对当前窗口有效:   set DEEPSEEK_API_KEY=sk-xxx')
        say('      永久生效（推荐）:   setx DEEPSEEK_API_KEY "sk-xxx"')
        say('                          ^^ 设完要重开一个终端')
    else:
        say('      只对当前窗口有效:   export DEEPSEEK_API_KEY=sk-xxx')
        say('      永久生效（推荐）:   把这行写进 ~/.bashrc 或 ~/.zshrc')
    say()
    return False


# ══════════════════════════════════════════════════════════════
#  等端口通了就开浏览器
# ══════════════════════════════════════════════════════════════

def port_alive(port: int) -> bool:
    import urllib.request
    try:
        urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=1)
        return True
    except Exception:
        return False


def watch_and_open(port: int):
    """
    后台线程：轮询端口，通了就开浏览器、然后退出。

    **不做固定延时**。载入世界 + 11 个 NPC 大概 10 秒，但那是现在的数字——
    写死一个 sleep 迟早不准：快了浏览器打开是连接失败页，慢了用户干等。
    """
    deadline = time.time() + WEB_READY_TIMEOUT
    while time.time() < deadline:
        if port_alive(port):
            webbrowser.open(f'http://127.0.0.1:{port}/')
            return
        time.sleep(0.5)


def port_in_use(port: int) -> bool:
    """端口上已经有东西在应答吗。用来在启动前拦住"上次没关干净的实例"。"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex(('127.0.0.1', port)) == 0


#: 关窗时要跑的收尾函数（有界、不能拖）
_close_hooks: list = []


def console_window_handle():
    """拿到本进程所在那个控制台窗口的句柄。没有窗口（输出被重定向）时返回 None。"""
    if os.name != 'nt':
        return None
    try:
        import ctypes
        h = ctypes.windll.kernel32.GetConsoleWindow()
        return int(h) if h else None
    except Exception:
        return None


def watch_console_window(on_gone, interval: float = 1.5):
    """
    盯住"本进程所在的那个控制台窗口"，窗口一消失就收工。

    **为什么要再加这一道**：关窗口时 Windows 走的是 `CTRL_CLOSE_EVENT`，
    但那个事件的投递依赖**终端宿主**——conhost / Windows Terminal / ConEmu
    各不相同，而且在自动化环境里连"找到那个窗口"都做不到（`FindWindow` 返回 0），
    所以我没法可靠地验证它。

    而 `IsWindow(控制台窗口句柄)` 是确定的：窗口在就是真，没了就是真没了。
    这样"关窗停止服务"就不依赖任何事件投递，**也不怕 cmd 是被杀而不是被关**
    （杀 cmd 不会发 CTRL_CLOSE_EVENT，那种情况下子进程会变成孤儿——
    实测过，python 确实活下来了）。

    非 Windows 返回 None，那边关终端发 SIGHUP，是另一套。
    """
    if os.name != 'nt':
        return None

    hwnd = console_window_handle()
    if not hwnd:
        say('  [i] 没有控制台窗口可盯（输出被重定向了？）——"关窗即停"这次不生效')
        return None

    def _loop():
        import ctypes
        user32 = ctypes.windll.user32
        while True:
            time.sleep(interval)
            if not user32.IsWindow(hwnd):
                # 窗口没了。跑收尾，然后**硬退**——此时已经没有"正常退出"可言，
                # 拖下去只会变成孤儿进程占着端口
                for fn in _close_hooks:
                    try:
                        fn()
                    except Exception:
                        pass
                os._exit(0)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t


def install_console_close_hook():
    """
    Windows 点窗口右上角的 X 时，系统发的是 `CTRL_CLOSE_EVENT`——
    **Python 不会把它变成异常，`atexit` 也不会跑**，进程直接被终止。
    那意味着后台存档线程可能被硬切在半路上。

    这里挂一个控制台处理器，抢在进程被终止前做一次**有界**的收尾
    （注意系统只给几秒，所以钩子里不能用 `wait_for_saves(timeout=30)` 那种长等待）。

    非 Windows 平台直接返回——那边关终端是发 SIGHUP，走的是另一套。
    """
    if os.name != 'nt':
        return
    try:
        import ctypes
        from ctypes import wintypes

        proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def _handler(ctrl_type):
            # 2 = CTRL_CLOSE_EVENT, 5 = LOGOFF, 6 = SHUTDOWN
            if ctrl_type in (2, 5, 6):
                for fn in _close_hooks:
                    try:
                        fn()
                    except Exception:
                        pass
            return False        # False = 继续走默认处理，进程照常退出

        # ⚠️ 回调必须留一个引用住，否则被 GC 掉之后系统回调进来就崩
        install_console_close_hook._ref = proto(_handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(install_console_close_hook._ref, True)
    except Exception as e:
        say(f'  [i] 没挂上"关窗收尾"钩子（{e}）——不影响使用')


# ══════════════════════════════════════════════════════════════
#  各模式
# ══════════════════════════════════════════════════════════════

def mode_web(port: int) -> int:
    if not ensure_module('flask'):
        return 1

    # 先把"上次没关干净"拦住——不然 Flask 会在载入世界 10 秒之后才报端口占用，
    # 那时候用户已经等半天了，报错还淹没在一堆启动日志里
    if port_in_use(port):
        say()
        say(f'  [x] 端口 {port} 上已经有东西在应答。')
        say('     多半是上一次没关干净的实例。查并清掉：')
        say()
        say(f'       netstat -ano | findstr :{port}')
        say('       taskkill /PID <上面那一列最后的数字> /F')
        say()
        say(f'     或者换个端口：  python start.py web {port + 1}')
        return 1

    say()
    say(f'  网页版  ->  http://127.0.0.1:{port}')
    say('  首轮要载入世界和 NPC，约 10 秒——端口通了会自动开浏览器')
    say('  ────────────────────────────────────────────────────────')
    say('  这个是"窗口即服务"：关掉本窗口，服务一起停。')
    say('  想停就按 Ctrl-C 或直接关窗口，不会留孤儿进程。')
    say()

    threading.Thread(target=watch_and_open, args=(port,), daemon=True).start()

    # ── 直接在**本进程**里跑服务 ──
    #
    # 原来是 `start.py → 子进程 web/server.py` 两层。那样关窗口时子进程是靠
    # 控制台的 CTRL_CLOSE_EVENT **连带**死掉的——能用，但那是"依赖另一个机制
    # 顺带生效"，不是保证；哪天子进程脱离了控制台组就成孤儿。
    #
    # 跑在本进程里之后：一个进程 = 一个窗口 = 一条命。关窗口必然结束服务，
    # 端口必然释放，没有中间层可以出错。
    sys.path.insert(0, ROOT)
    try:
        from web.server import app, engine
    except Exception as e:
        say(f'  [x] 载入服务失败：{type(e).__name__}: {e}')
        return 1

    # 两道保险，覆盖"窗口消失"的不同成因：
    #   ① CTRL_CLOSE_EVENT 处理器 —— 点 X 时走这条（但投递依赖终端宿主）
    #   ② 盯控制台窗口句柄      —— 窗口没了就退，不依赖任何事件投递
    # 窗口被关时系统只给几秒，所以超时用得短——宁可少等一点，
    # 也不能因为等存档而被硬切（硬切反而更容易把文件写坏）
    _close_hooks.append(lambda: engine.gc.wait_for_saves(timeout=3.0))
    install_console_close_hook()
    watch_console_window(lambda: None)

    try:
        # threaded=True：一轮要跑十几秒，单线程的话回合期间连状态都刷不出来
        app.run(host='127.0.0.1', port=port, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        say('\n  收到 Ctrl-C，正在收尾...')
    finally:
        # atexit 里 web/server.py 也注册了一次收尾；wait_for_saves 是幂等的，
        # 这里显式跑一遍是为了让 Ctrl-C 这条路也能看到"存档已落盘"的反馈
        engine.close()
        say('  已停止。')
    return 0


def mode_term() -> int:
    say()
    say('  终端版——输入 exit 或 Ctrl-C 退出')
    say()
    return run(['main.py'])


SUITES = [
    ('smoke_test',      '模块/配置/纯规则/信息域边界/存档往返'),
    ('location_test',   '地点树归一化 / 地段属性 / 保留键不泄漏'),
    ('dry_run',         '假 LLM 跑完整一轮，验管线闭合'),
    ('background_test', '心跳 / 后台落盘 / 记忆巩固'),
    ('ui_test',         '引擎 / 事件流 / 终端渲染保真 / 叙事流历史'),
]


def mode_test() -> int:
    say()
    say('  离线测试（不消耗 API 额度）')
    say()
    failed = []
    for name, desc in SUITES:
        say(f'  ── {name}  ({desc})')
        rc = run([f'src/test/{name}.py'])
        if rc != 0:
            failed.append(name)
    say()
    if failed:
        say(f'  ✗ {len(failed)} 个套件失败：{"、".join(failed)}')
        return 1
    say(f'  ✓ {len(SUITES)} 个套件全部通过')
    return 0


def mode_help():
    say()
    say('  gushi4 启动器')
    say()
    say('    python start.py             网页版（默认，端口 8765）')
    say('    python start.py web 9000    网页版，指定端口')
    say('    python start.py term        终端版')
    say('    python start.py test        跑离线测试（不花 API 额度）')
    say()


# ══════════════════════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════════════════════

def main() -> int:
    setup_console()
    args = sys.argv[1:]
    mode = (args[0].lower() if args else 'web')
    port = DEFAULT_PORT
    if mode == 'web' and len(args) > 1:
        try:
            port = int(args[1])
        except ValueError:
            say(f'  端口得是数字，收到的是 {args[1]!r}')
            return 2

    if mode in ('help', '-h', '--help'):
        banner()
        mode_help()
        return 0

    banner()

    if not check_python():
        return 1
    if not ensure_module('requests'):
        return 1
    if not check_api_key():
        return 1

    if mode == 'test':
        return mode_test()
    if mode in ('term', 'terminal'):
        return mode_term()
    if mode == 'web':
        return mode_web(port)

    say(f'  未知参数：{mode}')
    mode_help()
    return 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say('\n  已中断。')
        sys.exit(130)
