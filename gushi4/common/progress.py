"""
终端进度显示 —— 长调用期间让人知道"它还在动"。

# 为什么需要

一轮流水线里 L1/L2/L3 都是十几秒到**两分钟**的 LLM 调用，期间控制台一片空白。
实测被当成"程序卡死了"问过——**实际上什么也没坏，只是没有任何反馈**。

这类问题值得单独修，因为它和"性能"是两件事：
把 100 秒显示出来，和把 100 秒变成 20 秒，解决的是不同的问题。

# 设计要点

- **原地重绘一行**（`\\r`），不刷屏
- **延迟启动**：调用快就不闪。短调用占多数，闪一下反而是噪音
- **先换行**：阶段开始时先写一个 `\\n`，免得把玩家刚敲的那行输入覆盖掉
- **支持 detail 回调**：并行层可以显示"3/6 个 NPC"这种进度
- **非交互自动降级**：管道/重定向时不重绘（否则 `\\r` 会污染抓取的输出），
  但仍然在结束时打一行耗时——跑实验和看日志时这些耗时是有用的数据

用法：

    with Stage('L1 感知过滤'):
        result = l1.judge(...)

    done = [0]
    with Stage('L2 角色层', detail=lambda: f'{done[0]}/{total} 个NPC'):
        ...

# 事件模式（web 端）

传 `on_event` 会**额外**回调结构化事件：

    {'type': 'stage', 'phase': 'start'|'tick'|'end', 'name': ..., 'seconds': ..., 'detail': ...}

`tick` 每 0.4 秒一次（`detail` 里的计数就是从这里来的）。进度条的"还在动"感
在网页上靠这个维持——一轮 7.4 秒里没有任何反馈的话，和终端那个"以为卡死了"
是同一个问题。

⚠️ 事件是**叠加**在原有行为上，不是替代。终端有 TTY 时照常重绘那行 `⟳`；
服务器进程没有 TTY，所以只剩结束时的 `✓` 行——正好当服务端日志用。
做成互斥的话，网页模式下 `on_event` 一传，终端渲染就被关掉了。
"""

import sys
import threading
import time


# ===================== 终端输出渲染 =====================

def terminal_output(kind: str, text: str):
    """
    把 `(kind, text)` 还原成终端上的样子。

    ANSI 码是**终端的事**，不是数据的事——所以 `GameController` 只发纯文本 + 类型标签，
    由这里决定灰字/暗字。这样同一份输出推到网页时不会被 `\\033[3m` 污染。

    也是 `GameController.on_output` 的默认值：测试直接构造 GameController 时，
    行为与改造前完全一致。
    """
    if kind == 'narration':          # 旁白：灰斜体
        print(f'\033[3m  {text}\033[0m')
    elif kind == 'dim':              # 幕后简讯：暗字
        print(f'  \033[2m{text}\033[0m')
    else:                            # info / error / command：原样
        print(text)

# 短于这个秒数就不启动计时线程（避免快调用时闪一下）
DEFAULT_MIN_SHOW = 1.0

# 全局开关，方便调用方整体关掉
ENABLED = True


class Stage:
    """
    一个阶段的计时器兼进度指示。

    :param name:     阶段名，会显示在进度行上
    :param detail:   可选回调，返回一段附加文字（如 '3/6 个NPC'）
    :param min_show: 短于这个秒数的阶段不启动实时重绘
    :param on_event: 传了就走事件模式——不碰 stdout，改推结构化事件（web 端用）
    """

    def __init__(self, name: str, detail=None, min_show: float = DEFAULT_MIN_SHOW,
                 on_event=None):
        self.name = name
        self.detail = detail
        self.min_show = min_show
        self.on_event = on_event
        self._stop = threading.Event()
        self._thread = None
        self._t0 = 0.0
        self._interactive = bool(getattr(sys.stdout, 'isatty', lambda: False)())
        self._drew = False

    # ---------- 生命周期 ----------

    def __enter__(self):
        self._t0 = time.time()
        if not ENABLED:
            return self

        if self.on_event is not None:
            self.on_event({'type': 'stage', 'phase': 'start', 'name': self.name})

        if self._interactive or self.on_event is not None:
            if self._interactive:
                # 先换行：玩家刚敲的输入还留在 `>` 那一行上，别覆盖它
                sys.stdout.write('\n')
                sys.stdout.flush()
            self._thread = threading.Thread(target=self._tick, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

        elapsed = time.time() - self._t0
        if not ENABLED:
            return False

        if self.on_event is not None:
            self.on_event({'type': 'stage', 'phase': 'end', 'name': self.name,
                           'seconds': round(elapsed, 1)})

        # 只显示到 0.1 秒；再细没意义
        text = f'✓ {self.name}（{elapsed:.1f}s）'
        if self._drew:
            self._clear()
        sys.stdout.write(f'  \033[2m{text}\033[0m\n' if self._interactive
                         else f'  {text}\n')
        sys.stdout.flush()
        return False

    # ---------- 重绘 ----------

    def _tick(self):
        # 先等一会再开始画：太快的调用不值得闪
        if self._stop.wait(self.min_show):
            return
        while not self._stop.wait(0.4):
            self._draw()
        if self._drew:
            self._clear()

    def _detail_text(self) -> str:
        try:
            return f'  {self.detail()}' if self.detail else ''
        except Exception:
            return ''

    def _draw(self):
        elapsed = time.time() - self._t0
        extra = self._detail_text()

        # 事件是**叠加**的，不互斥：终端照画进度行，网页同时收到 tick
        if self.on_event is not None:
            self.on_event({'type': 'stage', 'phase': 'tick', 'name': self.name,
                           'seconds': round(elapsed, 1),
                           'detail': extra.strip()})
        if not self._interactive:
            return

        self._drew = True
        line = f'  ⟳ {self.name}… {elapsed:.0f}s{extra}'
        sys.stdout.write('\r\033[2m' + line + '\033[0m\033[K')
        sys.stdout.flush()

    @staticmethod
    def _clear():
        sys.stdout.write('\r\033[K')
        sys.stdout.flush()


# ===================== 自检 =====================

if __name__ == '__main__':
    import threading as _th

    with Stage('短阶段（0.3s，不该闪）'):
        time.sleep(0.3)

    done = [0]
    total = 3

    def _fake_worker():
        for _ in range(total):
            time.sleep(0.8)
            done[0] += 1

    with Stage('长阶段（2.4s，应显示计数）', detail=lambda: f'{done[0]}/{total} 个NPC'):
        _th.Thread(target=_fake_worker, daemon=True).start()
        while done[0] < total:
            time.sleep(0.1)

    with Stage('抛异常的阶段'):
        raise ValueError('阶段内部异常，计时器不该把它吞掉')

    # ---- 事件模式：不该往 stdout 写任何东西 ----
    events = []
    with Stage('事件模式阶段', on_event=events.append):
        time.sleep(0.1)
    assert events[0] == {'type': 'stage', 'phase': 'start', 'name': '事件模式阶段'}, events
    assert events[-1]['phase'] == 'end' and events[-1]['seconds'] >= 0, events
    print('✓ 事件模式自检通过：', events)
