"""
游戏引擎 —— 把主循环从脚本里抽出来，让终端和网页共用同一份流水线。

# 为什么要有这个文件

原来 `main.py` 是**脚本不是模块**：顶层 `while True` 直接跑，`gc` / `l1_layer` /
`round_no` 全是模块级全局，两处阻塞 `input()`，输出散落在五个文件的 `print()` 里。
结果是 `import main` 会当场卡死在 `input()` 上，网页没有任何地方可以挂。

抽出来之后有三条好处：

1. **一份流水线，两个前端**。终端和网页调同一个 `run_round()`，不会各改各的漂移。
2. **终端成为回归基准**。重构前后跑同样输入，终端输出必须逐行一致——
   这是"引擎抽取"这一步唯一的验收标准。
3. **阻塞点全部外移**。引擎自己**从不调用 `input()`**：玩家输入由调用方传进
   `run_round()`，移动目标由调用方先选定再通过 `resolve_move()` 换算。

# 事件流

所有输出去向统一成 `_emit(event)`。终端渲染器把事件还原成 `print`，
网页渲染器把事件塞进 `queue.Queue` 推 SSE——**同一接口的两个实现**。

`GameController` 自带一个 `on_output(kind, text)` 接收器，在 `attach()` 里接到
`_emit` 上，于是控制器里那些 `print` 也一并进了事件流。

# 并发

- **写**：`run_round()` 全程持 `self._lock`，一轮一次，不并发。
- **读**：只读接口**不加锁**。写回路径是整体重绑定而非原地改
  （`new = deepcopy(...); self.player.player_dict = new`），CPython 下单次属性赋值
  是原子的，读者要么看到旧 dict 要么看到新 dict，读不到半个。
  唯一要做的是组装视图前先浅拷贝——遍历 dict 期间被别处改动会抛 `RuntimeError`。
  已知代价：轮次进行中可能读到跨层混合快照（玩家位置已更新、NPC 回应尚未落盘），
  这是固有的，不是 bug。
"""

import os
import queue
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy

from common import log as log_cfg
from common import debug_tools as dt
from common.log import get_logger
from common.base_cnf import LAYER2_MAX_WORKERS
from common.game_controller import GameController
from common.L1_perception import L1_Perception
from common.L2_character import L2_Character
from common.L3_settlement import L3_Settlement
from common.R1_propagation import R1_Propagation
from common.R2_time import R2_Time
from common.background import BackgroundManager
from common.memory import MemoryManager
from common.progress import Stage
from common.small_tool import find_move_marker, apply_move_target

logger = get_logger('Engine')


#: 会进网页「叙事流」的事件类型。`stage`（进度条）和 `round_done` 不进。
STREAM_EVENT_TYPES = frozenset({
    'scene', 'round_start', 'dialogue', 'narration', 'dim', 'info', 'error', 'time',
})

#: 历史最多留这么多条，避免长时间挂机把内存吃满
HISTORY_LIMIT = 600

_NARR_PREFIX = '旁白：'
_PLAYER_SUFFIX = '（玩家）：'
_MOVE_SUFFIX = '（移动）：'


def parse_context_line(line: str):
    """
    把 `global_context` 的一行还原成流事件。

    格式（见 `Schema/Context_sch.py`）：`"轮次 | 时间 | 内容"`，内容带前缀区分类型。

    ⚠️ 这是**近似重建**——`global_context` 存的是纯文本，事件类型已经丢了。
    之所以还要用它，是因为它是**唯一在存档 / 读档 / 撤回三种情况下都正确**的记录：
    结构化事件队列在读档后会留着读档前的旧内容，撤回后也一样。
    """
    parts = str(line).split(' | ', 2)
    if len(parts) < 3:
        return None
    ts, content = parts[1], parts[2].strip()
    if not content:
        return None

    if content.startswith(_NARR_PREFIX):
        return {'type': 'narration', 'text': content[len(_NARR_PREFIX):], 'ts': ts}

    if _PLAYER_SUFFIX in content:
        who, said = content.split(_PLAYER_SUFFIX, 1)
        return {'type': 'round_start', 'input': said, 'who': who, 'ts': ts}

    if _MOVE_SUFFIX in content:
        who, rest = content.split(_MOVE_SUFFIX, 1)
        return {'type': 'info', 'text': f'{who}{_MOVE_SUFFIX}{rest}', 'ts': ts}

    if content.startswith('世界事件'):
        return {'type': 'info', 'text': content, 'ts': ts}

    # 其余按对白处理：`名字：内容`
    if '：' in content:
        who, said = content.split('：', 1)
        return {'type': 'dialogue', 'speaker': who, 'text': said, 'ts': ts}

    return {'type': 'info', 'text': content, 'ts': ts}


