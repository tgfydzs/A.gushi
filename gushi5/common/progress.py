"""
进度与终端输出。

两头都要讲清楚：

1. **终端输出接收器** —— 把 `(kind, text)` 还原成终端上的样子。
   gushi3 的 `GameController` 直接 `print`，于是命令回显只能出现在终端、网页拿不到。
   gushi4 改成"**发纯文本 + 语义标签**，由接收器决定样式"，终端与网页就成了
   同一个接口的两个实现。**ANSI 码是终端的事，不是数据的事**。
   它也是 `GameController.on_output` 的默认值：不传接收器时行为与改造前一致。

2. **阶段进度** —— 一轮要跑几十秒，网页上必须看得见"现在卡在哪一段"。
   终端不显示进度（它对着一行一行输出打字，进度反而是噪音）。
"""

import sys
import threading
import time


# ══════════════════════════════════════════════════════════════
#  终端输出
# ══════════════════════════════════════════════════════════════

def terminal_output(kind: str, text: str):
    """
    把一行输出打到终端。kind 的取值见 `GameController._out` 的说明。

    **不传接收器时行为不变**，所以"直接构造 GameController 抓 stdout"那种
    测试写法仍然成立。
    """
    if kind == 'narration':          # 旁白：灰斜体
        print(f'\033[3m  {text}\033[0m')
    elif kind == 'dim':              # 幕后简讯：暗字
        print(f'  \033[2m{text}\033[0m')
    else:                            # info / error / command：原样
        print(text)


# ══════════════════════════════════════════════════════════════
#  阶段进度
# ══════════════════════════════════════════════════════════════

class Stage:
    """
    一段耗时工序，用于给网页画进度。

    发三种事件（前端只认这三种，字段不能少）：

        {'type': 'stage', 'phase': 'start', 'name': ...}
        {'type': 'stage', 'phase': 'tick',  'name': ..., 'seconds': float, 'detail': ...}
        {'type': 'stage', 'phase': 'end',   'name': ..., 'seconds': float}

    ⚠️ `seconds` **必须是数字**：前端在 `phase='end'` 时会调 `seconds.toFixed(1)`，
    给字符串当场抛异常。

    `min_show`：跑得比它快的阶段**两个事件都不发**。不这么做的话，一轮里那几个
    几十毫秒的纯规则阶段会在进度条上闪一下就没，看着像卡顿而不是信息。
    实现上 start 是**延后**发的（不是先发再撤回）——撤回在 SSE 上做不到。

    `on_event` 为 None 时整个类退化成"只计时、不发事件"，终端侧就是这么用的。
    """

    def __init__(self, name: str, on_event=None, detail=None, min_show: float = 0.0):
        self.name = name
        self.on_event = on_event
        self.detail = detail
        self.min_show = min_show
        self._t0 = None
        self._ticker = None
        self._stop = threading.Event()
        self._started = False

    def _emit(self, ev: dict):
        if self.on_event:
            try:
                self.on_event(ev)
            except Exception:        # 进度发不出去不能影响正事
                pass

    def _tick_loop(self):
        # 每秒报一次，让进度条上的秒数一直在动
        while not self._stop.wait(1.0):
            detail = self.detail() if callable(self.detail) else self.detail
            self._emit({'type': 'stage', 'phase': 'tick', 'name': self.name,
                        'seconds': round(time.time() - self._t0, 1),
                        'detail': detail or ''})

    def __enter__(self):
        self._t0 = time.time()
        if self.min_show <= 0:
            self._started = True
            self._emit({'type': 'stage', 'phase': 'start', 'name': self.name})
        self._ticker = threading.Thread(target=self._tick_loop, daemon=True)
        self._ticker.start()
        return self

    def __exit__(self, *exc):
        elapsed = time.time() - self._t0
        self._stop.set()
        if self._ticker:
            self._ticker.join(timeout=0.2)
        if self.min_show > 0 and elapsed < self.min_show:
            return False             # 太快，整段不显示
        if not self._started:
            self._started = True
            self._emit({'type': 'stage', 'phase': 'start', 'name': self.name})
        self._emit({'type': 'stage', 'phase': 'end', 'name': self.name,
                    'seconds': round(elapsed, 1)})
        return False


# ══════════════════════════════════════════════════════════════
#  终端渲染器（引擎事件的另一个实现）
# ══════════════════════════════════════════════════════════════

def terminal_renderer(ev: dict):
    """
    把引擎事件还原成终端上的输出 —— 与网页渲染器**同一个接口**。

    对齐原则：**改造前后的终端输出逐行一致**。所以 `scene` 事件的四个字段
    还原成原来那四行，`time` 还原成最后那一行。`stage` 在终端**不显示**。
    """
    t = ev.get('type')
    if t == 'scene':
        print(f"\n📍 {ev.get('location') or ''}")
        present = ev.get('present') or []
        print(f'👥 在场：{"、".join(present)}' if present else '👥 （周围无人）')
        print(f"⏰ {ev.get('clock') or ''}")
    elif t == 'dialogue':
        print(f"\n{ev.get('speaker')}：{ev.get('text')}")
    elif t == 'narration':
        print(f'\033[3m  {ev.get("text")}\033[0m')
    elif t == 'dim':
        print(f'  \033[2m{ev.get("text")}\033[0m')
    elif t == 'time':
        print(f"\n⏰ [游戏时间 {ev.get('text')}（+{ev.get('minutes', 0):.0f}分）]")
    elif t in ('info', 'error'):
        print(ev.get('text') or '')
    elif t == 'round_start':
        # 终端在读到输入时就自己回显了，这里不重复
        pass
    # stage / round_done / round_queued：终端不显示


def flush():
    """确保终端输出即时可见（后台线程里打印时用）。"""
    try:
        sys.stdout.flush()
    except Exception:
        pass
