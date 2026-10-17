"""
gushi5 启动器 —— 一个文件管住"怎么跑"。

    python start.py            网页版（默认 127.0.0.1:8765，通了自动开浏览器）
    python start.py web 9000   网页版，指定端口
    python start.py term       终端版
    python start.py test       跑离线套件（不花 API 额度）
    python start.py help

# 为什么要有它

gushi3 没有启动器：主循环住在 `src/test/N2_contextRetrieval.py`，要跑得先记住
"哪个文件是入口"、再手动确认依赖装没装、key 有没有。启动器把这三件事收成一条命令。

# 两个不显然但必要的地方

1. **子进程一律带 `PYTHONIOENCODING=utf-8`**。Windows 的默认编码是代码页（GBK），
   而终端输出里有 `✓/✗/📍` 这些字符——不设的话会在**打印**上抛 `UnicodeEncodeError`，
   看起来像程序崩了，其实是打印崩了。
2. **`.bat` 里一个非 ASCII 字符都不能有**（cmd.exe 按代码页逐字节解析批处理，
   UTF-8 中文会被解析错乱，注释行会被当命令执行）。所以 `start.bat` 只有两行转发。
"""

import os
import subprocess
import sys
import threading
import time
import webbrowser

DEFAULT_PORT = 8765
ROOT = os.path.dirname(os.path.abspath(__file__))

#: 离线套件。前两个**完全不调 API**；后三个要构造 GameController（会建 LLMClient、
#: 读 base_cnf 里的 API_KEY），所以它们自己带护栏：没 key 就打一行 SKIP 并 exit 0。
OFFLINE_SUITES = (
    'src/test/map_edit_test.py',
    'src/test/map_migration_test.py',
    'src/test/clock_calendar_test.py',      # 时辰制真日历 / 中文数字换算 / 旧档兼容
    'src/test/time_skip_test.py',           # 时间跳过换算 / N8 取 max / 大跳后结清在途 / 心跳兜底
    'src/test/task_store_test.py',          # 任务表状态机 / 期限逾期 / 预设触发 / 通道兼容 / 存档往返
    'src/test/place_resolve_test.py',       # 地名解析：精确/全路径/末段/段/子串，歧义与编造一律拒绝
)

_KEY_HINT = (
    '  API key 没设。本机它是机器级环境变量，App 进程可能继承不到，从注册表取：\n'
    '  export DEEPSEEK_API_KEY="$(reg query '
    '"HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment" '
    '| grep -i DEEPSEEK_API_KEY | sed \'s/.*REG_SZ[[:space:]]*//\' | tr -d \'\\r\\n\')"'
)


def _child_env():
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'      # 见模块 docstring 第 1 条
    return env


def ensure_module(import_name: str, pip_name: str = None) -> bool:
    """缺依赖就自己装——启动器的存在意义之一就是"不用先读 README"。"""
    try:
        __import__(import_name)
        return True
    except ImportError:
        pass
    pip_name = pip_name or import_name
    print(f'  ⏳ 缺依赖 {pip_name}，正在安装…')
    r = subprocess.call([sys.executable, '-m', 'pip', 'install', pip_name])
    if r != 0:
        print(f'  ❌ 安装 {pip_name} 失败——请手动 `pip install {pip_name}`')
        return False
    return True


def _warn_if_no_key():
    if not (os.environ.get('DEEPSEEK_API_KEY') or os.environ.get('MODELSCOPE_API_KEY')):
        print('⚠️  ' + _KEY_HINT + '\n')


def run_web(port: int):
    if not ensure_module('flask'):
        return 1
    _warn_if_no_key()
    server = os.path.join(ROOT, 'web', 'server.py')

    def _open_later():
        time.sleep(1.2)
        try:
            webbrowser.open(f'http://127.0.0.1:{port}')
        except Exception:
            pass

    threading.Thread(target=_open_later, daemon=True).start()
    print(f'\n  gushi5 · http://127.0.0.1:{port}\n')
    return subprocess.call([sys.executable, server, '--port', str(port)],
                           cwd=ROOT, env=_child_env())


def run_term():
    _warn_if_no_key()
    return subprocess.call([sys.executable, os.path.join(ROOT, 'main.py')],
                           cwd=ROOT, env=_child_env())


def run_tests():
    _warn_if_no_key()
    failed = []
    for suite in OFFLINE_SUITES:
        path = os.path.join(ROOT, suite)
        if not os.path.isfile(path):
            print(f'  · 跳过（不存在）：{suite}')
            continue
        print(f'\n───── {suite} ─────')
        r = subprocess.call([sys.executable, os.path.basename(suite)],
                            cwd=os.path.dirname(path), env=_child_env())
        if r != 0:
            failed.append(suite)
    print()
    if failed:
        print(f'❌ {len(failed)} 个套件失败：' + '、'.join(failed))
        return 1
    print(f'✅ {len(OFFLINE_SUITES)} 个离线套件全部通过')
    return 0


def main(argv):
    args = [a for a in argv[1:]]
    mode = args[0].lower() if args else 'web'
    if mode in ('web', ''):
        port = int(args[1]) if len(args) > 1 and args[1].isdigit() else DEFAULT_PORT
        return run_web(port)
    if mode == 'term':
        return run_term()
    if mode == 'test':
        return run_tests()
    if mode in ('help', '-h', '--help'):
        print(__doc__)
        return 0
    print(f'未知参数：{mode}\n')
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