class GameEngine:
    """
    一次游戏会话的全部运行时状态 + 一轮流水线的执行。

    典型用法（终端）：

        engine = GameEngine()
        engine.attach(terminal_renderer)
        while True:
            act = engine.handle_input(input('>'))
            if act['action'] == 'break': break
            if act['action'] == 'run':
                engine.run_round(act['text'], act['move_info'])
    """

    def __init__(self, session_id: str = 'gushi4'):
        log_cfg.setup(session_id=session_id)

        self.session_id = session_id
        self.round_no = 0
        self.last_snap = None
        self._lock = threading.Lock()
        self._subscribers: list = []
        self._on_event = None
        # 叙事流的历史。网页 F5 之后靠它恢复，否则一刷新就白屏
        self._history = deque(maxlen=HISTORY_LIMIT)

        # ---- 状态控制器 ----
        self.gc = GameController(session_id=session_id)
        self.gc.on_output = self._gc_output       # 控制器的 print 并入事件流
        self.gc.init_world()
        self.gc.init_clock()
        self.gc.init_all_npcs()
        self.gc.init_player()

        # ---- 各层（构造一次，逐轮复用）----
        self.l1_layer = L1_Perception()
        self.l1_layer.set_world(self.gc.b1WorldLayer)
        self.l2_layer = L2_Character()
        self.l3_layer = L3_Settlement()
        self.l3_layer.set_world(self.gc.b1WorldLayer)   # 读 `生命值体系` 配置
        self.r1_prop = R1_Propagation()
        self.r2_time = R2_Time(self.gc.b1WorldLayer)
        self.memory_mgr = MemoryManager()
        self.bg_mgr = BackgroundManager(self.gc.deferred_queue, self.l2_layer,
                                        self.memory_mgr)

        self.player_name = self.gc.player.player_dict.get('玩家名称name', '玩家')
        logger.info(f'引擎就绪：{self.player_name} @ {self.gc.player_location}')

    # ===================== 事件流 =====================

    def attach(self, on_event):
        """注册同步渲染器（终端用）。传 None 解绑。"""
        self._on_event = on_event

    def subscribe(self, maxsize: int = 500) -> queue.Queue:
        """
        注册一个异步订阅者（web SSE 用），返回它自己的队列。

        队列**必须有界**：客户端断开但没清理干净时，无界队列会一直吃事件直到把内存撑爆。
        满了就丢事件（`_emit` 里 `put_nowait` + 吞 `Full`）——宁可少几条进度，
        也不能让一轮流水线卡在网络那一头。
        """
        q = queue.Queue(maxsize=maxsize)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def _emit(self, event: dict):
        """
        广播一个事件。

        事件形状：`{'type': ..., 'round': n, ...}`。类型见 `_gc_output` 与 `run_round`：
        `stage` / `narration` / `dim` / `info` / `error` / `scene` / `dialogue` /
        `background` / `time` / `round_start` / `round_end`。
        """
        ev = dict(event)
        ev.setdefault('round', self.round_no)

        # 落一份历史：网页刷新后靠 /api/history 恢复叙事流
        if ev.get('type') in STREAM_EVENT_TYPES:
            ev.setdefault('ts', self.gc.game_clock.now_str() if self.gc.game_clock else '')
            self._history.append(ev)

        # 队列满不能阻塞流水线——宁可丢事件，也不能让一轮卡住
        for q in list(self._subscribers):
            try:
                q.put_nowait(ev)
            except queue.Full:
                logger.warn('SSE 订阅者队列已满，丢弃一个事件')
        if self._on_event is not None:
            try:
                self._on_event(ev)
            except Exception as e:
                logger.error(f'事件渲染器异常：{type(e).__name__}: {e}')

    def emit(self, event: dict):
        """公开的事件入口——引擎外部（如 web 服务端）要往流里推东西时用这个。"""
        self._emit(event)

    # ===================== 叙事流历史 =====================

    def history(self, limit: int = HISTORY_LIMIT) -> list:
        """
        叙事流历史，供网页 F5 后恢复。**不含 `stage`**（进度条是瞬时的）。

        每项带 `ts`（游戏时间），前端可以据此标时间分隔。

        空局（还没跑过任何一轮）时补一条场景，否则页面恢复出来是一片白，
        连"你在哪、身边有谁"都不知道。**有历史时不再补**——真跑过一轮的记录里
        本来就有 scene，再补一条会重复。
        """
        items = list(self._history)[-limit:]
        return items if items else [self._scene_event()]

    def _scene_event(self) -> dict:
        loc = self.gc.player_location
        try:
            found = self.gc.b1WorldLayer.find_npcs_at_location(
                self.gc.b1WorldLayer.parse_path(loc), self.gc.all_npc_list)
            present = [self._display_name(n.name) for n in found]
        except Exception:
            present = []
        return {'type': 'scene', 'location': loc, 'present': present,
                'clock': self.gc.game_clock.now_str(), 'round': self.round_no}

    def hydrate_history(self, limit: int = 400):
        """
        从 `global_context` **重建**叙事流历史。

        什么时候需要：**读档**和**撤回**之后——那两种操作把状态整个换掉了，
        而结构化事件队列里留的还是旧内容。`global_context` 跟着状态一起换，所以用它重建。
        """
        self._history.clear()
        for line in list(self.gc.global_context.context)[-limit:]:
            ev = parse_context_line(line)
            if ev:
                self._history.append(ev)
        logger.info(f'叙事流历史已重建：{len(self._history)} 条')

    def _gc_output(self, kind: str, text: str):
        """`GameController.on_output` 的适配器：把 (kind, text) 转成事件。"""
        self._emit({'type': kind, 'text': text})

    # ===================== 只读视图（给 UI 拉状态）=====================
    #
    # 全部**不加锁**，组装前先浅拷贝。理由见模块 docstring 的「并发」一节。

    @staticmethod
    def _display_name(name: str) -> str:
        """
        名字的**首名**，与重构前 `main.py` / `game_controller.py` 的截取规则**逐字一致**：

            '白鹤（天道宗外门长老，道号）、白雨桐（本名）' → '白鹤（天道宗外门长老，道号）'

        ⚠️ 注意**不剥括号**——原来的 `n.name.split('、')[0]` 就是带括号的，
        多剥一层终端输出就对不上了。UI 想显示更短的名字，自己在前端裁。
        """
        return (name or '').split('、')[0].strip()

    @staticmethod
    def _short_name(name: str) -> str:
        """更短的名字，**只用于 UI 展示字段**，不参与任何与终端对齐的输出。"""
        return (name or '').split('、')[0].split('（')[0].strip()

    def _npc_brief(self, npc) -> dict:
        st = dict(npc.current_state or {})
        return {
            'name': npc.name,
            'short': self._short_name(npc.name),
            'display': self._display_name(npc.name),
            'location': st.get('当前所处位置', ''),
            'level': st.get('实力等级', ''),
            'identity': st.get('当前身份', ''),
            'mood': st.get('当前心情', ''),
            'physical': st.get('体力状态', ''),
            'involved': npc.name in (self.gc.event_layer.involved or []),
        }

    def scene_view(self) -> dict:
        """一屏状态：玩家 / 时钟 / 在场 NPC / 当前事件。供状态面板与 NPC 列表使用。"""
        pd = dict(self.gc.player.player_dict)          # 浅拷贝，见并发说明
        st = dict(pd.get('当前状态CurrentState') or {})
        loc = st.get('当前所处位置', '')

        present = []
        try:
            found = self.gc.b1WorldLayer.find_npcs_at_location(
                self.gc.b1WorldLayer.parse_path(loc), self.gc.all_npc_list)
            present = [self._npc_brief(n) for n in found]
        except Exception as e:
            logger.warn(f'查找在场 NPC 失败：{type(e).__name__}: {e}')

        ev = self.gc.event_layer
        return {
            'round': self.round_no,
            'session': self.session_id,
            'player': {
                'name': pd.get('玩家名称name', '玩家'),
                'location': loc,
                'status': st,                          # 含 实力等级/身份/体力状态/心情/生命…
                'inventory': dict(pd.get('持有物Inventory') or {}),
                'description': list(pd.get('描述Description') or []),
            },
            'clock': {
                'text': self.gc.game_clock.now_str(),
                'is_night': self.gc.game_clock.is_night(),
            },
            'present_npcs': present,
            'npc_total': len(self.gc.all_npc_list),
            'event': {
                'title': ev.title or '',
                'scope': ev.scope.value,
                'rounds': ev.rounds,
                'involved': list(ev.involved or []),
                'direct_target': list(ev.direct_target or []),
                'started_at': ev.started_at or '',
            },
            'can_undo': self.last_snap is not None,
        }

    def location_tree(self) -> dict:
        """
        地点树（规范化成前端好渲染的形状）+ 当前位置。

        原始节点形状有三种（见 `B1WorldLayer.normalize_node`）：老写法的字符串叶、
        带 `_属性` 的对象、只有属性的叶。这里统一成
        `{name, path, leaf, desc, attrs, children}`，前端不必猜类型。

        ⚠️ 遍历必须走 `normalize_node`——直接读原始节点的键，`_属性` 会被当成
        一个叫"_属性"的子地点渲染出来，前端就会多出一堆假节点。
        `ui_test.py` 那条 `total_nodes > 20` 正是这个漏掉的探测器。
        """
        raw = self.gc.b1WorldLayer.get_location_tree() or {}
        norm = self.gc.b1WorldLayer.normalize_node

        def walk(node, prefix: str) -> list:
            out = []
            _, _, kids = norm(node)
            for k, v in kids.items():
                path = f'{prefix}·{k}' if prefix else k
                attrs, desc, grand = norm(v)
                out.append({'name': k, 'path': path, 'leaf': not grand,
                            'desc': desc, 'attrs': attrs,
                            'children': walk(v, path) if grand else []})
            return out

        tree = walk(raw, '')
        current = self.gc.player_location
        return {
            'tree': tree,
            # 当前位置可能不在预设树里（L3 会写出自由地点名）——前端据此提示
            'current_path': current,
            'current_parts': self.gc.b1WorldLayer.parse_path(current),
            'in_tree': any(n['path'] == current for n in self._flatten(tree)),
        }

    @staticmethod
    def _flatten(nodes: list) -> list:
        out = []
        for n in nodes:
            out.append(n)
            out.extend(GameEngine._flatten(n['children']))
        return out

    def npc_detail(self, name: str) -> dict:
        """单个 NPC 的 11 张表原文。**玩家不可见**，供调试面板。"""
        npc = self.gc.all_npc_list.get_npc(name)
        if npc is None:
            return {'found': False, 'name': name}
        tables = npc.to_dict()
        return {
            'found': True,
            'name': npc.name,
            'short': self._short_name(npc.name),
            'brief': self._npc_brief(npc),
            'tables': tables,
        }

    def list_saves(self) -> list:
        return self.gc.save_manager.list_saves()

    def list_npcs(self) -> list:
        return [self._npc_brief(n) for n in self.gc.all_npc_list.npc_list]

    def contexts(self, which: str = 'global') -> dict:
        """`global` / `event` / `director` 三种上下文的原文。"""
        ctx = {'event': self.gc.event_context,
               'director': self.gc.director_context}.get(which, self.gc.global_context)
        return {'which': which, 'count': ctx.count, 'lines': list(ctx.context)}

    # ===================== 输入 / 命令 =====================

    def handle_input(self, raw: str) -> dict:
        """
        判断一条输入该怎么走，**不执行流水线**。

        :return: `{'action': ..., 'text': ..., 'move_info': ..., 'marker': ...}`

        | action        | 含义                                   |
        |---------------|----------------------------------------|
        | `empty`       | 空输入，忽略                           |
        | `break`       | 退出                                   |
        | `skip`        | 已处理（命令/撤回），跳过本轮          |
        | `need_move`   | 输入里有移动标记，**调用方先选目标**   |
        | `run`         | 正常推进一轮，用 `text` + `move_info`  |
        """
        if not (raw or '').strip():
            return {'action': 'empty'}

        s = raw.strip()

        if s == '!!':
            self.undo()
            return {'action': 'skip'}

        if s == '/debug':
            self._emit({'type': 'info',
                        'text': f'🔧 Debug：{"🟢" if dt.set_debug(not dt.DEBUG_MODE) else "🔴"}'})
            return {'action': 'skip'}

        if s == '/s1':
            self._emit({'type': 'info',
                        'text': '⚡ S1 旁路已摘除——移动耗时改由 L3 判断，它没有存在理由了'})
            return {'action': 'skip'}

        if s == '/help':
            for line in (
                '  /save <名> /load <名> /saves /quick_save /quick_load',
                '  /context [global|event|director]  /event  /npcs  /player [inv|desc]',
                '  {移动}  → 交互式选择目标位置',
                '  !!  撤回上一条     /debug 调试快照     exit',
            ):
                self._emit({'type': 'info', 'text': line})
            return {'action': 'skip'}

        cmd = self.gc.handle_command(raw)
        if cmd == 'break':
            return {'action': 'break'}
        if cmd == 'continue':
            # 读档同撤回：状态整个换掉了，叙事流历史得跟着重建
            if s.lower().startswith(('/load', '/quick_load')):
                self.hydrate_history()
            return {'action': 'skip'}

        # ---- 移动标记：这里**只检测不做导航** ----
        # 终端要让玩家在 `input()` 菜单里选，网页要让他在地点树里点。
        # 引擎不知道也不该知道是哪种，所以把选择权交回调用方。
        marker = find_move_marker(raw)
        if marker:
            return {'action': 'need_move', 'raw': raw, 'marker': marker}

        return {'action': 'run', 'text': raw, 'move_info': None}

    def resolve_move(self, raw: str, selected_path: str):
        """
        把选中的目标位置填回输入。调用方拿到目标后调这个，再 `run_round`。

        :return: `(完整输入文本, move_info)`；`selected_path` 为空时返回 `(None, None)`
        """
        if not selected_path:
            return None, None
        marker = find_move_marker(raw)
        if not marker:
            return raw, None
        return apply_move_target(raw, marker, selected_path, self.gc.player)

    def undo(self) -> bool:
        if self.last_snap is None:
            self._emit({'type': 'info', 'text': '↩ 没有可撤回的内容'})
            return False
        self.gc.restore(self.last_snap)
        # 撤回把状态整个换回去了，叙事流历史也得跟着重建——
        # 不然网页上还留着"撤回掉的那一轮"
        self.hydrate_history()
        self._emit({'type': 'info', 'text': '↩ 已撤回上一条'})
        return True

    def move_to(self, path: str):
        """直接移动（网页地点树点击）。返回换算好的输入文本与 move_info。"""
        return self.resolve_move('/move', path)

    # ===================== 主流程 =====================

    def show_scene(self):
        """N0 的场景显示，作为事件发出。"""
        self._emit(self._scene_event())

    def consume_background(self):
        """N0 消费后台产出。控制器自己会往事件流里写旁白与简讯。"""
        self.gc.consume_deferred()

    def _l1(self, player_input: str) -> dict:
        result = self.l1_layer.judge(
            event_context=self.gc.event_context,
            event_layer=self.gc.event_layer,
            all_npc_list=self.gc.all_npc_list,
            player=self.gc.player,
            director_context=self.gc.director_context,
            world_layer=self.gc.b1WorldLayer,
            player_input=player_input,
        )
        dt.dump_layer_io(self.round_no, 'L1', result)
        return result

    def _l2(self, perceptions: dict, context: list = None) -> list:
        """
        对锁定名单里的每个 NPC 并行跑一次 L2。

        **先收全部结果，再串行落盘**——并行写 `all_npc_list` 会互相踩。

        :param context: 给 NPC 的「对话上下文」。**必须不含本轮的玩家原话**——
                        该知道的人通过 `感知输入` 拿到，不该知道的人根本看不见。
                        详见下面 `_run_round_locked` 里 `ctx_before_player` 的说明。
        """
        tasks = []
        for name, perception in (perceptions or {}).items():
            npc = self.gc.all_npc_list.get_npc(name)
            if npc is None:
                logger.warn(f'L1 锁定了一个不存在的 NPC：{name}')
                continue
            tasks.append((npc, perception))

        if not tasks:
            logger.info('L2：本轮无锁定 NPC')
            return []

        logger.info(f'L2：{len(tasks)} 个 NPC 并行 → '
                    f'{[self._display_name(n.name) for n, _ in tasks]}')
        results = []
        ctx = list(context if context is not None else self.gc.global_context.context)

        # 并行层，耗时随 NPC 数增长，所以进度行带上"已完成几个"
        done = [0]

        def _progress():
            return f'{done[0]}/{len(tasks)} 个NPC'

        with Stage(f'L2 角色层（{len(tasks)} 个NPC）', detail=_progress,
                   on_event=self._emit):
            with ThreadPoolExecutor(max_workers=min(len(tasks), LAYER2_MAX_WORKERS)) as pool:
                futures = {pool.submit(self.l2_layer.respond_onstage, npc, msg, ctx): npc
                           for npc, msg in tasks}
                for fut in as_completed(futures):
                    npc = futures[fut]
                    try:
                        results.append(fut.result())
                    except Exception as e:
                        # 与原实现一致：只记日志，不往终端打字——否则会多出一行，
                        # 破坏"终端输出与重构前逐行一致"这条验收标准
                        logger.error(f'L2 [{npc.name}] 异常：{type(e).__name__}: {e}')
                    finally:
                        done[0] += 1

        # ---- 串行落盘（主观表） + 发回应事件 ----
        for r in results:
            self.gc.apply_l2_subjective(r)
            text = r.get('回应Response', '')
            if text:
                tag = self._display_name(r['name'])
                self._emit({'type': 'dialogue', 'speaker': tag, 'text': text})
                self.gc.add_context_all(f'{tag}：{text}')

        dt.dump_layer_io(self.round_no, 'L2', results)
        return results

    def run_round(self, player_input: str, move_info: dict = None) -> dict:
        """
        跑完整一轮。**全程持锁**——两个前端同时发输入时不能交错。

        :return: 本轮摘要 `{round, input, narration, dialogues, minutes, clock}`
        """
        with self._lock:
            return self._run_round_locked(player_input, move_info)

    def _run_round_locked(self, player_input: str, move_info: dict = None) -> dict:
        gc = self.gc
        narration, dialogues = [], []

        self._emit({'type': 'round_start', 'input': player_input})

        # ── 快照 ──
        self.last_snap = gc.snapshot()
        loc_before = gc.player_location
        dt.snapshot_npcs(self.round_no, 'before', gc.all_npc_list)
        dt.snapshot_scene(self.round_no, 'before', gc)

        logger.info(f'━━ 第 {self.round_no} 轮 ━━ {self.player_name}：{player_input}')

        # ⚠️ 先快照，**再**写入本轮玩家原话。
        #
        # 这份快照是给 L2 的「对话上下文」用的。为什么必须排除本轮：
        # L2 的 system prompt 要求 NPC「全部信息来源就是感知输入」，
        # 但同时又让它「承接对话上下文里最近的交流」——而对话上下文里**就有玩家原话**。
        # 于是「NPC 知不知道某件事」实际靠模型自觉，不是结构保证。
        #
        # 悄悄话是压这条线的最坏场景：只要某个 NPC 进了 `感知分发`，
        # 哪怕 L1 给它写的感知完全没提内容，它也能从上下文里读到玩家的原话。
        #
        # 摘掉本轮玩家话之后，该知道的人通过 `感知输入` 拿到（L1 已做失真），
        # 不该知道的人**结构上看不见**。上一轮的交流仍在，连贯性不受影响。
        ctx_before_player = list(gc.global_context.context)

        gc.add_context_all(f'{self.player_name}（玩家）：{player_input}')

        # ── L1 感知过滤 ──
        # （原来的 S1 旁路已摘除：它只判"移动方式→时间倍数"，而移动耗时改由 L3 判断，
        #   那条旁路就没有存在理由了。见 common/R2_time.py 的模块说明。）
        with Stage('L1 感知过滤', on_event=self._emit):
            l1_result = self._l1(player_input)

        # ── L2 角色层（并行 per NPC）──
        l2_results = self._l2(l1_result['感知分发'], ctx_before_player)

        # ── R1 地段传播（纯规则，移动落盘之前，按出发地判定）──
        with Stage('R1 地段传播', min_show=2.0, on_event=self._emit):
            prop_result = self.r1_prop.propagate(
                ground_spread=l1_result['地段传播'],
                world_layer=gc.b1WorldLayer,
                all_npc_list=gc.all_npc_list,
                experienced_names=list(l1_result['感知分发'].keys()),
                player_location=loc_before,
                scope=l1_result['scope'],
            )

        # ── L3 结算 ──
        locked_npcs = [gc.all_npc_list.get_npc(n) for n in l1_result['锁定名单']]
        locked_npcs = [n for n in locked_npcs if n is not None]

        # 结算层**没有别的记忆**——它看不到 NPC 的主观表，只看得到本轮的意图。
        # 上一轮"丹药到底给没给"只存在于旁白里，不给它就会出现前后矛盾。
        recent = list(gc.global_context.context)[-10:]
        known_locs = gc.b1WorldLayer.list_location_paths(max_depth=3)

        with Stage('L3 结算' if l2_results else 'L3 结算（独处）',
                   on_event=self._emit):
            if l2_results:
                l3_result = self.l3_layer.settle_with_npcs(
                    player=gc.player,
                    npcs=locked_npcs,
                    l2_results=l2_results,
                    player_input=player_input,
                    location=loc_before,
                    move_hint=move_info,
                    world_state=gc.b1WorldLayer.dict.get('世界状态列表', []),
                    recent_context=recent,
                    known_locations=known_locs,
                )
            else:
                l3_result = self.l3_layer.settle_solo(
                    player=gc.player,
                    player_input=player_input,
                    location=loc_before,
                    game_time=gc.game_clock.now_str(),
                    has_move=bool(move_info),
                    move_hint=move_info,
                    world_state=gc.b1WorldLayer.dict.get('世界状态列表', []),
                    recent_context=recent,
                    known_locations=known_locs,
                )
        dt.dump_layer_io(self.round_no, 'L3', l3_result)

        # ── 落盘：客观状态（唯一写入口） + 旁白 ──
        gc.apply_l3_settlement(l3_result)
        for line in l3_result['旁白']:
            narration.append(line)
            self._emit({'type': 'narration', 'text': line})
            gc.add_global_context(f'旁白：{line}')

        # ── R2：把 L3 判断的本轮耗时落到时钟上 ──
        # 移动时间不再是"段数 × 每段分钟 ÷ 方式倍数"算出来的——它在 L3 的
        # `本轮耗时分钟` 里（语义已改成"原地活动 + 赶路"）。
        # 理由见 common/R2_time.py：玩家完全可能在自由文本里直接说"我来到青木山脉"，
        # 那种情况标记路径拿不到位置差，而 L3 本来就决定新位置、正好一起判断。
        total_minutes = self.r2_time.process(
            game_clock=gc.game_clock,
            minutes=l3_result['本轮耗时分钟'],
        )

        # ── 移动落盘（L3 若已改过位置，这里兜底） ──
        if move_info and gc.player_location != move_info['to']:
            gc.player.player_dict['当前状态CurrentState']['当前所处位置'] = move_info['to']
            gc.add_context_all(
                f'{self.player_name}（移动）：从{move_info["from"]}前往{move_info["to"]}')

        # ── 快照 after + 审计 ──
        dt.snapshot_npcs(self.round_no, 'after', gc.all_npc_list)
        dt.diff_npcs(self.round_no, gc.all_npc_list)
        dt.save_perception_audit(self.round_no, l2_results, l3_result)

        # ── 心跳（后台线程，立即返回）──
        self.round_no += 1
        self.bg_mgr.try_trigger(
            game_clock=gc.game_clock,
            world_layer=gc.b1WorldLayer,
            all_npc_list=gc.all_npc_list,
            director_context=gc.director_context.context,
            world_state_list=gc.b1WorldLayer.dict.get('世界状态列表', []),
            event_context=gc.event_context.context,
            l2_results=l2_results,
            player_input=player_input,
            player=gc.player,
            propagation_result=prop_result,
        )

        self._emit({'type': 'time', 'text': gc.game_clock.now_str(),
                    'minutes': round(total_minutes, 1)})

        return {
            'round': self.round_no - 1,
            'input': player_input,
            'narration': narration,
            'dialogues': [{'speaker': self._display_name(r['name']),
                           'text': r.get('回应Response', '')}
                          for r in l2_results if r.get('回应Response')],
            'minutes': round(total_minutes, 1),
            'clock': gc.game_clock.now_str(),
            'location': gc.player_location,
        }

    # ===================== 生命周期 =====================

    def close(self):
        """退出前必须调用：存档在后台线程写盘，线程是 daemon 的，进程一退就丢。"""
        try:
            self.gc.wait_for_saves()
        finally:
            logger.info('gushi4 退出')
            log_cfg.close()


# ===================== 终端渲染器 =====================

def terminal_renderer(event: dict):
    """
    把事件还原成终端上的样子。

    **必须与重构前 `main.py` 的输出逐行一致**——这是引擎抽取那一步的验收标准。
    每个分支都标注了它对应原来的哪一行。
    """
    from common.progress import terminal_output

    t = event.get('type')

    if t == 'scene':                                    # N0_显示场景
        print(f'\n📍 {event["location"]}')
        if event['present']:
            print(f'👥 在场：{"、".join(event["present"])}')
        else:
            print('👥 （周围无人）')
        print(f'⏰ {event["clock"]}')

    elif t == 'dialogue':                               # main.py L2 打印
        print(f'\n{event["speaker"]}：{event["text"]}')

    elif t == 'time':                                   # main.py 末尾
        print(f'\n⏰ [游戏时间 {event["text"]}（+{event["minutes"]:.0f}分）]')

    elif t in ('narration', 'dim', 'info', 'error'):
        terminal_output(t, event.get('text', ''))

    # stage / round_start / round_end / background 由 Stage 自己或 sink 处理，这里不重复打印
